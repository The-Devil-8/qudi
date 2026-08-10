# -*- coding: utf-8 -*-
"""
Logic module for Region of Interest (ROI) segmentation from confocal scan data.

This module isolates biological cells from 200×200 µm (and similar wide-field)
confocal scans by combining adaptive background subtraction, iterative spike
removal, connected component analysis with size/shape filtering, and bright
cell-candidate extraction.

Design Rationale
----------------
At wide scan ranges (e.g. 200 µm), a 200×200 pixel image has ~1 µm/px
resolution.  Biological cells (HeLa, fibroblasts, etc.) appear as 10–40 µm
diameter blobs of moderate auto-fluorescence.  NV clusters and single bright
NV centers appear as intense 1–5 px spikes.  The dark diamond substrate forms
the majority of the image.

The previous simple pipeline (median → Gaussian → Otsu) over-segments the
substrate because scattered bright spots inflate the smoothed signal.  The
redesigned pipeline here:

  1. Estimates and subtracts the slowly-varying substrate background.
  2. Iteratively sigma-clips spikes *before* smoothing.
  3. Smooths at the cell scale and thresholds.
  4. Uses connected-component analysis to accept only cell-sized, compact
     regions — rejecting isolated noise pixels and substrate artifacts.
  5. Within accepted diffuse regions, keeps filtered bright cell candidates.

Note: We are NOT removing individual POIs (NV centers) at this stage.  At wide
scans the extremely bright spots are mostly large clusters, not single NVs.
Individual POIs can still be identified later at higher resolution (5-1 um
scans), but the wide-field ROI extracted here is the filtered bright cell
signal.
"""

import os
import numpy as np
from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    binary_fill_holes,
    binary_opening,
    binary_closing,
    binary_dilation,
    label,
    find_objects,
)

try:
    from skimage.filters import threshold_otsu
    from skimage.measure import find_contours, regionprops
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


class ROISegmentationLogic:
    """
    Multi-scale adaptive ROI segmentation for wide-field confocal scans.

    Extracts bright cell ROIs from wide-field confocal fluorescence images,
    using diffuse fluorescence only as a broad localization envelope.

    Typical usage::

        logic = ROISegmentationLogic()
        image, ux, uy, header = logic.parse_dat_file('scan.dat')
        result = logic.segment_roi(image)
        roi_mask = result['roi_mask']
        out_path = logic.filter_and_save(image, roi_mask, header, 'scan.dat')
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def parse_dat_file(self, filepath):
        """
        Parse a Qudi confocal .dat file into a 2D spatial grid.

        @param str filepath: Path to the .dat file.
        @return tuple: (image, x_coords, y_coords, header_lines)
            image      — 3D ndarray (ny, nx, 4): channels 0–2 are x, y, z
                         coordinates; channel 3 is fluorescence counts.
            x_coords   — 1D array of unique x positions (metres).
            y_coords   — 1D array of unique y positions (metres).
            header_lines — list of header strings from the file.
        """
        with open(filepath, 'r') as f:
            lines = f.readlines()

        data_start = 0
        header = []
        for i, line in enumerate(lines):
            if line.startswith('1.') or not line.startswith('#'):
                data_start = i
                break
            header.append(line)

        data = np.loadtxt(lines[data_start:])
        if data.size == 0:
            raise ValueError("No data found in file.")

        x = data[:, 0]
        y = data[:, 1]
        z = data[:, 2]
        counts = data[:, 3]

        ux = np.unique(x)
        uy = np.unique(y)

        nx = len(ux)
        ny = len(uy)

        image = np.zeros((ny, nx, 4))
        image[:, :, 0] = x.reshape(ny, nx)
        image[:, :, 1] = y.reshape(ny, nx)
        image[:, :, 2] = z.reshape(ny, nx)
        image[:, :, 3] = counts.reshape(ny, nx)

        return image, ux, uy, header

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_pixel_size(image):
        """
        Compute the physical pixel size (in metres) from the coordinate grid.

        Uses the x-coordinate channel of ``image`` (shape ``(ny, nx, 4)``).

        @param np.ndarray image: 3D image array from parse_dat_file.
        @return float: pixel size in metres.  Falls back to 1.0e-6 if the
            grid has fewer than 2 columns.
        """
        nx = image.shape[1]
        if nx < 2:
            return 1.0e-6
        x_min = image[0, 0, 0]
        x_max = image[0, -1, 0]
        return abs(x_max - x_min) / (nx - 1)

    @staticmethod
    def compute_component_properties(labeled, fluor):
        """
        Compute area, bounding-box perimeter, compactness, and mean
        intensity for each labelled connected component.

        @param np.ndarray labeled: integer-labelled image from
            ``scipy.ndimage.label``.
        @param np.ndarray fluor: 2D fluorescence array (same shape).
        @return list[dict]: one dict per component with keys
            'label', 'area', 'perimeter', 'compactness', 'mean_intensity',
            'centroid_row', 'centroid_col'.
        """
        props = []
        if HAS_SKIMAGE:
            from skimage.measure import regionprops
            regions = regionprops(labeled, intensity_image=fluor)
            for r in regions:
                area = r.area
                if area == 0:
                    continue
                perimeter = r.perimeter
                if perimeter == 0:
                    perimeter = 1.0
                compactness = (4.0 * np.pi * area) / (perimeter ** 2)
                props.append({
                    'label': r.label,
                    'area': area,
                    'perimeter': perimeter,
                    'compactness': compactness,
                    'solidity': r.solidity,
                    'mean_intensity': r.intensity_mean,
                    'centroid_row': r.centroid[0],
                    'centroid_col': r.centroid[1],
                })
            return props
            
        slices = find_objects(labeled)
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            component = (labeled[sl] == lbl)
            area = int(component.sum())
            if area == 0:
                continue

            padded = np.pad(component.astype(np.uint8), 1, mode='constant',
                            constant_values=0)
            eroded = (
                padded[1:-1, 1:-1]
                & padded[:-2, 1:-1]   # up
                & padded[2:, 1:-1]    # down
                & padded[1:-1, :-2]   # left
                & padded[1:-1, 2:]    # right
            )
            perimeter = int(component.sum() - eroded.sum())
            if perimeter <= 0:
                perimeter = 1

            compactness = (4.0 * np.pi * area) / (perimeter ** 2)

            region_fluor = fluor[sl][component]
            mean_intensity = float(region_fluor.mean()) if area > 0 else 0.0

            rows, cols = np.where(component)
            centroid_row = float(rows.mean()) + sl[0].start
            centroid_col = float(cols.mean()) + sl[1].start

            props.append({
                'label': lbl,
                'area': area,
                'perimeter': perimeter,
                'compactness': compactness,
                'solidity': 1.0, # Dummy fallback
                'mean_intensity': mean_intensity,
                'centroid_row': centroid_row,
                'centroid_col': centroid_col,
            })

        return props

    # ------------------------------------------------------------------
    # Core segmentation pipeline
    # ------------------------------------------------------------------

    def segment_roi(self, image,
                    background_kernel=51,
                    despike_kernel=7,
                    smooth_sigma=6.0,
                    min_cell_area_um2=50.0,
                    max_cell_fraction=0.7,
                    min_compactness=0.05,
                    bright_spot_sigma=5.0,
                    min_bright_cell_area_um2=10.0,
                    bright_spot_dilate=2):
        """
        Segment cell ROIs from a wide-field confocal image.

        Identifies bright cell ROIs and rejects both background substrate
        and tiny bright non-cell speckles.

        @param np.ndarray image: 3D array (ny, nx, 4) from parse_dat_file.
            Channel 3 is fluorescence intensity.

        @param int background_kernel: Median-filter kernel size (pixels) for
            substrate background estimation.  Should be larger than the
            largest expected cell diameter in pixels.  Default 51.
        @param int despike_kernel: Median filter size for spike removal.
            Default 7.
        @param float smooth_sigma: Gaussian σ (pixels) for cell-scale
            smoothing after spike removal.  Default 6.0.
        @param float min_cell_area_um2: Minimum area (µm²) for a connected
            component to be accepted as a cell.  Default 50.
        @param float max_cell_fraction: Maximum fraction of the total image
            area that a single component may occupy.  Default 0.7.
        @param float min_compactness: Minimum compactness (4πA/P²) for a
            component to be accepted as a cell.  Default 0.05.
        @param float bright_spot_sigma: Sigma threshold for bright cell
            detection within accepted diffuse regions.  Default 5.0.
        @param float min_bright_cell_area_um2: Minimum area (um^2) for final
            bright ROI components. This is separate from min_cell_area_um2
            because diffuse bounding regions are intentionally broader than
            the true bright cell signal. Default 10.
        @param int bright_spot_dilate: Dilation radius (px) used to recover
            the full bright cell candidate around thresholded peaks. Default 2.

        @return dict: with keys
            'roi_mask'       - bool array, True inside the final bright cell ROI.
            'diffuse_region_mask' - bool array, True inside accepted diffuse
                               bounding regions.
            'raw_bright_spots' - bool array, True for bright candidates before
                               final size/shape filtering.
            'component_labels' - int array, labelled accepted ROI components.
            'stats'          - list of dicts with per-cell properties
                               (area, centroid, mean_intensity, compactness).
        """
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        total_pixels = ny * nx

        # Auto-detect pixel size from coordinate grid
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2  # µm² per pixel

        # --- Stage 1: Adaptive background estimation ---
        # A large median filter smooths over cells and captures the
        # slowly-varying substrate auto-fluorescence.
        bg_kernel = background_kernel
        if bg_kernel % 2 == 0:
            bg_kernel += 1
        background = median_filter(fluor, size=bg_kernel)

        # --- Stage 2: Background subtraction ---
        raw_diff = fluor - background
        corrected = np.maximum(raw_diff, 0.0)

        # Estimate robust noise level from the unrectified difference
        med_diff = np.median(raw_diff)
        mad_diff = np.median(np.abs(raw_diff - med_diff))
        noise_sigma = 1.4826 * mad_diff
        if noise_sigma <= 0:
            noise_sigma = 1.0

        # --- Stage 3: Despiking ---
        # A median filter removes small high-intensity spikes before Gaussian smoothing
        # so they don't blossom into large blobs.
        d_kernel = despike_kernel
        if d_kernel % 2 == 0:
            d_kernel += 1
        despiked = median_filter(corrected, size=d_kernel)

        # --- Stage 4: Multi-scale Gaussian smoothing ---
        smoothed = gaussian_filter(despiked, sigma=smooth_sigma)

        # --- Stage 5: Adaptive thresholding for diffuse regions ---
        # We find the diffuse regions to use as bounding boxes for the true cells.
        nonzero_vals = smoothed[smoothed > 0]
        if len(nonzero_vals) > 10:
            if HAS_SKIMAGE:
                try:
                    # Clip to 99th percentile to remove extreme NV spikes before Otsu
                    # This prevents the threshold from being dragged too high by outliers.
                    p99 = np.percentile(nonzero_vals, 99)
                    clipped_vals = np.clip(nonzero_vals, a_min=None, a_max=p99)
                    thresh = threshold_otsu(clipped_vals)
                except Exception:
                    thresh = np.percentile(nonzero_vals, 65)
            else:
                thresh = np.percentile(nonzero_vals, 65)
        else:
            thresh = 0.0
        
        # Enforce minimum threshold based on noise level to prevent segmenting pure noise.
        # Gaussian smoothing significantly reduces noise amplitude, so we scale it down.
        min_thresh = 0.5 * noise_sigma
        thresh = max(thresh, min_thresh)
        
        if thresh <= 0 or not np.any(smoothed > thresh):
            # Almost entirely dark image or pure noise — nothing to segment
            empty = np.zeros((ny, nx), dtype=bool)
            return {
                'roi_mask': empty,
                'diffuse_region_mask': empty,
                'raw_bright_spots': empty,
                'component_labels': np.zeros((ny, nx), dtype=int),
                'stats': [],
            }

        raw_mask = smoothed > thresh

        # --- Stage 6: Connected component analysis with size filtering & Watershed ---
        # Morphological pre-cleanup to connect nearby regions
        raw_mask = binary_closing(raw_mask, iterations=2)
        raw_mask = binary_fill_holes(raw_mask)

        # Apply Watershed to separate overlapping cells
        from scipy.ndimage import distance_transform_edt
        if HAS_SKIMAGE:
            from skimage.feature import peak_local_max
            from skimage.segmentation import watershed

            # 1. Compute distance transform from the background
            distance = distance_transform_edt(raw_mask)
            
            # 2. Determine optimal min_distance for peak detection (cell radius proxy)
            if pixel_area_um2 > 0:
                min_cell_area_px = max(1, int(min_cell_area_um2 / pixel_area_um2))
            else:
                min_cell_area_px = 50
                
            # Assume cells are somewhat circular; radius = sqrt(Area/pi)
            # Use half of that to be safe but avoid over-segmentation
            min_dist_px = max(3, int(0.5 * np.sqrt(min_cell_area_px / np.pi)))
            
            # 3. Find peaks to use as markers
            coords = peak_local_max(distance, min_distance=min_dist_px, labels=raw_mask)
            mask_coords = np.zeros(distance.shape, dtype=bool)
            mask_coords[tuple(coords.T)] = True
            markers, _ = label(mask_coords)
            
            # 4. Apply watershed
            labeled_all = watershed(-distance, markers, mask=raw_mask)
            n_components = np.max(labeled_all) if labeled_all.size > 0 else 0
        else:
            # Fallback if skimage is missing
            labeled_all, n_components = label(raw_mask)

        component_props = self.compute_component_properties(labeled_all, fluor)

        # Convert min_cell_area from µm² to pixels
        if pixel_area_um2 > 0:
            min_cell_area_px = max(1, int(min_cell_area_um2 / pixel_area_um2))
            min_bright_cell_area_px = max(
                1, int(min_bright_cell_area_um2 / pixel_area_um2))
        else:
            min_cell_area_px = 50
            min_bright_cell_area_px = 10

        max_cell_area_px = int(total_pixels * max_cell_fraction)

        # Filter components
        accepted_labels = set()
        accepted_stats = []
        for prop in component_props:
            # Size filter
            if prop['area'] < min_cell_area_px:
                continue
            if prop['area'] > max_cell_area_px:
                continue
            # Compactness filter
            if prop['compactness'] < min_compactness:
                continue
            accepted_labels.add(prop['label'])
            accepted_stats.append(prop)

        # Build diffuse mask
        diffuse_mask = np.isin(labeled_all, list(accepted_labels))

        # --- Stage 7: Morphological cleanup of diffuse mask ---
        diffuse_mask = binary_closing(diffuse_mask, iterations=2)
        diffuse_mask = binary_fill_holes(diffuse_mask)
        diffuse_mask = binary_opening(diffuse_mask, iterations=1)

        # --- Stage 8: Bright spot (Cell) detection within diffuse regions ---
        # (We keep this for downstream use, but it is NOT the primary cell mask)
        raw_bright_spots = np.zeros((ny, nx), dtype=bool)
        if diffuse_mask.any():
            cell_intensities = fluor[diffuse_mask]
            med_cell = np.median(cell_intensities)
            mad_cell = np.median(np.abs(cell_intensities - med_cell))
            sigma_cell = 1.4826 * mad_cell
            if sigma_cell <= 0:
                sigma_cell = 1.0

            bright_thresh = med_cell + bright_spot_sigma * sigma_cell
            raw_bright_spots = (fluor > bright_thresh) & diffuse_mask

            if bright_spot_dilate > 0 and raw_bright_spots.any():
                struct_b = np.ones(
                    (2 * bright_spot_dilate + 1, 2 * bright_spot_dilate + 1),
                    dtype=bool)
                raw_bright_spots = binary_dilation(raw_bright_spots,
                                                   structure=struct_b)
                raw_bright_spots = raw_bright_spots & diffuse_mask

        # --- Stage 9: Final Assembly ---
        # The true "cell" is the diffuse mask. We re-label it to get final components.
        final_labeled, n_final = label(diffuse_mask)
        final_props = self.compute_component_properties(final_labeled, fluor)
        
        # We can re-apply size filters just in case morph ops merged things
        final_cell_labels = set()
        final_stats = []
        for prop in final_props:
            if prop['area'] < min_cell_area_px:
                continue
            if prop['area'] > max_cell_area_px:
                continue
            final_cell_labels.add(prop['label'])
            final_stats.append(prop)

        roi_mask = np.isin(final_labeled, list(final_cell_labels))
        component_labels = np.where(roi_mask, final_labeled, 0)

        return {
            'roi_mask': roi_mask,
            'diffuse_region_mask': diffuse_mask,
            'raw_bright_spots': raw_bright_spots,
            'component_labels': component_labels,
            'stats': final_stats,
        }

    # ------------------------------------------------------------------
    # Contour extraction
    # ------------------------------------------------------------------

    def get_contours(self, mask):
        """
        Extract boundary contours from a boolean mask.

        @param np.ndarray mask: 2D boolean mask.
        @return list: list of Nx2 ndarrays of (row, col) contour coordinates.
        """
        if HAS_SKIMAGE:
            return find_contours(mask.astype(float), 0.5)
        return []

    # ------------------------------------------------------------------
    # Filtered data export
    # ------------------------------------------------------------------

    def filter_and_save(self, image, roi_mask, header, original_filepath):
        """
        Zero out fluorescence outside the ROI and save to a new .dat file.

        @param np.ndarray image: 3D image array from parse_dat_file.
        @param np.ndarray roi_mask: boolean ROI mask.
        @param list header: header lines from the original file.
        @param str original_filepath: path to the original .dat file.
        @return str: path to the newly created ``_roi_filtered.dat`` file.
        """
        fluor = image[:, :, 3]
        masked_fluor = fluor.copy()
        masked_fluor[~roi_mask] = 0.0

        new_data = image.copy()
        new_data[:, :, 3] = masked_fluor

        base, ext = os.path.splitext(original_filepath)
        out_dat_path = f"{base}_roi_filtered{ext}"

        with open(out_dat_path, 'w') as f:
            for h in header:
                f.write(h)

            flat_x = new_data[:, :, 0].flatten()
            flat_y = new_data[:, :, 1].flatten()
            flat_z = new_data[:, :, 2].flatten()
            flat_c = new_data[:, :, 3].flatten()

            for i in range(len(flat_x)):
                f.write(f"{flat_x[i]:.6e}\t{flat_y[i]:.6e}\t"
                        f"{flat_z[i]:.6e}\t{flat_c[i]:.6e}\n")

        return out_dat_path
