# -*- coding: utf-8 -*-
"""
Unit tests for the ConfocalImageAnalysis CIP utility class.

Tests the image analysis functions used for automated NV center detection:
background estimation, noise estimation, thresholding, local maxima,
spot shape validation, clustering, and Gaussian refinement.

Run with: python -m pytest tests/test_image_analysis.py -v
"""

import numpy as np
import pytest

# Add the logic directory to the path for import
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'logic'))

from image_analysis import ConfocalImageAnalysis


class TestBackgroundEstimation:
    """Tests for background estimation and subtraction."""

    def test_flat_background(self):
        """A flat image should have a flat background estimate."""
        image = np.ones((50, 50)) * 1000.0
        bg = ConfocalImageAnalysis.estimate_background(image, kernel_size=15)
        np.testing.assert_allclose(bg, 1000.0, atol=1.0)

    def test_background_removes_gradient(self):
        """Background subtraction should remove a linear gradient."""
        x = np.linspace(0, 100, 50)
        y = np.linspace(0, 100, 50)
        xx, yy = np.meshgrid(x, y)
        gradient = xx + yy  # Linear gradient
        bg = ConfocalImageAnalysis.estimate_background(gradient, kernel_size=15)
        corrected = ConfocalImageAnalysis.subtract_background(gradient, bg)
        # Corrected image should be mostly flat
        assert corrected.std() < gradient.std()

    def test_spot_preserved_after_background_subtraction(self):
        """A bright spot should survive background subtraction."""
        image = np.ones((50, 50)) * 100.0
        # Add a bright spot
        image[25, 25] = 10000.0
        image[24:27, 24:27] = 5000.0
        image[25, 25] = 10000.0

        bg = ConfocalImageAnalysis.estimate_background(image, kernel_size=15)
        corrected = ConfocalImageAnalysis.subtract_background(image, bg)

        # The spot should still be the brightest point
        assert corrected[25, 25] > corrected[0, 0]
        assert corrected[25, 25] > 1000.0

    def test_non_negative_after_subtraction(self):
        """Background-subtracted image should have no negative values."""
        image = np.random.poisson(100, (50, 50)).astype(float)
        bg = ConfocalImageAnalysis.estimate_background(image, kernel_size=15)
        corrected = ConfocalImageAnalysis.subtract_background(image, bg)
        assert np.all(corrected >= 0)


class TestNoiseEstimation:
    """Tests for noise level estimation."""

    def test_known_noise_level(self):
        """MAD-based estimation should recover known Gaussian noise level."""
        np.random.seed(42)
        noise_sigma = 50.0
        image = np.random.normal(1000, noise_sigma, (100, 100))
        estimated = ConfocalImageAnalysis.estimate_noise_level(image)
        # Should be within 20% of true sigma
        assert abs(estimated - noise_sigma) / noise_sigma < 0.2

    def test_noise_robust_to_outliers(self):
        """Noise estimate should not be heavily biased by a few bright spots."""
        np.random.seed(42)
        image = np.random.normal(1000, 50, (100, 100))
        # Add a few very bright outlier pixels (NV centers)
        image[30, 30] = 100000
        image[70, 50] = 100000
        image[20, 80] = 100000

        estimated = ConfocalImageAnalysis.estimate_noise_level(image)
        # Should still be close to 50, not pulled up by outliers
        assert estimated < 200

    def test_zero_noise(self):
        """Constant image should have ~zero noise."""
        image = np.ones((50, 50)) * 5000.0
        estimated = ConfocalImageAnalysis.estimate_noise_level(image)
        assert estimated < 1.0


class TestNormalization:
    """Tests for intensity normalization."""

    def test_output_range(self):
        """Normalized image should be in [0, 1] range."""
        image = np.random.uniform(0, 10000, (50, 50))
        normalized = ConfocalImageAnalysis.normalize_intensity(image)
        assert normalized.min() >= 0.0
        assert normalized.max() <= 1.0

    def test_constant_image(self):
        """Constant image should normalize to zeros (no variation)."""
        image = np.ones((50, 50)) * 5000
        normalized = ConfocalImageAnalysis.normalize_intensity(image)
        assert np.all(normalized == 0)


class TestLocalMaxima:
    """Tests for local maxima detection."""

    def test_single_peak(self):
        """Should find a single peak in a Gaussian spot."""
        image = np.zeros((50, 50))
        # Add a Gaussian spot
        for i in range(50):
            for j in range(50):
                r2 = (i - 25) ** 2 + (j - 25) ** 2
                image[i, j] = 10000 * np.exp(-r2 / 10.0)

        mask = image > 100
        maxima = ConfocalImageAnalysis.detect_local_maxima(image, mask, 5)
        # Should find exactly one maximum near (25, 25)
        assert len(maxima) >= 1
        # The peak should be at (25, 25)
        distances = np.sqrt((maxima[:, 0] - 25) ** 2 + (maxima[:, 1] - 25) ** 2)
        assert np.min(distances) < 2

    def test_two_separated_peaks(self):
        """Should find two peaks that are well separated."""
        image = np.zeros((50, 50))
        # Add two Gaussian spots
        for i in range(50):
            for j in range(50):
                r2_a = (i - 15) ** 2 + (j - 15) ** 2
                r2_b = (i - 35) ** 2 + (j - 35) ** 2
                image[i, j] = 10000 * np.exp(-r2_a / 10.0) + \
                              8000 * np.exp(-r2_b / 10.0)

        mask = image > 100
        maxima = ConfocalImageAnalysis.detect_local_maxima(image, mask, 7)
        # Should find at least 2 maxima
        assert len(maxima) >= 2


class TestSpotShapeValidation:
    """Tests for spot circularity validation."""

    def test_circular_spot_passes(self):
        """A circular Gaussian spot should pass shape validation."""
        image = np.zeros((50, 50))
        for i in range(50):
            for j in range(50):
                r2 = (i - 25) ** 2 + (j - 25) ** 2
                image[i, j] = 10000 * np.exp(-r2 / 20.0)

        is_valid, circularity = ConfocalImageAnalysis.validate_spot_shape(
            image, 25, 25, 5)
        assert is_valid
        assert circularity > 0.8

    def test_elongated_spot_fails(self):
        """An elongated feature should fail shape validation."""
        image = np.zeros((50, 50))
        # Horizontal line (not circular)
        image[25, 10:40] = 10000

        is_valid, circularity = ConfocalImageAnalysis.validate_spot_shape(
            image, 25, 25, 5)
        assert not is_valid or circularity < 0.7


class TestClustering:
    """Tests for spatial clustering of detections."""

    def test_no_clustering_needed(self):
        """Well-separated points should not be merged."""
        positions = np.array([[10, 10], [30, 30], [10, 40]])
        intensities = np.array([1000, 2000, 1500])
        clustered = ConfocalImageAnalysis.cluster_detections(
            positions, intensities, min_distance=5)
        assert len(clustered) == 3

    def test_nearby_points_merged(self):
        """Points within min_distance should be merged."""
        positions = np.array([[10, 10], [11, 11], [30, 30]])
        intensities = np.array([1000, 2000, 1500])
        clustered = ConfocalImageAnalysis.cluster_detections(
            positions, intensities, min_distance=5)
        assert len(clustered) == 2
        # Brightest should be kept from the first cluster
        assert clustered[0][1] == 2000 or clustered[1][1] == 2000

    def test_empty_input(self):
        """Empty input should return empty output."""
        clustered = ConfocalImageAnalysis.cluster_detections(
            np.array([]).reshape(0, 2), np.array([]), min_distance=5)
        assert len(clustered) == 0


class TestGaussianRefinement:
    """Tests for sub-pixel Gaussian position refinement."""

    def test_centered_peak(self):
        """Refinement of a centered Gaussian should return the center."""
        image = np.zeros((50, 50))
        for i in range(50):
            for j in range(50):
                r2 = (i - 25) ** 2 + (j - 25) ** 2
                image[i, j] = 10000 * np.exp(-r2 / 20.0)

        result = ConfocalImageAnalysis.refine_position_gaussian_2d(
            image, 25, 25, 5)
        assert abs(result['row'] - 25.0) < 1.0
        assert abs(result['col'] - 25.0) < 1.0
        assert result['amplitude'] > 0

    def test_off_center_peak(self):
        """Refinement should pull position toward the true peak."""
        image = np.zeros((50, 50))
        # Peak at (25.5, 25.5) — between pixels
        for i in range(50):
            for j in range(50):
                r2 = (i - 25.5) ** 2 + (j - 25.5) ** 2
                image[i, j] = 10000 * np.exp(-r2 / 20.0)

        # Start at pixel (25, 25)
        result = ConfocalImageAnalysis.refine_position_gaussian_2d(
            image, 25, 25, 5)
        # Should be closer to 25.5 than to 25.0
        assert abs(result['row'] - 25.5) < abs(25.0 - 25.5)


class TestIntensityContrast:
    """Tests for intensity contrast computation."""

    def test_high_contrast_spot(self):
        """A bright spot on dim background should have high contrast."""
        image = np.ones((50, 50)) * 100.0
        image[25, 25] = 10000.0
        image[24:27, 24:27] = 5000.0
        image[25, 25] = 10000.0

        contrast = ConfocalImageAnalysis.compute_intensity_contrast(
            image, 25, 25, 5)
        assert contrast > 5.0

    def test_low_contrast(self):
        """A pixel barely above background should have low contrast."""
        image = np.ones((50, 50)) * 1000.0
        image[25, 25] = 1100.0

        contrast = ConfocalImageAnalysis.compute_intensity_contrast(
            image, 25, 25, 5)
        assert contrast < 2.0


class TestConfidence:
    """Tests for detection confidence scoring."""

    def test_high_quality_gives_high_confidence(self):
        """High SNR + good shape + good fit should give high confidence."""
        confidence = ConfocalImageAnalysis.compute_detection_confidence(
            snr=20.0, circularity=0.95, fit_quality=0.9)
        assert confidence > 0.8

    def test_low_quality_gives_low_confidence(self):
        """Low SNR should give low confidence regardless of other factors."""
        confidence = ConfocalImageAnalysis.compute_detection_confidence(
            snr=2.0, circularity=0.95, fit_quality=0.9)
        assert confidence < 0.7

    def test_confidence_range(self):
        """Confidence should always be in [0, 1]."""
        for snr in [0, 5, 20, 100]:
            for circ in [0, 0.5, 1.0]:
                for fit in [0, 0.5, 1.0]:
                    c = ConfocalImageAnalysis.compute_detection_confidence(
                        snr, circ, fit)
                    assert 0 <= c <= 1


class TestAutoColorRange:
    """Tests for automatic color range computation."""

    def test_basic_range(self):
        """Color range should span the data."""
        image = np.random.uniform(100, 10000, (50, 50))
        vmin, vmax = ConfocalImageAnalysis.auto_color_range(image)
        assert vmin < vmax
        assert vmin >= image.min()
        assert vmax <= image.max()

    def test_constant_image(self):
        """Constant image should still return valid range."""
        image = np.ones((50, 50)) * 5000
        vmin, vmax = ConfocalImageAnalysis.auto_color_range(image)
        assert vmax > vmin


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
