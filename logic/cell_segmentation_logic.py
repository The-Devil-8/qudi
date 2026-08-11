# -*- coding: utf-8 -*-
"""
Logic module for cell boundary segmentation from confocal scan data.

This module provides robust functions to analyze raw confocal .dat files,
identify the boundaries of biological cells (even under extreme NV cluster
spike noise as in Confocal3), separate individual cell instances for queueing
(e.g., ScanRegionQueue), and write filtered data back to a new .dat file.

Design Rationale & Physics
--------------------------
1. Ultra-Bright NV Spikes & Clusters:
   Confocal fluorescence scans contain NV cluster spikes ($>2\times 10^7$ counts/sec)
   which are up to 400x brighter than the auto-fluorescence of cell bodies
   ($50,000 - 200,000$ counts/sec).
   Linear smoothing or standard Otsu thresholding drags the threshold up to
   $>2,000,000$ counts/sec, causing standard algorithms to segment only cluster
   spikes while completely missing the biological cells.

2. Dynamic Range Compression & Winsorization:
   Applying a non-linear log transform $I_{\\text{log}} = \\log_{10}(1 + \\max(0, I))$
   compresses the cluster-to-cell dynamic range ratio from $200\\times$ down to $1.5\\times$.
   Percentile Winsorization (capping log-intensity at the 92nd percentile) prevents
   $2\times 10^7$ spikes from blooming into artificial mounded blobs during Gaussian smoothing.

3. Marker-Controlled Watershed Instance Decomposition:
   Extracted diffuse cell bodies are decomposed into distinct cell regions using
   distance transforms and watershed peak detection, returning bounding boxes
   ready for direct consumption by ScanRegionQueue.
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
)

try:
    from skimage.filters import threshold_otsu
    from skimage.measure import find_contours, regionprops
    from skimage.segmentation import watershed
    from skimage.feature import peak_local_max
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


class CellSegmentationLogic:
    """
    Robust cell boundary segmentation and instance decomposition pipeline.
    """

    def __init__(self):
        pass

    def parse_dat_file(self, filepath):
        """
        Parses a Qudi confocal .dat file into a 2D spatial grid.
        
        @param str filepath: Path to the .dat file
        @return tuple: (image, x_coords, y_coords, header_lines)
            image: 3D numpy array of shape (ny, nx, 4) where channel 3 is fluorescence
            x_coords: 1D array of unique x coordinates
            y_coords: 1D array of unique y coordinates
            header_lines: List of strings containing the file header
        """
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        data_start = 0
        header = []
        for i, line in enumerate(lines):
            # Check for the start of the data, which usually begins with scientific notation
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

    def segment_cells(self, image, cap_percentile=92.0, bg_kernel=51, smooth_sigma=5.0):
        """
        Detect complete biological cell boundaries within the fluorescence image,
        robustly handling extreme NV cluster spikes and faint auto-fluorescence.
        
        @param np.ndarray image: 3D array (ny, nx, 4) from parse_dat_file.
        @param float cap_percentile: Upper percentile for Winsorization spike capping.
        @param int bg_kernel: Median filter size for substrate background subtraction.
        @param float smooth_sigma: Gaussian smoothing scale for macro-structure.
        @return tuple: (mask, smoothed_image)
            mask: boolean 2D array (True inside cell, False outside)
            smoothed_image: 2D smoothed log-subtracted background-free signal.
        """
        fluor = image[:, :, 3].astype(float)
        
        # 1. Dynamic Range Compression: Log Transform
        fluor_clean = np.maximum(fluor, 0.0)
        log_fluor = np.log10(fluor_clean + 1.0)
        
        # 2. Spike Winsorization / Percentile Capping
        # Suppresses ultra-bright NV clusters (>10^7 counts) to prevent threshold skew
        p_cap = np.percentile(log_fluor, cap_percentile)
        clipped_log = np.minimum(log_fluor, p_cap)
        
        # 3. Substrate Background Estimation & Subtraction
        bg_k = bg_kernel if bg_kernel % 2 != 0 else bg_kernel + 1
        bg_log = median_filter(clipped_log, size=bg_k)
        subtracted = np.maximum(clipped_log - bg_log, 0.0)
        
        # 4. Spike Despiking & Gaussian Smoothing
        despiked = median_filter(subtracted, size=7)
        smoothed = gaussian_filter(despiked, sigma=smooth_sigma)
        
        # 5. Adaptive Thresholding
        nonzero_vals = smoothed[smoothed > 0]
        if len(nonzero_vals) > 10:
            if HAS_SKIMAGE:
                try:
                    thresh = threshold_otsu(nonzero_vals)
                except Exception:
                    thresh = np.percentile(nonzero_vals, 50)
            else:
                thresh = np.percentile(nonzero_vals, 50)
        else:
            thresh = 0.0
            
        mask = smoothed > thresh
        
        # 6. Morphological Cleanup
        mask = binary_closing(mask, iterations=3)
        mask = binary_fill_holes(mask)
        mask = binary_opening(mask, iterations=2)
        
        return mask, smoothed

    def segment_cells_with_instances(self, image, min_cell_area_um2=50.0, cap_percentile=92.0):
        """
        Segment cell boundaries and decompose diffuse mask into individual cell
        instances, producing bounding boxes ready for ScanRegionQueue.
        
        @param np.ndarray image: 3D array (ny, nx, 4) from parse_dat_file.
        @param float min_cell_area_um2: Minimum area in um^2 to accept a cell instance.
        @param float cap_percentile: Upper percentile for Winsorization spike capping.
        @return tuple: (mask, smoothed, labeled_cells, cell_boxes)
            mask: 2D boolean mask of all cell bodies
            smoothed: 2D smoothed log-subtracted image
            labeled_cells: 2D integer array of cell instance labels (1..N)
            cell_boxes: list of dicts with bounding boxes and metadata for ScanRegionQueue
        """
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2
        
        # 1. Primary cell boundary mask
        mask, smoothed = self.segment_cells(image, cap_percentile=cap_percentile)
        
        if not np.any(mask):
            return mask, smoothed, np.zeros((ny, nx), dtype=int), []
            
        # 2. Watershed Instance Separation
        if HAS_SKIMAGE:
            # Use smooth log intensity profile to find cell centers
            distance = gaussian_filter(smoothed, sigma=3.0)
            min_cell_px = max(1, int(min_cell_area_um2 / pixel_area_um2)) if pixel_area_um2 > 0 else 25
            min_dist_px = max(4, int(0.5 * np.sqrt(min_cell_px / np.pi)))
            
            coords = peak_local_max(distance, min_distance=min_dist_px, labels=mask)
            if len(coords) > 0:
                markers = np.zeros_like(mask, dtype=int)
                markers[tuple(coords.T)] = np.arange(1, len(coords) + 1)
                labeled_all = watershed(-distance, markers, mask=mask)
            else:
                labeled_all, _ = label(mask)
        else:
            labeled_all, _ = label(mask)

        # 3. Extract Cell Instances & Bounding Boxes
        cell_boxes = []
        final_labeled = np.zeros_like(labeled_all)
        next_label = 1
        
        unique_lbls = [l for l in np.unique(labeled_all) if l > 0]
        for lbl in unique_lbls:
            cell_mask = (labeled_all == lbl)
            area_px = int(cell_mask.sum())
            area_um2 = area_px * pixel_area_um2 if pixel_area_um2 > 0 else area_px
            
            if min_cell_area_um2 > 0 and area_um2 < min_cell_area_um2:
                continue
                
            rows, cols = np.where(cell_mask)
            min_r, max_r = int(rows.min()), int(rows.max())
            min_c, max_c = int(cols.min()), int(cols.max())
            
            row_ctr = float(rows.mean())
            col_ctr = float(cols.mean())
            
            # Physical coordinates from grid
            x_grid = image[:, :, 0]
            y_grid = image[:, :, 1]
            
            min_x, max_x = float(x_grid[0, min_c]), float(x_grid[0, max_c])
            min_y, max_y = float(y_grid[min_r, 0]), float(y_grid[max_r, 0])
            x_ctr = float(x_grid[0, int(round(col_ctr))])
            y_ctr = float(y_grid[int(round(row_ctr)), 0])
            
            mean_intensity = float(fluor[cell_mask].mean())
            
            final_labeled[cell_mask] = next_label
            cell_boxes.append({
                'cell_id': next_label,
                'bbox_px': (min_r, min_c, max_r, max_c),
                'bbox_um': (min_x, max_x, min_y, max_y),
                'centroid_px': (row_ctr, col_ctr),
                'centroid_um': (x_ctr, y_ctr),
                'area_px': area_px,
                'area_um2': area_um2,
                'mean_intensity': mean_intensity,
            })
            next_label += 1
            
        final_mask = (final_labeled > 0)
        return final_mask, smoothed, final_labeled, cell_boxes

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
        
        # Make outside completely dark
        masked_fluor[~mask] = 0.0 
        
        # Update the data array
        new_data = image.copy()
        new_data[:, :, 3] = masked_fluor
        
        # Save the new dat file
        base, ext = os.path.splitext(original_filepath)
        out_dat_path = f"{base}_filtered{ext}"
        
        with open(out_dat_path, 'w') as f:
            for h in header:
                f.write(h)
                
            # write the flattened data back
            flat_x = new_data[:, :, 0].flatten()
            flat_y = new_data[:, :, 1].flatten()
            flat_z = new_data[:, :, 2].flatten()
            flat_c = new_data[:, :, 3].flatten()
            
            for i in range(len(flat_x)):
                f.write(f"{flat_x[i]:.6e}\t{flat_y[i]:.6e}\t{flat_z[i]:.6e}\t{flat_c[i]:.6e}\n")
                
        return out_dat_path
