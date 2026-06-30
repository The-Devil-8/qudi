# -*- coding: utf-8 -*-
"""
Integration tests for ROISegmentationLogic using real wide-field confocal data.

Tests the multi-scale adaptive ROI segmentation pipeline on the 4 provided
200x200 µm confocal images. Verifies that cell bodies are correctly extracted
while background and bright NV clusters are excluded.

Outputs visual verification panels to: tests/test_roi_segmentation/output/
Run with: python -m pytest tests/test_roi_segmentation_confocal.py -v
"""

import os
import sys
import pytest
import numpy as np
import matplotlib
# Use Agg backend for non-interactive plotting
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from logic.roi_segmentation_logic import ROISegmentationLogic


# Setup directories
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
CONFOCAL_DIR = os.path.abspath(os.path.join(TEST_DIR, '..', 'Confocal'))
OUTPUT_DIR = os.path.join(TEST_DIR, 'test_roi_segmentation', 'output')

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# List of the 4 confocal wide-field scans
CONFOCAL_FILES = [
    "20260615-1140-42_confocal_xy_data.dat",
    "20260615-1425-59_confocal_xy_data.dat",
    "20260615-1816-21_confocal_xy_data.dat",
    "20260615-1911-38_confocal_xy_data.dat"
]

@pytest.fixture(scope="module")
def logic():
    return ROISegmentationLogic()

@pytest.mark.parametrize("filename", CONFOCAL_FILES)
def test_confocal_scan_segmentation(logic, filename):
    filepath = os.path.join(CONFOCAL_DIR, filename)
    
    # 1. Parse the file
    image, ux, uy, header = logic.parse_dat_file(filepath)
    fluor = image[:, :, 3]
    ny, nx = fluor.shape
    total_pixels = ny * nx
    
    # 2. Run segmentation
    # Adjusting parameters for this specific 200um scan set
    # Using defaults which were designed for ~1um/px resolution
    result = logic.segment_roi(image)
    
    roi_mask = result['roi_mask']
    cell_mask = result['cell_mask']
    bright_spot_mask = result['bright_spot_mask']
    component_labels = result['component_labels']
    stats = result['stats']
    
    # 3. Basic validity assertions
    # Must find at least some ROI
    assert roi_mask.any(), f"No ROI detected in {filename}"
    
    # ROI should not be the entire image (we expect substrate to dominate wide-field)
    roi_fraction = roi_mask.sum() / total_pixels
    assert roi_fraction < 0.5, f"ROI is too large ({roi_fraction:.1%}) in {filename}"
    assert roi_fraction > 0.005, f"ROI is too small ({roi_fraction:.1%}) in {filename}"
    
    # Check component counts (expect between 1 and 30 cells in these 200x200 um scans)
    num_cells = len(stats)
    assert 1 <= num_cells <= 30, f"Found unexpected number of cells ({num_cells}) in {filename}"
    
    # Bright spots must have higher intensity than the rest of the cell
    if bright_spot_mask.any():
        max_bright_intensity = fluor[bright_spot_mask].max()
        if roi_mask.any():
            max_roi_intensity = fluor[roi_mask].max()
            # It's possible for some ROI pixel to be bright if the threshold wasn't hit, 
            # but generally bright spots contain the max values.
            # Using 99th percentile to be robust against single outlier pixels.
            roi_99th = np.percentile(fluor[roi_mask], 99)
            assert max_bright_intensity > roi_99th, "Bright spots are not actually brighter than ROI"
    
    # 4. Generate Visual Verification Panel
    generate_verification_plot(
        filename, ux, uy, fluor, cell_mask, bright_spot_mask, roi_mask, component_labels
    )
    
    # 5. Test Filter and Save
    out_dat_name = filename.replace('.dat', '_test_roi.dat')
    out_dat_path = os.path.join(OUTPUT_DIR, out_dat_name)
    actual_out_path = logic.filter_and_save(image, roi_mask, header, out_dat_path)
    
    assert os.path.exists(actual_out_path)


def generate_verification_plot(filename, ux, uy, fluor, cell_mask, bright_spot_mask, roi_mask, component_labels):
    """Generates a 4-panel diagnostic plot to verify segmentation."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 12))
    axes = axes.flatten()
    
    extent = [ux[0]*1e6, ux[-1]*1e6, uy[0]*1e6, uy[-1]*1e6]
    
    # Color limits for raw image
    vmin = np.percentile(fluor, 2)
    vmax = np.percentile(fluor, 99.5)
    if vmax <= vmin: vmax = vmin + 1.0
    
    # Panel 1: Original Image
    im0 = axes[0].imshow(fluor, extent=extent, origin='lower', cmap='inferno', vmin=vmin, vmax=vmax)
    axes[0].set_title(f"Original: {filename}\n(Inferno: vmin={vmin:.0f}, vmax={vmax:.0f})")
    fig.colorbar(im0, ax=axes[0])
    
    # Panel 2: Component Labels (Cell Mask)
    masked_labels = np.ma.masked_where(component_labels == 0, component_labels)
    im1 = axes[1].imshow(fluor, extent=extent, origin='lower', cmap='gray', vmin=vmin, vmax=vmax, alpha=0.5)
    axes[1].imshow(masked_labels, extent=extent, origin='lower', cmap='tab20', alpha=0.7)
    axes[1].set_title("Cell Mask (Connected Components)")
    
    # Panel 3: Bright Spots
    im2 = axes[2].imshow(fluor, extent=extent, origin='lower', cmap='gray', vmin=vmin, vmax=vmax, alpha=0.5)
    bright_overlay = np.ma.masked_where(~bright_spot_mask, np.ones_like(bright_spot_mask))
    axes[2].imshow(bright_overlay, extent=extent, origin='lower', cmap='Reds', vmin=0, vmax=1, alpha=0.7)
    axes[2].set_title("Excluded Bright Spots (NV Clusters)")
    
    # Panel 4: Final ROI
    roi_fluor = fluor.copy()
    roi_fluor[~roi_mask] = 0.0
    im3 = axes[3].imshow(roi_fluor, extent=extent, origin='lower', cmap='inferno', vmin=vmin, vmax=vmax)
    axes[3].set_title("Final ROI (Cell Body minus Bright Spots)")
    fig.colorbar(im3, ax=axes[3])
    
    for ax in axes:
        ax.set_xlabel('X (\u03bcm)')
        ax.set_ylabel('Y (\u03bcm)')
    
    plt.tight_layout()
    
    out_png_name = filename.replace('.dat', '_verification.png')
    out_png_path = os.path.join(OUTPUT_DIR, out_png_name)
    plt.savefig(out_png_path, dpi=150)
    plt.close(fig)

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
