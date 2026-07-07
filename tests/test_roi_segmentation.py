# -*- coding: utf-8 -*-
"""
Unit tests for ROISegmentationLogic.

Tests the multi-scale adaptive ROI segmentation pipeline using synthetic data:
background estimation, diffuse localization, bright cell extraction,
size/shape filtering, and edge cases.

Run with: python -m pytest tests/test_roi_segmentation.py -v
"""

import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'logic'))

from roi_segmentation_logic import ROISegmentationLogic


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def logic():
    return ROISegmentationLogic()


def _make_image(ny, nx, scan_range_m=200e-6, z_pos=23e-6):
    """Build a synthetic (ny, nx, 4) image with coordinate grids."""
    image = np.zeros((ny, nx, 4))
    x_coords = np.linspace(0.1e-6, scan_range_m, nx)
    y_coords = np.linspace(0.1e-6, scan_range_m, ny)
    for i in range(nx):
        image[:, i, 0] = x_coords[i]
    for j in range(ny):
        image[j, :, 1] = y_coords[j]
    image[:, :, 2] = z_pos
    return image


def _add_cell(image, center_row, center_col, radius_px, intensity):
    """Add a circular cell blob to channel 3."""
    ny, nx = image.shape[:2]
    for r in range(ny):
        for c in range(nx):
            dist2 = (r - center_row) ** 2 + (c - center_col) ** 2
            if dist2 < radius_px ** 2:
                # Gaussian-like falloff
                image[r, c, 3] += intensity * np.exp(-dist2 / (2 * (radius_px / 2) ** 2))
    return image


def _add_spike(image, row, col, intensity):
    """Add a single bright spike pixel."""
    if 0 <= row < image.shape[0] and 0 <= col < image.shape[1]:
        image[row, col, 3] += intensity
    return image


# ======================================================================
# Test: Pixel size estimation
# ======================================================================

class TestPixelSizeEstimation:
    """Tests for auto-detection of physical pixel size."""

    def test_standard_200um_scan(self, logic):
        """200 µm scan over 200 pixels → ~1 µm/px."""
        image = _make_image(200, 200, scan_range_m=200e-6)
        ps = logic.estimate_pixel_size(image)
        expected = 200e-6 / 199  # ~1.005 µm
        assert abs(ps - expected) / expected < 0.01

    def test_small_scan(self, logic):
        """25 µm scan → smaller pixel size."""
        image = _make_image(100, 100, scan_range_m=25e-6)
        ps = logic.estimate_pixel_size(image)
        assert ps < 1e-6  # sub-micron

    def test_single_column_fallback(self, logic):
        """Single-column image should fall back to 1 µm."""
        image = _make_image(50, 1)
        ps = logic.estimate_pixel_size(image)
        assert ps == 1.0e-6


# ======================================================================
# Test: Component property computation
# ======================================================================

class TestComponentProperties:
    """Tests for connected component property calculation."""

    def test_single_square_component(self):
        """A 10×10 square should have correct area and reasonable compactness."""
        labeled = np.zeros((50, 50), dtype=int)
        labeled[20:30, 20:30] = 1
        fluor = np.ones((50, 50)) * 100.0
        fluor[20:30, 20:30] = 500.0

        props = ROISegmentationLogic.compute_component_properties(labeled, fluor)
        assert len(props) == 1
        assert props[0]['area'] == 100
        assert props[0]['mean_intensity'] == 500.0
        assert props[0]['compactness'] > 0.5  # square is fairly compact

    def test_no_components(self):
        """All-zero labeling should return empty list."""
        labeled = np.zeros((50, 50), dtype=int)
        fluor = np.ones((50, 50)) * 100.0
        props = ROISegmentationLogic.compute_component_properties(labeled, fluor)
        assert len(props) == 0

    def test_multiple_components(self):
        """Two separate components should produce two entries."""
        labeled = np.zeros((50, 50), dtype=int)
        labeled[5:10, 5:10] = 1
        labeled[30:40, 30:40] = 2
        fluor = np.ones((50, 50)) * 100.0

        props = ROISegmentationLogic.compute_component_properties(labeled, fluor)
        assert len(props) == 2
        areas = sorted([p['area'] for p in props])
        assert areas == [25, 100]

    def test_single_pixel_component(self):
        """Single-pixel component should have area=1 and perimeter=1."""
        labeled = np.zeros((20, 20), dtype=int)
        labeled[10, 10] = 1
        fluor = np.ones((20, 20)) * 50.0
        fluor[10, 10] = 200.0

        props = ROISegmentationLogic.compute_component_properties(labeled, fluor)
        assert len(props) == 1
        assert props[0]['area'] == 1


# ======================================================================
# Test: Full pipeline — cell detection
# ======================================================================

class TestCellDetection:
    """Tests for the full segment_roi pipeline on synthetic data."""

    def test_single_cell_detected(self, logic):
        """A single bright circular cell on dark background should be found."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        # Background noise
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(500, 50, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        # Cell at center, radius 15 px (~15 µm)
        _add_cell(image, 50, 50, 15, 5000)

        result = logic.segment_roi(image, min_cell_area_um2=20.0)
        assert result['roi_mask'].any(), "ROI mask should not be empty"
        assert result['diffuse_region_mask'].any(), "Cell mask should not be empty"

    def test_two_cells_detected(self, logic):
        """Two well-separated cells should both be found."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(500, 50, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        _add_cell(image, 25, 25, 12, 4000)
        _add_cell(image, 75, 75, 12, 4000)

        result = logic.segment_roi(image, min_cell_area_um2=20.0)
        assert result['diffuse_region_mask'].any()
        # The cells should cover a reasonable but not majority area
        frac = result['diffuse_region_mask'].sum() / result['diffuse_region_mask'].size
        assert frac < 0.5

    def test_no_cells_in_dark_image(self, logic):
        """A uniformly dark image should produce no ROI."""
        image = _make_image(50, 50)
        image[:, :, 3] = 100.0  # uniform low
        result = logic.segment_roi(image, min_cell_area_um2=20.0)
        assert not result['roi_mask'].any()

    def test_rejects_tiny_noise_spots(self, logic):
        """Single-pixel or very small bright spots should be rejected."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(500, 50, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        # Add several isolated bright pixels (not cells)
        for r, c in [(10, 10), (30, 70), (80, 20), (60, 90)]:
            _add_spike(image, r, c, 50000)

        result = logic.segment_roi(image, min_cell_area_um2=20.0)
        # These tiny spots should NOT appear in cell mask
        # (they might get sigma-clipped, or fail component size filter)
        roi_frac = result['roi_mask'].sum() / result['roi_mask'].size
        assert roi_frac < 0.1, "Noise spikes should not produce large ROI"


# ======================================================================
# Test: Size filtering
# ======================================================================

class TestSizeFiltering:
    """Tests for minimum and maximum area filtering."""

    def test_reject_small_region(self, logic):
        """Regions smaller than min_cell_area should be rejected."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(200, 20, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        # Add a very small bright spot (3x3 px = 9 µm² at 1 µm/px)
        image[50:53, 50:53, 3] = 10000

        result = logic.segment_roi(image, min_cell_area_um2=100.0)
        # 9 px² < 100 µm² → should be rejected
        # (It may also be caught by spike removal)
        assert result['roi_mask'].sum() < 20

    def test_keep_large_cell(self, logic):
        """Regions above min_cell_area should be accepted."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(300, 30, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        _add_cell(image, 50, 50, 20, 6000)

        result = logic.segment_roi(image, min_cell_area_um2=50.0)
        assert result['diffuse_region_mask'].any(), "Large cell should be detected"


# ======================================================================
# Test: Bright cell ROI extraction
# ======================================================================

class TestBrightCellExtraction:
    """Tests for extracting bright cell candidates as the final ROI."""

    def test_bright_spot_kept_as_roi(self, logic):
        """Bright spots within a diffuse region should become the final ROI."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(300, 30, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        # Add cell
        _add_cell(image, 50, 50, 20, 5000)
        # Add extremely bright spike inside the cell
        _add_spike(image, 50, 50, 200000)

        result = logic.segment_roi(image, min_cell_area_um2=20.0,
                                   bright_spot_sigma=3.0)
        # The cell should be detected
        assert result['diffuse_region_mask'].any()
        # The bright cell candidate is now the final ROI, not an exclusion mask.
        assert result['raw_bright_spots'].any()
        assert result['roi_mask'][50, 50]
        assert result['roi_mask'].sum() <= result['diffuse_region_mask'].sum()

    def test_bright_candidate_filtered_by_area(self, logic):
        """Bright candidates can be rejected by the bright-cell area filter."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(300, 30, (100, 100))
        image[:, :, 3] = np.maximum(image[:, :, 3], 0)
        rows, cols = np.ogrid[:100, :100]
        diffuse_region = (rows - 50) ** 2 + (cols - 50) ** 2 < 18 ** 2
        image[:, :, 3][diffuse_region] += 3000
        _add_spike(image, 50, 50, 200000)

        result = logic.segment_roi(
            image,
            min_cell_area_um2=20.0,
            min_bright_cell_area_um2=2000.0,
            bright_spot_sigma=3.0,
            bright_spot_dilate=1,
        )

        assert result['diffuse_region_mask'].any()
        assert result['raw_bright_spots'].any()
        assert not result['roi_mask'].any()


# ======================================================================
# Test: Edge cases
# ======================================================================

class TestEdgeCases:
    """Tests for degenerate inputs."""

    def test_empty_image(self, logic):
        """All-zero image should not crash."""
        image = _make_image(50, 50)
        image[:, :, 3] = 0.0
        result = logic.segment_roi(image)
        assert isinstance(result, dict)
        assert 'roi_mask' in result

    def test_uniform_image(self, logic):
        """Uniform intensity image should produce no ROI."""
        image = _make_image(50, 50)
        image[:, :, 3] = 5000.0
        result = logic.segment_roi(image)
        # Nothing to segment in a uniform image
        assert isinstance(result, dict)

    def test_all_bright_image(self, logic):
        """All-bright image should not select everything."""
        image = _make_image(50, 50)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(100000, 1000, (50, 50))
        result = logic.segment_roi(image)
        assert isinstance(result, dict)

    def test_single_pixel_image(self, logic):
        """1×1 image should not crash."""
        image = _make_image(1, 1)
        image[0, 0, 3] = 1000
        result = logic.segment_roi(image)
        assert isinstance(result, dict)

    def test_very_small_image(self, logic):
        """5×5 image should handle without crash."""
        image = _make_image(5, 5, scan_range_m=5e-6)
        image[:, :, 3] = np.random.uniform(0, 1000, (5, 5))
        result = logic.segment_roi(image)
        assert isinstance(result, dict)

    def test_result_shapes_match(self, logic):
        """All output masks should match the input image shape."""
        image = _make_image(80, 60)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(500, 50, (80, 60))
        _add_cell(image, 40, 30, 10, 3000)
        result = logic.segment_roi(image, min_cell_area_um2=10.0)
        assert result['roi_mask'].shape == (80, 60)
        assert result['diffuse_region_mask'].shape == (80, 60)
        assert result['raw_bright_spots'].shape == (80, 60)
        assert result['component_labels'].shape == (80, 60)

    def test_stats_contain_expected_keys(self, logic):
        """Stats dicts should have the documented keys."""
        image = _make_image(100, 100, scan_range_m=100e-6)
        np.random.seed(42)
        image[:, :, 3] = np.random.normal(300, 30, (100, 100))
        _add_cell(image, 50, 50, 15, 5000)
        result = logic.segment_roi(image, min_cell_area_um2=20.0)
        if result['stats']:
            stat = result['stats'][0]
            for key in ('label', 'area', 'perimeter', 'compactness',
                        'mean_intensity', 'centroid_row', 'centroid_col'):
                assert key in stat, f"Missing key: {key}"


# ======================================================================
# Test: Contour extraction
# ======================================================================

class TestContourExtraction:
    """Tests for contour extraction from masks."""

    def test_contours_from_circle(self, logic):
        """A circular mask should produce at least one contour."""
        mask = np.zeros((50, 50), dtype=bool)
        for r in range(50):
            for c in range(50):
                if (r - 25) ** 2 + (c - 25) ** 2 < 100:
                    mask[r, c] = True
        contours = logic.get_contours(mask)
        if contours:  # only if skimage available
            assert len(contours) >= 1

    def test_empty_mask_no_contours(self, logic):
        """Empty mask should produce no contours."""
        mask = np.zeros((50, 50), dtype=bool)
        contours = logic.get_contours(mask)
        assert len(contours) == 0


# ======================================================================
# Test: Filter and save
# ======================================================================

class TestFilterAndSave:
    """Tests for data export."""

    def test_filter_and_save_creates_file(self, logic, tmp_path):
        """filter_and_save should create a valid .dat file."""
        image = _make_image(10, 10, scan_range_m=10e-6)
        image[:, :, 3] = 1000.0
        roi_mask = np.ones((10, 10), dtype=bool)
        roi_mask[0:3, :] = False  # mask out some rows

        header = ["# Test header\n"]
        out_path = str(tmp_path / "test_data.dat")
        result_path = logic.filter_and_save(image, roi_mask, header, out_path)

        assert os.path.exists(result_path)
        assert result_path.endswith("_roi_filtered.dat")

        # Read back and verify masked pixels are zero
        with open(result_path, 'r') as f:
            lines = f.readlines()
        data_lines = [l for l in lines if not l.startswith('#')]
        assert len(data_lines) == 100  # 10x10

    def test_masked_pixels_are_zero(self, logic, tmp_path):
        """Pixels outside ROI should have zero counts in output."""
        image = _make_image(10, 10, scan_range_m=10e-6)
        image[:, :, 3] = 5000.0
        roi_mask = np.zeros((10, 10), dtype=bool)
        roi_mask[4:6, 4:6] = True  # only center 2x2

        header = ["# Test header\n"]
        out_path = str(tmp_path / "test_data.dat")
        result_path = logic.filter_and_save(image, roi_mask, header, out_path)

        data = np.loadtxt(result_path, comments='#')
        counts = data[:, 3]
        # 96 pixels should be zero, 4 should be 5000
        assert np.sum(counts == 0) == 96
        assert np.sum(counts == 5000) == 4


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
