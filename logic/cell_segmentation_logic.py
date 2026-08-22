# -*- coding: utf-8 -*-
"""
Logic module for cell boundary segmentation from confocal scan data.

Upgraded to dynamically adapt between low-density sparse samples (preventing background
false positives) and highly populated dense cell clusters.
"""

import os
import numpy as np
from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    binary_fill_holes,
    binary_opening,
    binary_closing,
    label,
    grey_opening,
)

try:
    from skimage.filters import threshold_local, threshold_otsu
    from skimage.measure import find_contours
    from skimage.segmentation import watershed
    from skimage.feature import peak_local_max
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


class CellSegmentationLogic:
    """
    Robust cell boundary segmentation and instance decomposition pipeline.
    Adaptive across wide variations of sample densities.
    """

    def __init__(self):
        pass

    def parse_dat_file(self, filepath):
        """
        Parses a Qudi confocal .dat file into a 2D spatial grid.
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

    @staticmethod
    def estimate_pixel_size(image):
        """
        Estimate physical pixel size (meters per pixel) from image coordinates.
        """
        nx = image.shape[1]
        if nx < 2:
            return 1.0e-6
        x_min = image[0, 0, 0]
        x_max = image[0, -1, 0]
        return abs(x_max - x_min) / (nx - 1)

    def segment_cells(self, image, cap_percentile=92.0, bg_kernel=51, smooth_sigma=1.5):
        """
        Detect biological cell boundaries using a hybrid Global-Gated Local Adaptive technique.
        Eliminates dark-space false positives while keeping overlapping cluster separation.
        """
        fluor = image[:, :, 3].astype(float)
        
        # 1. Non-linear Log Transform
        fluor_clean = np.maximum(fluor, 0.0)
        log_fluor = np.log10(fluor_clean + 1.0)
        
        # 2. Winsorization / Percentile Capping
        p_cap = np.percentile(log_fluor, cap_percentile)
        clipped_log = np.minimum(log_fluor, p_cap)
        
        # 3. Morphological White Top-Hat background flattening
        bg_k = bg_kernel if bg_kernel % 2 != 0 else bg_kernel + 1
        bg_floor = grey_opening(clipped_log, size=(bg_k, bg_k))
        subtracted = np.maximum(clipped_log - bg_floor, 0.0)
        
        # 4. Low-scale Edge Preserving Smoothing
        smoothed = gaussian_filter(subtracted, sigma=smooth_sigma)
        
        # 5. DUAL-PATH GATING ENGINE (Fixes the sparse sample false positives)
        if HAS_SKIMAGE and smoothed.any():
            # A. Calculate a global conservative floor to distinguish true signal from noise
            try:
                # Otsu on nonzero values determines if there is any global signal contrast
                nonzero_vals = smoothed[smoothed > 0]
                t_global_gate = threshold_otsu(nonzero_vals) if len(nonzero_vals) > 20 else 0.0
            except Exception:
                t_global_gate = np.percentile(smoothed, 50)
            
            # Absolute hard constraint: must be above a baseline floor to prevent background chattering
            absolute_noise_floor = np.percentile(smoothed, 30) + 0.02
            gate_threshold = max(0.3 * t_global_gate, absolute_noise_floor)
            global_signal_gate = smoothed > gate_threshold
            
            # B. Run the local adaptive matrix
            block_size = bg_k
            local_thresh = threshold_local(smoothed, block_size=block_size, method='gaussian', offset=0.01)
            local_adaptive_mask = smoothed > local_thresh
            
            # C. Intersection: Only keep local adaptive pixels if they pass the global signal gate
            binary_mask = local_adaptive_mask & global_signal_gate
        else:
            # Robust fallback
            t_global = np.percentile(smoothed, 40)
            binary_mask = smoothed > t_global
            
        # 6. Morphological Refinement
        mask = binary_closing(binary_mask, iterations=2)
        mask = binary_fill_holes(mask)
        mask = binary_opening(mask, iterations=1)
        
        return mask, smoothed

    def segment_cells_with_instances(self, image, min_cell_area_um2=30.0, cap_percentile=92.0, bg_kernel=51, smooth_sigma=1.5):
        """
        Segment cell boundaries and decompose overlapping clusters into distinct instances.
        Protects unpopulated regions from false positive seeding.
        """
        # Execute the gated base localization
        mask, smoothed = self.segment_cells(
            image, cap_percentile=cap_percentile, bg_kernel=bg_kernel, smooth_sigma=smooth_sigma
        )
        
        if not HAS_SKIMAGE or not mask.any():
            labeled_cells, num_features = label(mask)
            cell_boxes = []
            for i in range(1, num_features + 1):
                rows, cols = np.where(labeled_cells == i)
                cell_boxes.append({
                    'label': i,
                    'bbox_px': (int(rows.min()), int(cols.min()), int(rows.max()), int(cols.max()))
                })
            return mask, smoothed, labeled_cells, cell_boxes

        # 1. Local Maximum Coordinate Detection (Cell Centers) bounded to valid masks
        # Setting min_distance=8 pixels ensures we do not over-segment irregular/elliptical bodies
        coordinates = peak_local_max(
            smoothed, 
            min_distance=8, 
            labels=mask,
            exclude_border=False
        )
        
        # 2. Setup Seed Arrays
        peaks_mask = np.zeros(smoothed.shape, dtype=bool)
        if len(coordinates) > 0:
            peaks_mask[tuple(coordinates.T)] = True
        markers, _ = label(peaks_mask)
        
        # 3. Create Inverse Gradient Basin Topography
        gradient_basin = -smoothed
        
        # 4. Execute Topographic Decompaction
        labeled_cells = watershed(gradient_basin, markers, mask=mask)
        
        # 5. Extract Individual Bounding Boxes filtering by absolute micron parameters
        pixel_size_m = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size_m * 1e6) ** 2
        
        unique_labels = np.unique(labeled_cells)
        cell_boxes = []
        
        for idx in unique_labels:
            if idx == 0:
                continue # Skip background
                
            cell_locs = (labeled_cells == idx)
            area_px = np.sum(cell_locs)
            area_um2 = area_px * pixel_area_um2
            
            # Apply micro-filtration parameters to clear sub-resolution remnants
            if area_um2 >= min_cell_area_um2:
                rows, cols = np.where(cell_locs)
                cell_boxes.append({
                    'label': int(idx),
                    'bbox_px': (int(rows.min()), int(cols.min()), int(rows.max()), int(cols.max())),
                    'area_um2': float(area_um2)
                })
                
        return mask, smoothed, labeled_cells, cell_boxes

    def get_contours(self, mask):
        """
        Extract the boundary contours from the boolean mask.
        
        @param np.ndarray mask: boolean mask of the cell boundaries
        @return list: List of contours (Nx2 numpy arrays of [row, col])
        """
        if HAS_SKIMAGE:
            return find_contours(mask.astype(float), 0.5)
        return []

    def filter_and_save(self, image, mask, header, original_filepath):
        """
        Zeroes out all fluorescence counts outside the detected cell mask, 
        and saves the filtered data to a new .dat file.
        
        @param np.ndarray image: The original 3D image array
        @param np.ndarray mask: The boolean cell mask
        @param list header: The original file header lines
        @param str original_filepath: Path to the original file
        @return str: Path to the newly created _filtered.dat file
        """
        fluor = image[:, :, 3]
        masked_fluor = fluor.copy()
        
        masked_fluor[~mask] = 0.0 
        
        new_data = image.copy()
        new_data[:, :, 3] = masked_fluor
        
        base, ext = os.path.splitext(original_filepath)
        out_dat_path = f"{base}_filtered{ext}"
        
        with open(out_dat_path, 'w') as f:
            for h in header:
                f.write(h)
                
            flat_x = new_data[:, :, 0].flatten()
            flat_y = new_data[:, :, 1].flatten()
            flat_z = new_data[:, :, 2].flatten()
            flat_c = new_data[:, :, 3].flatten()
            
            for i in range(len(flat_x)):
                f.write(f"{flat_x[i]:.6e}\t{flat_y[i]:.6e}\t{flat_c[i]:.6e}\t{flat_c[i]:.6e}\n")
                
        return out_dat_path
