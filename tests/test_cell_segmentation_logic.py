# -*- coding: utf-8 -*-
"""
Unit and comparative integration tests for CellSegmentationLogic.

Tests the target CellSegmentationLogic implementation against real confocal scan data
from Confocal2 (clean baseline control) and Confocal3 (target dataset with 3D overlapping
low-lit cells and extreme NV cluster spikes).

Visual outputs are saved to: tests/test_cell_segmentation_old/

Run with:
    python -m pytest tests/test_cell_segmentation_logic.py -v -s
"""

import os
import sys
import glob
import numpy as np
import pytest
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

# Add logic directory to python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if os.path.join(PROJECT_ROOT, 'logic') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'logic'))

from cell_segmentation_logic import CellSegmentationLogic, HAS_SKIMAGE
from roi_segmentation_logic import ROISegmentationLogic

try:
    import skimage
    from skimage.filters import threshold_otsu
    from skimage.measure import find_contours
    SKIMAGE_AVAILABLE = True
except ImportError:
    SKIMAGE_AVAILABLE = False


VISUALS_DIR = os.path.join(PROJECT_ROOT, 'tests', 'test_cell_segmentation_old')
CONFOCAL2_DIR = os.path.join(PROJECT_ROOT, 'Confocal2')
CONFOCAL3_DIR = os.path.join(PROJECT_ROOT, 'Confocal3')


@pytest.fixture(scope='module', autouse=True)
def ensure_visuals_dir():
    """Ensure visual output directory exists."""
    os.makedirs(VISUALS_DIR, exist_ok=True)
    return VISUALS_DIR


@pytest.fixture
def cell_logic():
    return CellSegmentationLogic()


@pytest.fixture
def roi_logic():
    return ROISegmentationLogic()


def create_categorical_cmap(n_colors=256):
    """Generate reproducible colormap for cell label visualization."""
    np.random.seed(42)
    colors = np.random.rand(n_colors, 4)
    colors[:, 3] = 0.8  # Translucent
    colors[0] = [0, 0, 0, 0]  # Background is transparent
    return ListedColormap(colors)


# ======================================================================
# Test Class 1: Scikit-Image Environment Verification
# ======================================================================

class TestScikitImageIntegration:
    """Verifies that scikit-image is installed in the environment and active."""

    def test_scikit_image_is_installed(self):
        """Check scikit-image is installed and importable."""
        assert SKIMAGE_AVAILABLE, "scikit-image must be installed in the environment"
        assert hasattr(skimage, '__version__'), "scikit-image version must be accessible"

    def test_scikit_image_flag_in_cell_logic(self):
        """Check CellSegmentationLogic detected scikit-image."""
        assert HAS_SKIMAGE, "CellSegmentationLogic.HAS_SKIMAGE must be True"


# ======================================================================
# Test Class 2: Synthetic Unit Tests for CellSegmentationLogic
# ======================================================================

class TestCellSegmentationLogicSynthetic:
    """Synthetic unit tests for CellSegmentationLogic methods."""

    def test_segment_cells_synthetic(self, cell_logic):
        """Test cell segmentation on synthetic cell image."""
        ny, nx = 100, 100
        image = np.zeros((ny, nx, 4))
        for i in range(nx):
            image[:, i, 0] = i * 1e-6
        for j in range(ny):
            image[j, :, 1] = j * 1e-6
            
        np.random.seed(42)
        fluor = np.random.normal(100, 10, (ny, nx))
        y_grid, x_grid = np.ogrid[:ny, :nx]
        cell_blob = ((x_grid - 50)**2 + (y_grid - 50)**2) < 20**2
        fluor[cell_blob] += 1000.0
        image[:, :, 3] = fluor

        mask, smoothed = cell_logic.segment_cells(image)

        assert mask.shape == (ny, nx)
        assert smoothed.shape == (ny, nx)
        assert mask.dtype == bool
        assert mask[50, 50], "Center of cell blob should be segmented"

    def test_segment_cells_with_instances_synthetic(self, cell_logic):
        """Test instance segmentation and bounding box extraction for ScanRegionQueue."""
        ny, nx = 100, 100
        image = np.zeros((ny, nx, 4))
        for i in range(nx):
            image[:, i, 0] = i * 1e-6
        for j in range(ny):
            image[j, :, 1] = j * 1e-6

        fluor = np.random.normal(100, 5, (ny, nx))
        y_grid, x_grid = np.ogrid[:ny, :nx]
        
        c1 = ((x_grid - 25)**2 + (y_grid - 25)**2) < 12**2
        c2 = ((x_grid - 75)**2 + (y_grid - 75)**2) < 12**2
        fluor[c1] += 2000.0
        fluor[c2] += 2000.0
        image[:, :, 3] = fluor

        mask, smoothed, labeled, cell_boxes = cell_logic.segment_cells_with_instances(image, min_cell_area_um2=10.0)

        assert mask.any()
        assert labeled.max() >= 2, "Should identify at least 2 distinct cell instances"
        assert len(cell_boxes) >= 2


# ======================================================================
# Test Class 3: Confocal3 & Confocal2 Real Data Integration Tests
# ======================================================================

class TestCellSegmentationLogicRealData:
    """
    Tests CellSegmentationLogic on real confocal data from Confocal2 and Confocal3.
    Target evaluation focus: Confocal3 (overlapping cells & extreme NV spikes).
    """

    def test_run_and_compare_all_datasets(self, cell_logic, roi_logic):
        c2_files = sorted(glob.glob(os.path.join(CONFOCAL2_DIR, '*_confocal_xy_data.dat')))[:2]
        c3_files = sorted(glob.glob(os.path.join(CONFOCAL3_DIR, '*_confocal_xy_data.dat')))

        if not c2_files or not c3_files:
            pytest.skip("Confocal2 or Confocal3 dataset files missing.")

        print("\n" + "=" * 80)
        print("REAL FLUORESCENCE DATA EVALUATION: Target Dataset Confocal3 vs Control Confocal2")
        print("=" * 80)

        for ds_name, file_list in [('Confocal2', c2_files), ('Confocal3', c3_files)]:
            for filepath in file_list:
                filename = os.path.basename(filepath)
                
                image, ux, uy, header = cell_logic.parse_dat_file(filepath)
                fluor = image[:, :, 3]
                ny, nx = fluor.shape

                # Targeted CellSegmentationLogic
                mask, smooth, labeled, cell_boxes = cell_logic.segment_cells_with_instances(image)
                contours = cell_logic.get_contours(mask)

                area_px = int(mask.sum())
                pct_area = (area_px / (ny * nx)) * 100.0
                fg_mean = float(fluor[mask].mean()) if area_px > 0 else 0.0
                bg_mean = float(fluor[~mask].mean()) if (~mask).any() else 0.0
                contrast = (fg_mean / bg_mean) if bg_mean > 0 else 0.0

                print(f"\nDataset: {ds_name} | File: {filename}")
                print(f"  Shape: {ny}x{nx} px | Mask Area: {area_px} px ({pct_area:.1f}%)")
                print(f"  Extracted 3D Cell Instances for ScanRegionQueue: {len(cell_boxes)}")
                print(f"  Mean Fluor (FG / BG): {fg_mean:.1f} c/s / {bg_mean:.1f} c/s (Contrast={contrast:.2f}x)")

                # Specific Assertions
                if ds_name == 'Confocal2':
                    assert 12.0 <= pct_area <= 22.0, \
                        f"Confocal2 control mask area must remain clean (12-22%), got {pct_area:.1f}%"
                elif ds_name == 'Confocal3':
                    assert 15.0 <= pct_area <= 30.0, \
                        f"Confocal3 target mask area must capture low-lit cells (15-30%), got {pct_area:.1f}%"
                    assert len(cell_boxes) >= 15, \
                        f"Confocal3 must extract overlapping 3D cell instances for ScanRegionQueue (>=15), got {len(cell_boxes)}"

                # Plot Visual Comparison
                fig, axes = plt.subplots(1, 3, figsize=(16, 5))
                p2, p98 = np.percentile(fluor, (2, 98))
                
                axes[0].imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
                axes[0].set_title(f"Raw Scan ({ds_name})\n{filename}", fontsize=9, fontweight='bold')
                axes[0].axis('off')
                
                axes[1].imshow(fluor, cmap='gray', vmin=p2, vmax=p98)
                for cnt in contours:
                    axes[1].plot(cnt[:, 1], cnt[:, 0], color='cyan', linewidth=1.5)
                axes[1].set_title(f"Cell Mask Boundaries ({pct_area:.1f}% Area)", fontsize=9, fontweight='bold')
                axes[1].axis('off')
                
                cmap_lbls = create_categorical_cmap()
                axes[2].imshow(labeled, cmap=cmap_lbls, interpolation='nearest')
                for box in cell_boxes:
                    min_r, min_c, max_r, max_c = box['bbox_px']
                    rect = plt.Rectangle((min_c, min_r), max_c - min_c, max_r - min_r,
                                         fill=False, edgecolor='yellow', linewidth=0.8, linestyle=':')
                    axes[2].add_patch(rect)
                axes[2].set_title(f"Extracted Cell Instances (N={len(cell_boxes)} for ScanRegionQueue)",
                                  fontsize=9, fontweight='bold')
                axes[2].axis('off')
                
                plt.tight_layout()
                out_png = os.path.join(VISUALS_DIR, f"targeted_{ds_name}_{os.path.splitext(filename)[0]}.png")
                plt.savefig(out_png, dpi=150, bbox_inches='tight')
                plt.close(fig)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
