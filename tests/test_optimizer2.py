"""Tests for the offline, bounded Optimizer2D analysis module."""

import os

import numpy as np
import pytest

from logic.cell_segmentation_logic import CellSegmentationLogic
from logic.optimizer2 import Optimizer2D


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFOCAL2_DIR = os.path.join(PROJECT_ROOT, 'Confocal2')
CLOSE_SCAN_FILES = (
    '20260706-1701-46_confocal_xy_data.dat',
    '20260706-1724-08_confocal_xy_data.dat',
    '20260706-1833-28_confocal_xy_data.dat',
)


def _gaussian(x_grid, y_grid, amplitude, center_x, center_y,
              sigma_x, sigma_y, offset):
    return offset + amplitude * np.exp(
        -0.5 * (((x_grid - center_x) / sigma_x) ** 2 +
                ((y_grid - center_y) / sigma_y) ** 2))


def test_synthetic_peak_is_localized_within_sampled_support():
    x_coordinates = np.linspace(-0.5e-6, 0.5e-6, 21)
    y_coordinates = np.linspace(-0.4e-6, 0.4e-6, 17)
    x_grid, y_grid = np.meshgrid(x_coordinates, y_coordinates)
    true_position = (0.13e-6, -0.11e-6)
    image = _gaussian(x_grid, y_grid, 8.0e4, true_position[0],
                      true_position[1], 0.10e-6, 0.12e-6, 1.1e4)

    result = Optimizer2D().fit_local(
        image, x_coordinates, y_coordinates, seed_position_m=(0.0, 0.0),
        window_size_m=1.0e-6)

    assert result.success
    assert result.r_squared > 0.999
    assert np.allclose(result.position_m, true_position, atol=1e-10)
    x_min, x_max, y_min, y_max = result.sampled_bounds_m
    assert x_min <= result.position_m[0] <= x_max
    assert y_min <= result.position_m[1] <= y_max


def test_edge_peak_is_flagged_without_extrapolating():
    x_coordinates = np.linspace(-0.5e-6, 0.5e-6, 21)
    y_coordinates = np.linspace(-0.5e-6, 0.5e-6, 21)
    x_grid, y_grid = np.meshgrid(x_coordinates, y_coordinates)
    image = _gaussian(x_grid, y_grid, 5.0e4, 0.49e-6, 0.0,
                      0.08e-6, 0.08e-6, 8.0e3)

    result = Optimizer2D().fit_local(
        image, x_coordinates, y_coordinates, seed_position_m=(0.0, 0.0),
        window_size_m=1.0e-6)

    assert result.success
    assert result.is_edge_fit
    x_min, x_max, y_min, y_max = result.sampled_bounds_m
    assert x_min <= result.position_m[0] <= x_max
    assert y_min <= result.position_m[1] <= y_max


@pytest.mark.parametrize('filename', CLOSE_SCAN_FILES)
def test_confocal2_replay_produces_bounded_audit_result(filename):
    """Replay available close scans without inferring live optimizer behaviour."""
    path = os.path.join(CONFOCAL2_DIR, filename)
    if not os.path.exists(path):
        pytest.skip('Confocal2 close-scan fixture is unavailable')

    image, x_coordinates, y_coordinates, _ = (
        CellSegmentationLogic().parse_dat_file(path))
    counts = image[:, :, 3]
    peak_row, peak_col = np.unravel_index(np.argmax(counts), counts.shape)
    seed = (x_coordinates[peak_col], y_coordinates[peak_row])
    window_size = min((x_coordinates[-1] - x_coordinates[0]) / 3.0,
                      (y_coordinates[-1] - y_coordinates[0]) / 3.0)

    result = Optimizer2D().fit_local(
        counts, x_coordinates, y_coordinates, seed_position_m=seed,
        window_size_m=window_size)

    assert result.sample_shape[0] >= 5
    assert result.sample_shape[1] >= 5
    if result.success:
        x_min, x_max, y_min, y_max = result.sampled_bounds_m
        assert x_min <= result.position_m[0] <= x_max
        assert y_min <= result.position_m[1] <= y_max
