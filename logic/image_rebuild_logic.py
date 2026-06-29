# -*- coding: utf-8 -*-
"""
Logic module for rebuilding and visualizing confocal scan data.

This module provides functions to read a generic .dat confocal file 
(including those filtered by the CellSegmentationLogic) and generate
visual plots using matplotlib.
"""

import numpy as np
import matplotlib.pyplot as plt

class ImageRebuildLogic:
    """
    Class to handle rebuilding and plotting of confocal images from .dat files.
    """

    def __init__(self):
        pass

    def load_dat_file(self, filepath):
        """
        Parses a Qudi confocal .dat file into a 2D spatial grid.
        
        @param str filepath: Path to the .dat file
        @return tuple: (image, x_coords, y_coords)
            image: 3D numpy array of shape (ny, nx, 4) where channel 3 is fluorescence
            x_coords: 1D array of unique x coordinates
            y_coords: 1D array of unique y coordinates
        """
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        data_start = 0
        for i, line in enumerate(lines):
            # Check for the start of the data
            if line.startswith('1.') or not line.startswith('#'):
                data_start = i
                break
                
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
        
        return image, ux, uy

    def generate_visual_display(self, filepath, out_image_path, title="Confocal Scan"):
        """
        Rebuild the image from the .dat file and save a visual display.
        
        @param str filepath: Path to the .dat file to rebuild
        @param str out_image_path: Path where the resulting .png should be saved
        @param str title: Title for the generated plot
        """
        image, x_coords, y_coords = self.load_dat_file(filepath)
        fluor = image[:, :, 3]
        
        # Calculate optimal color range to match the Qudi GUI display
        # The raw min/max will wash out the image due to ultra-bright NV centers
        vmin = np.percentile(fluor, 2)
        vmax = np.percentile(fluor, 99.5)
        if vmax <= vmin:
            vmax = vmin + 1.0
            
        fig, ax = plt.subplots(figsize=(8, 6))
        
        extent = [x_coords[0]*1e6, x_coords[-1]*1e6, y_coords[0]*1e6, y_coords[-1]*1e6]
        
        im = ax.imshow(fluor, extent=extent, origin='lower', cmap='inferno', vmin=vmin, vmax=vmax)
        ax.set_title(title)
        ax.set_xlabel('X (um)')
        ax.set_ylabel('Y (um)')
        
        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label('Fluorescence (counts/s)')
        
        plt.tight_layout()
        plt.savefig(out_image_path, dpi=300)
        plt.close()
        
        return out_image_path
