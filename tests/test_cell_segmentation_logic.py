# -*- coding: utf-8 -*-
"""
Unit and comparative integration tests for CellSegmentationLogic.

Tests the upgraded robust CellSegmentationLogic against real confocal scan data
from Confocal2 and Confocal3 datasets (including Confocal3 Image 2 with extreme NV cluster spikes),
ensuring scikit-image is installed and used. Also performs side-by-side comparison with
ROISegmentationLogic.

Visual outputs are saved to: tests/test_cell_segmentation_old/

Run with:
    python -m pytest tests/test_cell_segmentation_logic.py -v
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

# Ensure scikit-image is imported
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

    def test_otsu_and_contours_functions_work(self):
        """Check threshold_otsu and find_contours execute cleanly."""
        dummy_img = np.random.uniform(0, 100, (50, 50))
        dummy_img[20:30, 20:30] += 200.0
        
        t = threshold_otsu(dummy_img)
        assert 0 < t < 300, "Otsu threshold should fall within data range"
        
        mask = dummy_img > t
        contours = find_contours(mask.astype(float), 0.5)
        assert len(contours) >= 1, "find_contours should find at least one boundary"


# ======================================================================
# Test Class 2: Synthetic Unit Tests for CellSegmentationLogic
# ======================================================================

class TestCellSegmentationLogicSynthetic:
    """Synthetic unit tests for CellSegmentationLogic methods."""

    def test_segment_cells_synthetic(self, cell_logic):
        """Test cell segmentation on synthetic cell image."""
        ny, nx = 100, 100
        image = np.zeros((ny, nx, 4))
        # Grid coordinates
        for i in range(nx):
            image[:, i, 0] = i * 1e-6
        for j in range(ny):
            image[j, :, 1] = j * 1e-6
            
        # Fluorescence channel with background + cell blob
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
        assert not mask[5, 5], "Background pixel should not be segmented"

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
        
        # Two distinct cell blobs
        c1 = ((x_grid - 25)**2 + (y_grid - 25)**2) < 12**2
        c2 = ((x_grid - 75)**2 + (y_grid - 75)**2) < 12**2
        fluor[c1] += 2000.0
        fluor[c2] += 2000.0
        image[:, :, 3] = fluor

        mask, smoothed, labeled, cell_boxes = cell_logic.segment_cells_with_instances(image, min_cell_area_um2=10.0)

        assert mask.any()
        assert labeled.max() >= 2, "Should identify at least 2 distinct cell instances"
        assert len(cell_boxes) >= 2
        for box in cell_boxes:
            for key in ('cell_id', 'bbox_px', 'bbox_um', 'centroid_px', 'centroid_um', 'area_px', 'area_um2'):
                assert key in box, f"Missing key {key} in bounding box metadata"

    def test_get_contours_synthetic(self, cell_logic):
        """Test contour extraction on synthetic mask."""
        mask = np.zeros((50, 50), dtype=bool)
        mask[10:40, 10:40] = True
        
        contours = cell_logic.get_contours(mask)
        assert isinstance(contours, list)
        assert len(contours) >= 1
        assert contours[0].ndim == 2
        assert contours[0].shape[1] == 2  # (row, col)

    def test_filter_and_save_synthetic(self, cell_logic, tmp_path):
        """Test file output filter_and_save method."""
        ny, nx = 20, 20
        image = np.zeros((ny, nx, 4))
        image[:, :, 3] = 500.0
        mask = np.zeros((ny, nx), dtype=bool)
        mask[5:15, 5:15] = True
        
        header = ["# Qudi confocal scan\n", "# Header line 2\n"]
        orig_file = str(tmp_path / "test_scan.dat")
        
        filtered_path = cell_logic.filter_and_save(image, mask, header, orig_file)
        
        assert os.path.exists(filtered_path)
        assert filtered_path.endswith("_filtered.dat")
        
        # Verify masked contents
        data = np.loadtxt(filtered_path, comments='#')
        counts = data[:, 3]
        assert np.sum(counts == 0.0) == 300  # 400 - 100 = 300 outside
        assert np.sum(counts == 500.0) == 100  # 100 inside mask


# ======================================================================
# Test Class 3: Real Data Evaluation & Comparison (Confocal2 & Confocal3)
# ======================================================================

class TestCellSegmentationLogicRealData:
    """
    Tests CellSegmentationLogic on real confocal data from Confocal2 and Confocal3,
    comparing performance against ROISegmentationLogic (the pipeline logic).
    Specifically validates robust handling of Confocal3 Image 2.
    """

    @staticmethod
    def _select_test_files():
        """Select 2 files each from Confocal2 and Confocal3."""
        c2_files = sorted(glob.glob(os.path.join(CONFOCAL2_DIR, '*_confocal_xy_data.dat')))
        c3_files = sorted(glob.glob(os.path.join(CONFOCAL3_DIR, '*_confocal_xy_data.dat')))

        if len(c2_files) < 2 or len(c3_files) < 2:
            pytest.skip("Confocal2 or Confocal3 data directory is missing test files.")

        return {
            'Confocal2': c2_files[:2],
            'Confocal3': c3_files[:2]
        }

    def test_run_and_compare_all_datasets(self, cell_logic, roi_logic):
        """
        Run upgraded CellSegmentationLogic and ROISegmentationLogic on 2 images of
        Confocal2 and 2 images of Confocal3. Generates side-by-side visuals.
        """
        datasets = self._select_test_files()
        
        print("\n" + "=" * 80)
        print("QUANTITATIVE SEGMENTATION COMPARISON: Upgraded CellSegmentationLogic vs ROISegmentationLogic")
        print("=" * 80)

        for ds_name, file_list in datasets.items():
            for filepath in file_list:
                filename = os.path.basename(filepath)
                
                # 1. Parse image
                image, ux, uy, header = cell_logic.parse_dat_file(filepath)
                fluor = image[:, :, 3]
                ny, nx = fluor.shape

                # 2. Run upgraded CellSegmentationLogic (with instances for ScanRegionQueue)
                old_mask, old_smooth, old_labeled, cell_boxes = cell_logic.segment_cells_with_instances(image)
                old_contours = cell_logic.get_contours(old_mask)

                # 3. Run active ROISegmentationLogic
                roi_result = roi_logic.segment_roi(image)
                new_roi_mask = roi_result['roi_mask']
                new_diffuse_mask = roi_result['diffuse_region_mask']
                new_labels = roi_result['component_labels']
                new_contours = roi_logic.get_contours(new_roi_mask)

                # 4. Quantitative Metrics
                old_area_px = int(old_mask.sum())
                new_roi_area_px = int(new_roi_mask.sum())

                old_fg_mean = float(fluor[old_mask].mean()) if old_area_px > 0 else 0.0
                old_bg_mean = float(fluor[~old_mask].mean()) if (~old_mask).any() else 0.0
                
                new_fg_mean = float(fluor[new_roi_mask].mean()) if new_roi_area_px > 0 else 0.0
                new_bg_mean = float(fluor[~new_roi_mask].mean()) if (~new_roi_mask).any() else 0.0

                old_contrast = (old_fg_mean / old_bg_mean) if old_bg_mean > 0 else 0.0
                new_contrast = (new_fg_mean / new_bg_mean) if new_bg_mean > 0 else 0.0

                print(f"\nDataset: {ds_name} | File: {filename}")
                print(f"  Image Shape         : {ny} x {nx} px")
                print(f"  [UPGRADED CellSeg  ]: Mask Area={old_area_px} px ({old_area_px/(ny*nx)*100:.1f}%), "
                      f"Instances={len(cell_boxes)}, Mean Fluor={old_fg_mean:.1f}, Contrast={old_contrast:.2f}x")
                print(f"  [PIPELINE ROISeg   ]: Mask Area={new_roi_area_px} px ({new_roi_area_px/(ny*nx)*100:.1f}%), "
                      f"Components={len(roi_result['stats'])}, Mean Fluor={new_fg_mean:.1f}, Contrast={new_contrast:.2f}x")

                # Specific Assertion for Confocal3 Image 2 (20260806-0016-25)
                if '20260806-0016-25' in filename:
                    assert old_area_px / (ny * nx) > 0.15, \
                        f"Upgraded CellSegLogic must capture faint cell bodies in Confocal3 Image 2 (>15% area), got {old_area_px/(ny*nx)*100:.1f}%"
                    assert len(cell_boxes) >= 5, \
                        f"Upgraded CellSegLogic must extract cell instances for ScanRegionQueue, got {len(cell_boxes)}"

                # General Assertions
                assert old_mask.shape == (ny, nx)
                assert new_roi_mask.shape == (ny, nx)
                assert isinstance(old_contours, list)
                assert isinstance(new_contours, list)

                # 5. Generate Visual Output
                self._plot_comparison_figure(
                    ds_name=ds_name,
                    filename=filename,
                    fluor=fluor,
                    old_mask=old_mask,
                    old_smooth=old_smooth,
                    old_contours=old_contours,
                    old_labeled=old_labeled,
                    cell_boxes=cell_boxes,
                    new_roi_mask=new_roi_mask,
                    new_contours=new_contours,
                    roi_stats=roi_result['stats'],
                )

        print("\n" + "=" * 80)
        print(f"Visual diagnostic plots saved to: {VISUALS_DIR}")
        print("=" * 80)

    @staticmethod
    def _plot_comparison_figure(ds_name, filename, fluor, old_mask, old_smooth, old_contours,
                                old_labeled, cell_boxes, new_roi_mask, new_contours, roi_stats):
        """Create and save a 4-panel comparison figure."""
        fig, axes = plt.subplots(1, 4, figsize=(22, 5.5))
        base_name = os.path.splitext(filename)[0]

        p2, p98 = np.percentile(fluor, (2, 98))
        cmap_labels = create_categorical_cmap()

        # Panel 1: Raw Fluorescence
        ax0 = axes[0]
        im0 = ax0.imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
        ax0.set_title(f"Raw Scan ({ds_name})\n{filename}", fontsize=10, fontweight='bold')
        fig.colorbar(im0, ax=ax0, fraction=0.046, pad=0.04)
        ax0.axis('off')

        # Panel 2: Upgraded CellSegmentationLogic Envelope
        ax1 = axes[1]
        ax1.imshow(old_smooth, cmap='viridis')
        for contour in old_contours:
            ax1.plot(contour[:, 1], contour[:, 0], color='cyan', linewidth=1.5)
        old_pct = (old_mask.sum() / old_mask.size) * 100
        ax1.set_title(f"Upgraded CellSegLogic Envelope\nLog-Winsorized (Area: {old_pct:.1f}%)",
                      fontsize=10, fontweight='bold')
        ax1.axis('off')

        # Panel 3: Cell Instance Segmentation for ScanRegionQueue
        ax2 = axes[2]
        ax2.imshow(fluor, cmap='gray', vmin=p2, vmax=p98)
        if old_labeled.max() > 0:
            ax2.imshow(old_labeled, cmap=cmap_labels, interpolation='nearest')
        for box in cell_boxes:
            min_r, min_c, max_r, max_c = box['bbox_px']
            rect = plt.Rectangle((min_c, min_r), max_c - min_c, max_r - min_r,
                                 fill=False, edgecolor='yellow', linewidth=1.0, linestyle=':')
            ax2.add_patch(rect)
        ax2.set_title(f"Extracted Cell Instances\n({len(cell_boxes)} regions for ScanRegionQueue)",
                      fontsize=10, fontweight='bold')
        ax2.axis('off')

        # Panel 4: Comparative Contour Overlay
        ax3 = axes[3]
        ax3.imshow(fluor, cmap='gray', vmin=p2, vmax=p98)
        
        for i, cnt in enumerate(old_contours):
            label = "Upgraded CellSeg" if i == 0 else ""
            ax3.plot(cnt[:, 1], cnt[:, 0], color='cyan', linewidth=2.0, linestyle='-', label=label)
            
        for i, cnt in enumerate(new_contours):
            label = "Pipeline ROISeg" if i == 0 else ""
            ax3.plot(cnt[:, 1], cnt[:, 0], color='red', linewidth=1.5, linestyle='--', label=label)

        ax3.set_title("Direct Contour Comparison\n(Cyan=Upgraded CellSeg, Red=Pipeline ROI)", fontsize=10, fontweight='bold')
        ax3.legend(loc='upper right', fontsize=8, framealpha=0.8)
        ax3.axis('off')

        plt.tight_layout()
        out_png = os.path.join(VISUALS_DIR, f"compare_{ds_name}_{base_name}.png")
        plt.savefig(out_png, dpi=150, bbox_inches='tight')
        plt.close(fig)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
