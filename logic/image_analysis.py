# -*- coding: utf-8 -*-

"""
CIP (Color Image Processing) utilities for confocal fluorescence image analysis.

These functions operate on the raw fluorescence intensity arrays — the same data
that gets mapped to colors (via the Inferno LUT) for display in the GUI. They
automate the visual inspection that a human performs when scanning the color image
for bright NV center spots.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import numpy as np
from scipy.ndimage import median_filter, maximum_filter
from scipy.spatial.distance import cdist


class ConfocalImageAnalysis:
    """
    CIP (Color Image Processing) utilities for confocal fluorescence images.

    This class provides a collection of static methods that implement the
    image analysis pipeline for automated NV center detection. Each method
    corresponds to a stage in the CIP pipeline described in the documentation
    (see documentation/automation/08_cip_detection_algorithm.md).

    The methods operate on 2D NumPy arrays of fluorescence intensity values
    (counts/second) — the same data that produces the color image displayed
    in the Qudi confocal and POI Manager GUIs.
    """

    @staticmethod
    def estimate_background(image, kernel_size=15):
        """Estimate slowly-varying background fluorescence.

        Uses a large-kernel median filter. The kernel must be significantly
        larger than the expected NV spot size so that NV spots (which are
        localized bright points) do not bias the background estimate.

        In color image terms: this removes the slowly-varying "base color"
        so that NV spots stand out more clearly against a uniform background.

        @param np.ndarray image: 2D array of fluorescence intensity (counts/s)
        @param int kernel_size: median filter kernel size in pixels (must be odd)

        @return np.ndarray: estimated background image (same shape as input)
        """
        # Ensure odd kernel size for symmetric filtering
        if kernel_size % 2 == 0:
            kernel_size += 1
        return median_filter(image.astype(float), size=kernel_size)

    @staticmethod
    def subtract_background(image, background):
        """Subtract background from the fluorescence image.

        After subtraction, NV spots (which are localized bright regions)
        remain as positive values, while the background becomes ~zero.

        @param np.ndarray image: raw fluorescence image
        @param np.ndarray background: estimated background (from estimate_background)

        @return np.ndarray: background-corrected image (non-negative)
        """
        corrected = image.astype(float) - background.astype(float)
        return np.maximum(corrected, 0.0)

    @staticmethod
    def normalize_intensity(image, low_percentile=2, high_percentile=98):
        """Normalize image intensity to [0, 1] range using robust percentiles.

        This is the algorithmic equivalent of auto-adjusting the color bar
        min/max in the GUI so that NV spots appear as the brightest colors
        and the background appears dark.

        @param np.ndarray image: 2D fluorescence image
        @param float low_percentile: lower percentile for normalization (0-100)
        @param float high_percentile: upper percentile for normalization (0-100)

        @return np.ndarray: normalized image with values in [0, 1]
        """
        vmin = np.percentile(image, low_percentile)
        vmax = np.percentile(image, high_percentile)
        if vmax <= vmin:
            return np.zeros_like(image, dtype=float)
        return np.clip((image.astype(float) - vmin) / (vmax - vmin), 0.0, 1.0)

    @staticmethod
    def estimate_noise_level(image):
        """Estimate noise level using MAD (Median Absolute Deviation).

        MAD is robust against outliers (NV spots are outliers in the
        intensity distribution, since most pixels are background).
        The factor 1.4826 converts MAD to equivalent Gaussian standard
        deviation.

        This tells us the minimum intensity fluctuation that could be
        a real signal vs. just random noise in the color image.

        @param np.ndarray image: 2D fluorescence image (ideally background-corrected)

        @return float: estimated noise standard deviation (in same units as image)
        """
        median_val = np.median(image)
        mad = np.median(np.abs(image - median_val))
        sigma = 1.4826 * mad  # MAD to Gaussian sigma conversion
        return float(sigma)

    @staticmethod
    def threshold_intensity(image, threshold):
        """Create a binary mask of pixels above the detection threshold.

        Pixels above the threshold correspond to "hot colored" regions
        in the color image — potential NV center locations.

        @param np.ndarray image: 2D fluorescence image (background-corrected)
        @param float threshold: intensity threshold (counts/s or normalized)

        @return np.ndarray: boolean mask (True = above threshold)
        """
        return image > threshold

    @staticmethod
    def detect_local_maxima(image, mask, neighborhood_size):
        """Find local intensity maxima — the brightest pixel in each neighborhood.

        In color image terms: find the single "hottest colored" pixel in each
        local region of the image. These are the most likely NV center positions.

        A pixel is a local maximum if:
        1. It equals the maximum value in its neighborhood
        2. It is above the detection threshold (in the mask)

        @param np.ndarray image: 2D fluorescence image
        @param np.ndarray mask: boolean mask from threshold_intensity
        @param int neighborhood_size: size of the local neighborhood (pixels, must be odd)

        @return np.ndarray: Nx2 array of (row, col) positions of local maxima
        """
        if neighborhood_size % 2 == 0:
            neighborhood_size += 1
        local_max = maximum_filter(image, size=neighborhood_size)
        # A pixel is a local maximum if it equals the local max AND is in the mask
        is_local_max = (image == local_max) & mask
        # Also require that the pixel is not just a flat region
        # (i.e., the local max is strictly greater than at least some neighbors)
        positions = np.argwhere(is_local_max)
        return positions

    @staticmethod
    def validate_spot_shape(image, row, col, radius):
        """Check if the intensity pattern around a candidate is approximately circular.

        A real NV center produces a circular Gaussian-like intensity profile
        (the microscope's Point Spread Function). Artifacts like dust, scratches,
        or detector glitches produce asymmetric or irregular patterns.

        This validates by comparing horizontal and vertical intensity profiles
        through the candidate center.

        @param np.ndarray image: 2D fluorescence image
        @param int row: row position of the candidate
        @param int col: column position of the candidate
        @param int radius: radius of the region to check (pixels)

        @return tuple(bool, float): (is_valid, circularity_score)
            is_valid: True if the spot passes the circularity test
            circularity_score: 0.0 (very asymmetric) to 1.0 (perfectly circular)
        """
        nrows, ncols = image.shape

        # Extract horizontal and vertical profiles through the center
        r1 = max(0, row - radius)
        r2 = min(nrows, row + radius + 1)
        c1 = max(0, col - radius)
        c2 = min(ncols, col + radius + 1)

        # Horizontal profile (fixed row, varying col)
        h_profile = image[row, c1:c2].astype(float)
        # Vertical profile (varying row, fixed col)
        v_profile = image[r1:r2, col].astype(float)

        # Avoid division by zero
        h_sum = h_profile.sum()
        v_sum = v_profile.sum()
        if h_sum <= 0 or v_sum <= 0:
            return False, 0.0

        # Compare profile integrals — should be similar for circular spots
        ratio = max(h_sum, v_sum) / min(h_sum, v_sum)
        circularity = 1.0 / ratio  # 1.0 = perfectly circular

        # Allow up to 50% asymmetry
        is_valid = ratio < 1.5

        return is_valid, circularity

    @staticmethod
    def cluster_detections(positions, intensities, min_distance):
        """Merge nearby detections into single candidates.

        Multiple pixels near a single NV center may all pass the detection
        filters. This groups them and keeps only the brightest from each
        cluster.

        @param np.ndarray positions: Nx2 array of (row, col) or (x, y) positions
        @param np.ndarray intensities: N-element array of intensity values
        @param float min_distance: minimum distance between distinct candidates

        @return list: list of (position, intensity) tuples for cluster centers
        """
        if len(positions) == 0:
            return []

        positions = np.array(positions, dtype=float)
        intensities = np.array(intensities, dtype=float)

        # Sort by intensity (brightest first)
        order = np.argsort(-intensities)
        positions = positions[order]
        intensities = intensities[order]

        clustered = []
        used = set()

        for i in range(len(positions)):
            if i in used:
                continue
            # This is a cluster center (brightest in its neighborhood)
            clustered.append((positions[i].copy(), float(intensities[i])))
            # Mark all nearby detections as belonging to this cluster
            if len(positions) > 1:
                distances = cdist([positions[i]], positions)[0]
                nearby = np.where(distances < min_distance)[0]
                used.update(nearby.tolist())

        return clustered

    @staticmethod
    def refine_position_gaussian_2d(image, center_row, center_col, radius,
                                     x_coords=None, y_coords=None):
        """Refine candidate position using 2D Gaussian fit on the local intensity patch.

        This provides sub-pixel position accuracy by fitting the microscope's
        PSF model (2D Gaussian) to the fluorescence intensity data around the
        candidate.

        This is a simplified center-of-mass refinement. For full 2D Gaussian
        fitting, the caller should use FitLogic.make_twoDgaussian_fit().

        @param np.ndarray image: 2D fluorescence image
        @param int center_row: row of the candidate
        @param int center_col: column of the candidate
        @param int radius: radius of the patch to fit
        @param np.ndarray x_coords: optional 1D array of x coordinates for each column
        @param np.ndarray y_coords: optional 1D array of y coordinates for each row

        @return dict: {'row': float, 'col': float, 'x': float, 'y': float,
                       'amplitude': float, 'quality': float}
        """
        nrows, ncols = image.shape

        r1 = max(0, center_row - radius)
        r2 = min(nrows, center_row + radius + 1)
        c1 = max(0, center_col - radius)
        c2 = min(ncols, center_col + radius + 1)

        patch = image[r1:r2, c1:c2].astype(float)

        if patch.size == 0 or patch.max() <= 0:
            return {
                'row': float(center_row), 'col': float(center_col),
                'x': None, 'y': None,
                'amplitude': 0.0, 'quality': 0.0
            }

        # Subtract local background (minimum of patch)
        bg = patch.min()
        patch_bg = patch - bg

        # Center of mass refinement (weighted centroid)
        total = patch_bg.sum()
        if total <= 0:
            refined_row = float(center_row)
            refined_col = float(center_col)
        else:
            row_indices = np.arange(r1, r2)
            col_indices = np.arange(c1, c2)
            col_grid, row_grid = np.meshgrid(col_indices, row_indices)
            refined_row = float(np.sum(row_grid * patch_bg) / total)
            refined_col = float(np.sum(col_grid * patch_bg) / total)

        # Convert to physical coordinates if available
        x_refined = None
        y_refined = None
        if x_coords is not None and y_coords is not None:
            # Interpolate physical coordinates
            if 0 <= refined_col < len(x_coords):
                col_int = int(refined_col)
                col_frac = refined_col - col_int
                if col_int + 1 < len(x_coords):
                    x_refined = x_coords[col_int] * (1 - col_frac) + x_coords[col_int + 1] * col_frac
                else:
                    x_refined = x_coords[col_int]

            if 0 <= refined_row < len(y_coords):
                row_int = int(refined_row)
                row_frac = refined_row - row_int
                if row_int + 1 < len(y_coords):
                    y_refined = y_coords[row_int] * (1 - row_frac) + y_coords[row_int + 1] * row_frac
                else:
                    y_refined = y_coords[row_int]

        # Simple quality metric: peak-to-background ratio
        amplitude = float(patch.max())
        if bg > 0:
            quality = float((patch.max() - bg) / bg)
        else:
            quality = float(amplitude)

        return {
            'row': refined_row,
            'col': refined_col,
            'x': x_refined,
            'y': y_refined,
            'amplitude': amplitude,
            'quality': min(quality / 10.0, 1.0)  # Normalize to [0, 1]
        }

    @staticmethod
    def compute_intensity_contrast(image, row, col, radius):
        """Compute the contrast ratio between a spot's peak and local background.

        High contrast indicates a clear NV center; low contrast suggests
        background fluctuation or a very dim emitter.

        @param np.ndarray image: 2D fluorescence image
        @param int row: row of the spot center
        @param int col: column of the spot center
        @param int radius: radius defining "local" region

        @return float: contrast ratio (peak / background). Higher = more distinct.
        """
        nrows, ncols = image.shape
        r1 = max(0, row - radius)
        r2 = min(nrows, row + radius + 1)
        c1 = max(0, col - radius)
        c2 = min(ncols, col + radius + 1)

        patch = image[r1:r2, c1:c2].astype(float)
        peak = patch.max()

        # Background = median of an annular region around the spot
        # Use pixels on the patch border as background estimate
        if patch.shape[0] >= 3 and patch.shape[1] >= 3:
            border = np.concatenate([
                patch[0, :], patch[-1, :],      # top and bottom rows
                patch[1:-1, 0], patch[1:-1, -1]  # left and right columns (excluding corners)
            ])
            bg = np.median(border)
        else:
            bg = np.median(patch)

        if bg <= 0:
            return float(peak)  # Can't compute ratio; return absolute peak
        return float(peak / bg)

    @staticmethod
    def auto_color_range(image, low_percentile=2, high_percentile=99.5):
        """Compute optimal color bar range for NV detection contrast.

        This determines the min/max values for the color mapping so that
        NV spots appear as bright/hot colors against a dark background.

        @param np.ndarray image: 2D fluorescence image
        @param float low_percentile: percentile for color bar minimum
        @param float high_percentile: percentile for color bar maximum

        @return tuple(float, float): (vmin, vmax) for the color mapping
        """
        vmin = float(np.percentile(image, low_percentile))
        vmax = float(np.percentile(image, high_percentile))
        if vmax <= vmin:
            vmax = vmin + 1.0
        return vmin, vmax

    @staticmethod
    def compute_detection_confidence(snr, circularity, fit_quality):
        """Compute an overall detection confidence score.

        Combines multiple quality metrics into a single [0, 1] confidence value.

        @param float snr: signal-to-noise ratio (intensity / noise_sigma)
        @param float circularity: spot circularity score [0, 1]
        @param float fit_quality: Gaussian fit quality [0, 1]

        @return float: overall confidence in [0, 1]
        """
        # SNR contribution: saturates at SNR=20
        snr_score = min(1.0, max(0.0, snr / 20.0))
        # Circularity: already [0, 1]
        shape_score = max(0.0, min(1.0, circularity))
        # Fit quality: already [0, 1]
        fit_score = max(0.0, min(1.0, fit_quality))

        # Weighted combination
        confidence = 0.4 * snr_score + 0.3 * fit_score + 0.3 * shape_score
        return float(confidence)
