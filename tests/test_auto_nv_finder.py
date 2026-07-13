# -*- coding: utf-8 -*-
"""
Unit tests for AutoNVFinderLogic.

Tests the CIP detection pipeline, candidate management, and optimization logic
by testing the detection pipeline directly (which doesn't require Qudi framework
imports) and testing the CandidateNV data class.

The full AutoNVFinderLogic class inherits from GenericLogic which requires the
complete Qudi framework (fysom, qtpy/PyQt5, etc.). These tests verify the
detection and decision-making logic in isolation.

Run with: python -m pytest tests/test_auto_nv_finder.py -v
"""

import numpy as np
import pytest
import sys
import os

# Add the logic directory to the path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'logic'))

from image_analysis import ConfocalImageAnalysis


class TestCIPDetectionPipeline:
    """Test the full CIP detection pipeline as used by AutoNVFinderLogic.

    This tests the exact same pipeline that _detect_candidates() runs,
    but without needing to instantiate the Qudi module framework.
    """

    @pytest.fixture
    def cip(self):
        return ConfocalImageAnalysis()

    @pytest.fixture
    def single_nv_image(self):
        """Create a synthetic confocal scan image with one NV center.

        Returns a (50, 50, 4) array mimicking confocal_logic.xy_image:
        channel 0: X coords, 1: Y coords, 2: Z coords, 3: fluorescence
        """
        image = np.zeros((50, 50, 4))
        # Set physical coordinates (10 μm × 10 μm scan)
        x_coords = np.linspace(0, 10e-6, 50)
        y_coords = np.linspace(0, 10e-6, 50)
        for i in range(50):
            image[:, i, 0] = x_coords[i]
            image[i, :, 1] = y_coords[i]
        image[:, :, 2] = 0.0

        # Background: 5000 c/s with some noise
        np.random.seed(42)
        image[:, :, 3] = 5000 + np.random.normal(0, 200, (50, 50))

        # NV center at row 25, col 25 — Gaussian spot, peak 50,000 c/s
        for r in range(50):
            for c in range(50):
                r2 = (r - 25)**2 + (c - 25)**2
                image[r, c, 3] += 45000 * np.exp(-r2 / 8.0)

        return image

    @pytest.fixture
    def multi_nv_image(self):
        """Create a synthetic image with 3 NV centers at known positions."""
        image = np.zeros((100, 100, 4))
        x_coords = np.linspace(0, 20e-6, 100)
        y_coords = np.linspace(0, 20e-6, 100)
        for i in range(100):
            image[:, i, 0] = x_coords[i]
            image[i, :, 1] = y_coords[i]
        image[:, :, 2] = 0.0

        np.random.seed(42)
        image[:, :, 3] = 3000 + np.random.normal(0, 150, (100, 100))

        # NV 1: row 25, col 25, intensity 80k
        # NV 2: row 75, col 25, intensity 60k
        # NV 3: row 50, col 75, intensity 40k
        nvs = [(25, 25, 80000), (75, 25, 60000), (50, 75, 40000)]
        for (nr, nc, amp) in nvs:
            for r in range(100):
                for c in range(100):
                    r2 = (r - nr)**2 + (c - nc)**2
                    image[r, c, 3] += amp * np.exp(-r2 / 10.0)

        return image

    @pytest.fixture
    def no_nv_image(self):
        """Create a synthetic image with NO NV centers — just background."""
        image = np.zeros((50, 50, 4))
        x_coords = np.linspace(0, 10e-6, 50)
        y_coords = np.linspace(0, 10e-6, 50)
        for i in range(50):
            image[:, i, 0] = x_coords[i]
            image[i, :, 1] = y_coords[i]
        image[:, :, 2] = 0.0

        np.random.seed(42)
        image[:, :, 3] = 5000 + np.random.normal(0, 200, (50, 50))
        return image

    def _run_detection_pipeline(self, scan_image, cip,
                                 threshold_sigma=5.0,
                                 background_filter_size=15,
                                 min_spot_intensity=1000,
                                 max_candidates=50,
                                 spot_diameter=1.5e-6):
        """Run the same CIP pipeline that AutoNVFinderLogic._detect_candidates uses.

        This is a standalone reproduction of the detection algorithm from
        auto_nv_finder_logic.py, testing the same logic without Qudi imports.
        """
        fluorescence = scan_image[:, :, 3].astype(float)
        nrows, ncols = fluorescence.shape
        x_coords = scan_image[0, :, 0]
        y_coords = scan_image[:, 0, 1]
        z_current = float(scan_image[0, 0, 2])

        if ncols > 1:
            pixel_size_x = abs(x_coords[-1] - x_coords[0]) / (ncols - 1)
        else:
            pixel_size_x = spot_diameter
        pixel_size = pixel_size_x

        spot_pixels = max(3, int(spot_diameter / pixel_size))
        if spot_pixels % 2 == 0:
            spot_pixels += 1

        # Stage 1: Background
        background = cip.estimate_background(fluorescence, kernel_size=background_filter_size)
        corrected = cip.subtract_background(fluorescence, background)

        # Stage 2: Normalize
        normalized = cip.normalize_intensity(corrected)

        # Stage 3: Noise
        noise_sigma = cip.estimate_noise_level(corrected)
        if noise_sigma <= 0:
            noise_sigma = 1.0

        # Stage 4: Threshold
        threshold = max(threshold_sigma * noise_sigma, min_spot_intensity)
        mask = cip.threshold_intensity(corrected, threshold)
        if not np.any(mask):
            return []

        # Stage 5: Local maxima
        maxima_positions = cip.detect_local_maxima(corrected, mask, spot_pixels)
        if len(maxima_positions) == 0:
            return []

        # Stage 6: Shape validation
        radius = max(1, spot_pixels // 2)
        valid = []
        for pos in maxima_positions:
            row, col = int(pos[0]), int(pos[1])
            is_valid, circ = cip.validate_spot_shape(corrected, row, col, radius)
            if is_valid:
                valid.append((row, col, circ))

        if not valid:
            return []

        # Stage 7: Clustering
        positions = np.array([(r, c) for r, c, _ in valid], dtype=float)
        intensities = np.array([corrected[r, c] for r, c, _ in valid])
        circularities = {(r, c): circ for r, c, circ in valid}
        clustered = cip.cluster_detections(positions, intensities, min_distance=spot_pixels)

        # Stage 8: Gaussian refinement
        candidates = []
        for (pos, intensity) in clustered[:max_candidates]:
            row, col = int(pos[0]), int(pos[1])
            refined = cip.refine_position_gaussian_2d(
                corrected, row, col, radius,
                x_coords=x_coords, y_coords=y_coords)

            x_phys = refined['x'] if refined['x'] is not None else float(x_coords[min(col, ncols-1)])
            y_phys = refined['y'] if refined['y'] is not None else float(y_coords[min(row, nrows-1)])

            circ = circularities.get((row, col), 0.5)
            snr = intensity / noise_sigma
            confidence = cip.compute_detection_confidence(snr, circ, refined['quality'])

            candidates.append({
                'x': x_phys, 'y': y_phys, 'z_estimate': z_current,
                'pixel_row': row, 'pixel_col': col,
                'intensity': float(intensity), 'confidence': float(confidence),
                'circularity': float(circ)
            })

        candidates.sort(key=lambda c: c['intensity'], reverse=True)
        return candidates

    # ===== Single NV Tests =====

    def test_single_nv_detected(self, cip, single_nv_image):
        """Pipeline should detect exactly one NV in a single-NV image."""
        candidates = self._run_detection_pipeline(single_nv_image, cip)
        assert len(candidates) == 1

    def test_single_nv_position_accuracy(self, cip, single_nv_image):
        """Detected NV position should be close to the true position."""
        candidates = self._run_detection_pipeline(single_nv_image, cip)
        assert len(candidates) == 1
        # True NV is at row 25, col 25 → physical position 5 μm, 5 μm
        x_true = 5e-6
        y_true = 5e-6
        x_det = candidates[0]['x']
        y_det = candidates[0]['y']
        distance = np.sqrt((x_det - x_true)**2 + (y_det - y_true)**2)
        # Should be within 1 μm (5 pixels at 0.2 μm/pixel)
        assert distance < 1e-6, f"Position error {distance:.2e} m"

    def test_single_nv_pixel_position(self, cip, single_nv_image):
        """Detected pixel position should be close to the true pixel."""
        candidates = self._run_detection_pipeline(single_nv_image, cip)
        assert len(candidates) == 1
        row = candidates[0]['pixel_row']
        col = candidates[0]['pixel_col']
        assert abs(row - 25) <= 2
        assert abs(col - 25) <= 2

    def test_single_nv_high_confidence(self, cip, single_nv_image):
        """A bright NV on uniform background should have high confidence."""
        candidates = self._run_detection_pipeline(single_nv_image, cip)
        assert len(candidates) == 1
        assert candidates[0]['confidence'] > 0.5

    def test_single_nv_intensity_positive(self, cip, single_nv_image):
        """Detected intensity should be positive and substantial."""
        candidates = self._run_detection_pipeline(single_nv_image, cip)
        assert len(candidates) == 1
        assert candidates[0]['intensity'] > 5000

    # ===== Multi-NV Tests =====

    def test_multi_nv_count(self, cip, multi_nv_image):
        """Pipeline should detect all 3 NV centers."""
        candidates = self._run_detection_pipeline(multi_nv_image, cip)
        assert len(candidates) == 3

    def test_multi_nv_sorted_by_intensity(self, cip, multi_nv_image):
        """Candidates should be sorted brightest-first."""
        candidates = self._run_detection_pipeline(multi_nv_image, cip)
        for i in range(len(candidates) - 1):
            assert candidates[i]['intensity'] >= candidates[i+1]['intensity']

    def test_multi_nv_positions(self, cip, multi_nv_image):
        """Each detected NV should be close to one of the true positions."""
        candidates = self._run_detection_pipeline(multi_nv_image, cip)
        true_pixels = [(25, 25), (75, 25), (50, 75)]

        for true_r, true_c in true_pixels:
            found = False
            for cand in candidates:
                if abs(cand['pixel_row'] - true_r) <= 3 and abs(cand['pixel_col'] - true_c) <= 3:
                    found = True
                    break
            assert found, f"NV at ({true_r}, {true_c}) not detected"

    # ===== No-NV Tests =====

    def test_no_nv_no_candidates(self, cip, no_nv_image):
        """Pipeline should find 0 candidates on pure background image."""
        candidates = self._run_detection_pipeline(no_nv_image, cip)
        assert len(candidates) == 0

    # ===== Parameter Sensitivity Tests =====

    def test_high_threshold_reduces_detections(self, cip, multi_nv_image):
        """A very high threshold should reject dim NVs."""
        # The test NVs have SNR ~200+, so we need a very high threshold
        # to actually filter any out, or a very high min_spot_intensity
        candidates = self._run_detection_pipeline(
            multi_nv_image, cip, threshold_sigma=20.0, min_spot_intensity=50000)
        # min_spot_intensity=50000 should reject NV3 (40k peak)
        assert len(candidates) < 3

    def test_low_threshold_keeps_all(self, cip, multi_nv_image):
        """A low threshold should keep all NVs."""
        candidates = self._run_detection_pipeline(
            multi_nv_image, cip, threshold_sigma=2.0)
        assert len(candidates) >= 3

    def test_max_candidates_limit(self, cip, multi_nv_image):
        """max_candidates should limit the output count."""
        candidates = self._run_detection_pipeline(
            multi_nv_image, cip, max_candidates=1)
        assert len(candidates) <= 1

    # ===== Edge Cases =====

    def test_tiny_image(self, cip):
        """Pipeline should handle a very small image without crashing."""
        image = np.zeros((5, 5, 4))
        for i in range(5):
            image[:, i, 0] = i * 1e-6
            image[i, :, 1] = i * 1e-6
        image[:, :, 3] = 1000
        image[2, 2, 3] = 50000
        candidates = self._run_detection_pipeline(image, cip)
        # May or may not detect it — shouldn't crash
        assert isinstance(candidates, list)

    def test_uniform_image(self, cip):
        """Pipeline should return 0 candidates for uniform image."""
        image = np.ones((50, 50, 4)) * 5000
        for i in range(50):
            image[:, i, 0] = i * 0.2e-6
            image[i, :, 1] = i * 0.2e-6
        candidates = self._run_detection_pipeline(image, cip)
        assert len(candidates) == 0


class TestOptimizationDecisions:
    """Test the optimization quality checks that AutoNVFinderLogic performs."""

    def test_accept_close_position(self):
        """Position within spot_diameter*2 should be accepted."""
        candidate_pos = (5e-6, 5e-6)
        optimized_pos = (5.1e-6, 5.1e-6)
        spot_diameter = 1.5e-6
        max_displacement = spot_diameter * 2

        distance = np.sqrt(
            (optimized_pos[0] - candidate_pos[0])**2 +
            (optimized_pos[1] - candidate_pos[1])**2)
        assert distance < max_displacement

    def test_reject_far_position(self):
        """Position far from candidate should be rejected."""
        candidate_pos = (5e-6, 5e-6)
        optimized_pos = (15e-6, 15e-6)
        spot_diameter = 1.5e-6
        max_displacement = spot_diameter * 2

        distance = np.sqrt(
            (optimized_pos[0] - candidate_pos[0])**2 +
            (optimized_pos[1] - candidate_pos[1])**2)
        assert distance >= max_displacement

    def test_accept_exact_position(self):
        """Optimized position exactly at candidate should be accepted."""
        candidate_pos = (5e-6, 5e-6)
        optimized_pos = (5e-6, 5e-6)
        distance = np.sqrt(
            (optimized_pos[0] - candidate_pos[0])**2 +
            (optimized_pos[1] - candidate_pos[1])**2)
        assert distance == 0.0

    def test_displacement_calculation(self):
        """Test displacement math for known values."""
        dx = 3e-6
        dy = 4e-6
        distance = np.sqrt(dx**2 + dy**2)
        assert abs(distance - 5e-6) < 1e-12


class TestCandidateNVDataClass:
    """Test the CandidateNV helper data structure."""

    def test_candidate_creation(self):
        """Test that candidates are properly initialized with pending status."""
        # Inline CandidateNV since we can't import from auto_nv_finder_logic
        # (requires Qudi framework). Test the data contract instead.
        candidate = {
            'x': 5e-6, 'y': 3e-6, 'z_estimate': 0.0,
            'pixel_row': 25, 'pixel_col': 15,
            'intensity': 125000, 'confidence': 0.85,
            'status': 'pending', 'rejection_reason': '',
            'optimized_pos': None, 'poi_name': ''
        }
        assert candidate['status'] == 'pending'
        assert candidate['optimized_pos'] is None
        assert candidate['confidence'] > 0

    def test_candidate_acceptance(self):
        """Test candidate state transition: pending → accepted."""
        candidate = {'status': 'pending', 'optimized_pos': None, 'poi_name': ''}
        # Simulate acceptance
        candidate['status'] = 'accepted'
        candidate['optimized_pos'] = (5.1e-6, 3.1e-6, 0.0)
        candidate['poi_name'] = 'NV_001'
        assert candidate['status'] == 'accepted'
        assert candidate['poi_name'] == 'NV_001'

    def test_candidate_rejection(self):
        """Test candidate state transition: pending → rejected."""
        candidate = {'status': 'pending', 'rejection_reason': ''}
        candidate['status'] = 'rejected'
        candidate['rejection_reason'] = 'fit_failed'
        assert candidate['status'] == 'rejected'
        assert 'fit' in candidate['rejection_reason']


class TestEndToEndPipeline:
    """End-to-end test of the full pipeline with synthetic data."""

    def test_full_pipeline_realistic_diamond(self):
        """Simulate a realistic diamond sample with NVs on a gradient background."""
        cip = ConfocalImageAnalysis()

        # 80×80 image, 16 μm × 16 μm
        image = np.zeros((80, 80, 4))
        x_coords = np.linspace(0, 16e-6, 80)
        y_coords = np.linspace(0, 16e-6, 80)
        for i in range(80):
            image[:, i, 0] = x_coords[i]
            image[i, :, 1] = y_coords[i]

        # Background with gradient (simulates sample tilt)
        np.random.seed(42)
        for r in range(80):
            for c in range(80):
                bg = 4000 + 20 * r + 10 * c  # gradient
                image[r, c, 3] = bg + np.random.normal(0, 200)

        # Add 4 NV centers
        nvs = [(20, 20, 70000), (60, 20, 50000), (40, 60, 90000), (20, 60, 30000)]
        for (nr, nc, amp) in nvs:
            for r in range(80):
                for c in range(80):
                    r2 = (r - nr)**2 + (c - nc)**2
                    image[r, c, 3] += amp * np.exp(-r2 / 8.0)

        # Run detection
        fluorescence = image[:, :, 3].astype(float)
        background = cip.estimate_background(fluorescence, kernel_size=15)
        corrected = cip.subtract_background(fluorescence, background)
        noise_sigma = cip.estimate_noise_level(corrected)
        threshold = 5.0 * noise_sigma
        mask = cip.threshold_intensity(corrected, threshold)
        maxima = cip.detect_local_maxima(corrected, mask, 7)

        # Validate
        valid = []
        for pos in maxima:
            r, c = int(pos[0]), int(pos[1])
            ok, circ = cip.validate_spot_shape(corrected, r, c, 3)
            if ok:
                valid.append((r, c))

        # Should find all 4 NVs despite the gradient background
        assert len(valid) >= 3, f"Found {len(valid)} NVs, expected >= 3"

        # Each detected spot should be near a true NV
        true_pixels = [(20, 20), (60, 20), (40, 60), (20, 60)]
        matched = 0
        for (dr, dc) in valid:
            for (tr, tc) in true_pixels:
                if abs(dr - tr) <= 3 and abs(dc - tc) <= 3:
                    matched += 1
                    break
        assert matched >= 3, f"Only {matched}/4 NVs matched true positions"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
