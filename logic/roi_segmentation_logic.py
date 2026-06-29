# -*- coding: utf-8 -*-
"""
Logic module for Region of Interest (ROI) segmentation from confocal scan data.

This module isolates the background body of a biological cell by first
finding the cell boundaries, and then explicitly identifying and removing
overly bright clusters.

Note: We are not removing POIs (NV centers) here. At wide scans (e.g. 25 micron and above), 
the extremely bright spots are mostly large clusters, not individual NV centers. 
Individual POIs will be identified later at higher resolution (5 to 1 micron) 
inside the mid-intensity regions (the ROI) that we extract here.
"""

import os
import numpy as np
from scipy.ndimage import gaussian_filter, median_filter, binary_fill_holes, binary_opening, binary_closing
try:
    from skimage.filters import threshold_otsu
    from skimage.measure import find_contours
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

class ROISegmentationLogic:
    """
    Class to handle the ROI segmentation pipeline (Cell body MINUS bright clusters).
    """

    def __init__(self):
        pass

    def parse_dat_file(self, filepath):
        """
        Parses a Qudi confocal .dat file into a 2D spatial grid.
        
        @param str filepath: Path to the .dat file
        @return tuple: (image, x_coords, y_coords, header_lines)
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

    def segment_roi(self, image):
        """
        Detect the ROI (Region of Interest) by isolating the cell body and removing overly bright clusters.
        
        @param np.ndarray image: 3D array where image[:,:,3] is the fluorescence intensity.
        @return tuple: (roi_mask, cell_mask, bright_cluster_mask)
            roi_mask: boolean array (True inside cell AND outside bright clusters)
            cell_mask: boolean array representing the entire cell body
            bright_cluster_mask: boolean array representing the bright clusters
        """
        fluor = image[:, :, 3]
        
        # 1. Cell Boundary Detection
        despiked = median_filter(fluor, size=7)
        smoothed = gaussian_filter(despiked, sigma=5)
        
        if HAS_SKIMAGE:
            try:
                thresh = threshold_otsu(smoothed)
            except Exception:
                thresh = np.percentile(smoothed, 70)
        else:
            thresh = np.percentile(smoothed, 70)
            
        cell_mask = smoothed > thresh
        
        cell_mask = binary_closing(cell_mask, iterations=3)
        cell_mask = binary_fill_holes(cell_mask)
        cell_mask = binary_opening(cell_mask, iterations=2)
        
        # 2. Bright Cluster Detection
        # The difference between raw fluor and despiked highlights the high-frequency spikes.
        spikes = fluor - despiked
        
        # Determine statistical threshold for bright clusters within the cell
        spikes_in_cell = spikes[cell_mask]
        if len(spikes_in_cell) > 0:
            median_val = np.median(spikes_in_cell)
            mad = np.median(np.abs(spikes_in_cell - median_val))
            sigma = 1.4826 * mad
            if sigma <= 0:
                sigma = 1.0
            
            # Threshold: Median + 10 sigma (to robustly catch extreme bright clusters, independent of outliers)
            cluster_thresh = median_val + 10 * sigma
        else:
            cluster_thresh = 0
            
        bright_cluster_mask = spikes > cluster_thresh
        
        # 3. Final ROI Construction (Mid-intensity regions inside the cell)
        roi_mask = cell_mask & (~bright_cluster_mask)
        
        return roi_mask, cell_mask, bright_cluster_mask

    def get_contours(self, mask):
        """
        Extract the boundary contours from the boolean mask.
        
        @param np.ndarray mask: boolean mask
        @return list: List of contours (Nx2 numpy arrays of [row, col])
        """
        if HAS_SKIMAGE:
            return find_contours(mask, 0.5)
        return []

    def filter_and_save(self, image, roi_mask, header, original_filepath):
        """
        Zeroes out all fluorescence counts outside the detected ROI mask, 
        and saves the filtered data to a new .dat file.
        
        @param np.ndarray image: The original 3D image array
        @param np.ndarray roi_mask: The boolean ROI mask
        @param list header: The original file header lines
        @param str original_filepath: Path to the original file
        @return str: Path to the newly created _roi_filtered.dat file
        """
        fluor = image[:, :, 3]
        masked_fluor = fluor.copy()
        
        # Make everything outside ROI completely dark
        masked_fluor[~roi_mask] = 0.0 
        
        # Update the data array
        new_data = image.copy()
        new_data[:, :, 3] = masked_fluor
        
        # Save the new dat file
        base, ext = os.path.splitext(original_filepath)
        out_dat_path = f"{base}_roi_filtered{ext}"
        
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
