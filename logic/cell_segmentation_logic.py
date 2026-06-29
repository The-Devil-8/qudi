# -*- coding: utf-8 -*-
"""
Logic module for cell boundary segmentation from confocal scan data.

This module provides the necessary functions to analyze raw confocal .dat files,
identify the boundaries of biological cells (or other macroscopic structures), 
mask the regions outside these boundaries, and write the filtered data back to a new .dat file.
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

class CellSegmentationLogic:
    """
    Class to handle the cell boundary segmentation pipeline.
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

    def segment_cells(self, image):
        """
        Detect the cell boundaries within the fluorescence image.
        
        This uses median filtering to remove high-intensity spikes (NV centers),
        followed by heavy Gaussian blurring to extract the macro-structure of the cell's
        auto-fluorescence. A threshold is then applied to generate a binary mask.
        
        @param np.ndarray image: 3D array where image[:,:,3] is the fluorescence intensity.
        @return tuple: (mask, smoothed_image)
            mask: boolean array (True inside cell, False outside)
            smoothed_image: the blurred intensity image used for thresholding
        """
        fluor = image[:, :, 3]
        
        # 1. Remove spikes (NVs) using a median filter
        despiked = median_filter(fluor, size=7)
        
        # 2. Smooth to capture the macro "cell" shape
        smoothed = gaussian_filter(despiked, sigma=5)
        
        # 3. Threshold to find the cell boundaries
        if HAS_SKIMAGE:
            try:
                thresh = threshold_otsu(smoothed)
            except Exception:
                thresh = np.percentile(smoothed, 70)
        else:
            thresh = np.percentile(smoothed, 70)
            
        mask = smoothed > thresh
        
        # 4. Clean up mask using morphological operations
        mask = binary_closing(mask, iterations=3)
        mask = binary_fill_holes(mask)
        mask = binary_opening(mask, iterations=2)
        
        return mask, smoothed

    def get_contours(self, mask):
        """
        Extract the boundary contours from the boolean mask.
        
        @param np.ndarray mask: boolean mask of the cell boundaries
        @return list: List of contours (Nx2 numpy arrays of [row, col])
        """
        if HAS_SKIMAGE:
            return find_contours(mask, 0.5)
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
