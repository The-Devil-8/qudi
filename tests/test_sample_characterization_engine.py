# -*- coding: utf-8 -*-
"""
Unit and integration tests for SampleCharacterizationEngine module.

Tests the automatic sample characterization, density classification (SPARSE vs DENSE),
ambiguity handling with algorithm duels, quality scoring, and compatibility with
downstream scan queue pipelines.

Visual outputs are saved to: tests/output_visuals/sample_characterization/
"""

from typing import Any, Dict, List, Optional, Tuple, Union
import os
import sys
import numpy as np
import pytest
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Add project root and logic directory to python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if os.path.join(PROJECT_ROOT, 'logic') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'logic'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import skimage
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

from sample_characterization_engine import (
    SampleCharacterizationEngine,
    SampleType,
    AlgorithmChoice,
    SampleCharacterization,
    SegmentationResult,
    CharacterizationResult,
)


VISUALS_DIR = os.path.join(PROJECT_ROOT, 'tests', 'output_visuals', 'sample_characterization')


# ======================================================================
# Synthetic Image Generators & Helpers
# ======================================================================

def _add_gaussian_blob(
    image: np.ndarray,
    center_row: float,
    center_col: float,
    radius: float,
    peak_intensity: float,
) -> None:
    """
    Add a 2D Gaussian blob representing a fluorescent cell to channel 3 of the image.

    Parameters
    ----------
    image : np.ndarray
        3D numpy array of shape (ny, nx, 4) where channel 3 is fluorescence intensity.
    center_row : float
        Row coordinate (y) of the cell centroid in pixel coordinates.
    center_col : float
        Column coordinate (x) of the cell centroid in pixel coordinates.
    radius : float
        Characteristic cell radius in pixels.
    peak_intensity : float
        Peak fluorescence counts/sec added at center.
    """
    ny, nx = image.shape[:2]
    yy, xx = np.ogrid[:ny, :nx]
    dist_sq = (yy - center_row)**2 + (xx - center_col)**2
    sigma = radius / 2.5
    blob = peak_intensity * np.exp(-dist_sq / (2 * sigma**2))
    image[:, :, 3] += blob


def _make_synthetic_image(
    ny: int,
    nx: int,
    cells: List[Dict[str, Any]],
    fov_um: float = 200.0,
    noise_sigma: float = 0.0,
) -> np.ndarray:
    """
    Create a synthetic confocal image with specified cells and physical coordinate grids.

    Parameters
    ----------
    ny : int
        Number of pixels along Y-axis.
    nx : int
        Number of pixels along X-axis.
    cells : list of dict
        List of cell specifications with keys:
        - 'center_row' (float)
        - 'center_col' (float)
        - 'radius' (float)
        - 'peak_intensity' (float)
        - optional 'bg_intensity' (float)
    fov_um : float, optional
        Physical field of view in micrometers, by default 200.0.
    noise_sigma : float, optional
        Standard deviation of Gaussian noise added to background, by default 0.0.

    Returns
    -------
    np.ndarray
        Synthetic confocal image with shape (ny, nx, 4) in meters and counts:
        - Channel 0: X coordinates (meters)
        - Channel 1: Y coordinates (meters)
        - Channel 2: Z coordinates (meters, zeros)
        - Channel 3: Fluorescence counts (c/s)
    """
    fov_m = fov_um * 1e-6
    x_coords = np.linspace(0.0, fov_m, nx)
    y_coords = np.linspace(0.0, fov_m, ny)
    xx, yy = np.meshgrid(x_coords, y_coords)

    image = np.zeros((ny, nx, 4), dtype=float)
    image[:, :, 0] = xx
    image[:, :, 1] = yy
    image[:, :, 2] = 0.0

    bg_val = float(cells[0].get('bg_intensity', 1000.0)) if cells else 0.0
    image[:, :, 3] = bg_val

    if noise_sigma > 0.0:
        noise = np.random.normal(0, noise_sigma, (ny, nx))
        image[:, :, 3] = np.maximum(image[:, :, 3] + noise, 0.0)

    for cell in cells:
        _add_gaussian_blob(
            image=image,
            center_row=float(cell['center_row']),
            center_col=float(cell['center_col']),
            radius=float(cell['radius']),
            peak_intensity=float(cell['peak_intensity']),
        )

    return image


# ======================================================================
# Unit & Functional Tests for SampleCharacterizationEngine
# ======================================================================

def test_classify_sparse_sample():
    """
    Test classification of a sparse sample with 5 well-separated Gaussian cells.

    Asserts:
    - characterization.sample_type == SampleType.SPARSE
    - characterization.recommended_algorithm == AlgorithmChoice.ALGO_A_SPARSE
    - characterization.confidence > 0.5
    - segmentation.mask has some True pixels
    - len(segmentation.cell_boxes) >= 1
    """
    cells = [
        {'center_row': 40, 'center_col': 40, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 40, 'center_col': 160, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 100, 'center_col': 100, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 160, 'center_col': 40, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 160, 'center_col': 160, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
    ]
    image = _make_synthetic_image(ny=200, nx=200, cells=cells, fov_um=200.0)

    engine = SampleCharacterizationEngine()
    char_result = engine.characterize_and_segment(image, min_cell_area_um2=30.0)

    assert char_result is not None, "characterize_and_segment returned None"
    char = char_result.characterization
    seg = char_result.segmentation

    assert char.sample_type == SampleType.SPARSE, f"Expected SPARSE sample, got {char.sample_type}"
    assert char.recommended_algorithm == AlgorithmChoice.ALGO_A_SPARSE, (
        f"Expected ALGO_A_SPARSE, got {char.recommended_algorithm}"
    )
    assert char.confidence > 0.5, f"Expected confidence > 0.5, got {char.confidence}"
    assert seg.mask.any(), "Segmentation mask should contain True pixels"
    assert len(seg.cell_boxes) >= 1, f"Expected at least 1 cell box, got {len(seg.cell_boxes)}"


def test_classify_dense_sample():
    """
    Test classification of a dense sample with 25+ overlapping cells covering >45% area.

    Asserts:
    - characterization.sample_type == SampleType.DENSE
    - characterization.recommended_algorithm == AlgorithmChoice.ALGO_B_DENSE
    """
    cells = []
    # Dense overlapping clusters covering > 45% of FOV
    grid_coords = [25, 50, 75, 100, 125, 150, 175]
    for r in grid_coords:
        for c in grid_coords:
            cells.append({
                'center_row': r,
                'center_col': c,
                'radius': 22,
                'peak_intensity': 60000.0,
                'bg_intensity': 2000.0,
            })
    image = _make_synthetic_image(ny=200, nx=200, cells=cells, fov_um=200.0)

    engine = SampleCharacterizationEngine()
    char_result = engine.characterize_and_segment(image, min_cell_area_um2=30.0)

    assert char_result is not None, "characterize_and_segment returned None"
    char = char_result.characterization

    assert char.sample_type == SampleType.DENSE, f"Expected DENSE sample, got {char.sample_type}"
    assert char.recommended_algorithm == AlgorithmChoice.ALGO_B_DENSE, (
        f"Expected ALGO_B_DENSE, got {char.recommended_algorithm}"
    )


def test_classify_ambiguous_triggers_duel():
    """
    Test characterization on an ambiguous sample with ~28% coverage.

    Asserts:
    - char_result.duel_performed == True (or confidence is moderate / sample_type is AMBIGUOUS)
    """
    cells = []
    # 12 moderately spaced blobs: 3x4 grid on 200x200
    for r in [40, 100, 160]:
        for c in [30, 75, 125, 170]:
            cells.append({
                'center_row': r,
                'center_col': c,
                'radius': 16,
                'peak_intensity': 25000.0,
                'bg_intensity': 2000.0,
            })
    image = _make_synthetic_image(ny=200, nx=200, cells=cells, fov_um=200.0)

    engine = SampleCharacterizationEngine()
    char_result = engine.characterize_and_segment(image, min_cell_area_um2=30.0)

    assert char_result is not None, "characterize_and_segment returned None"
    assert (
        char_result.duel_performed is True
        or char_result.characterization.sample_type == SampleType.AMBIGUOUS
        or char_result.characterization.confidence <= 0.85
    ), (
        f"Expected duel or moderate confidence for ambiguous sample, got {char_result.characterization.confidence}"
    )


def test_empty_image():
    """
    Test edge case: completely dark / blank image (zeros).

    Asserts:
    - No crashes or unhandled exceptions.
    - foreground_fraction ~ 0.0.
    """
    empty_image = np.zeros((100, 100, 4), dtype=float)
    x = np.linspace(0, 100e-6, 100)
    y = np.linspace(0, 100e-6, 100)
    xx, yy = np.meshgrid(x, y)
    empty_image[:, :, 0] = xx
    empty_image[:, :, 1] = yy

    engine = SampleCharacterizationEngine()
    char_result = engine.characterize_and_segment(empty_image)

    assert char_result is not None, "characterize_and_segment returned None for empty image"
    assert char_result.characterization.foreground_fraction == pytest.approx(0.0, abs=0.05)


def test_single_cell():
    """
    Test characterization on an image containing exactly one cell.

    Asserts:
    - sample_type == SampleType.SPARSE
    - estimated_cell_count >= 1
    """
    cells = [{
        'center_row': 50,
        'center_col': 50,
        'radius': 15,
        'peak_intensity': 15000.0,
        'bg_intensity': 1000.0,
    }]
    image = _make_synthetic_image(ny=100, nx=100, cells=cells, fov_um=100.0)

    engine = SampleCharacterizationEngine()
    char = engine.characterize_sample(image)

    assert char is not None, "characterize_sample returned None"
    assert char.sample_type == SampleType.SPARSE, f"Expected SPARSE sample, got {char.sample_type}"
    assert char.estimated_cell_count >= 1, f"Expected estimated_cell_count >= 1, got {char.estimated_cell_count}"


def test_metric_foreground_fraction():
    """
    Directly test _compute_foreground_fraction returns reasonable values for known inputs.
    """
    engine = SampleCharacterizationEngine()

    # 1. Blank mask -> ~0.0
    empty_mask = np.zeros((100, 100), dtype=bool)
    fg_empty = engine._compute_foreground_fraction(empty_mask)
    assert fg_empty == pytest.approx(0.0, abs=0.05), f"Expected fg ~0.0, got {fg_empty}"

    # 2. Synthetic 25% foreground block
    block_mask = np.zeros((100, 100), dtype=bool)
    block_mask[25:75, 25:75] = True  # 50x50 = 2500 / 10000 = 25%
    fg_block = engine._compute_foreground_fraction(block_mask)
    assert 0.20 <= fg_block <= 0.30, f"Expected fg between 0.20 and 0.30, got {fg_block}"


def test_metric_bimodality():
    """
    Test _compute_histogram_bimodality with bimodal and unimodal distributions.

    Asserts:
    - Bimodal distribution achieves a higher bimodality score than unimodal distribution.
    """
    engine = SampleCharacterizationEngine()
    np.random.seed(42)

    # Unimodal: single Gaussian noise distribution
    unimodal_img = np.zeros((100, 100, 4), dtype=float)
    unimodal_img[:, :, 3] = np.random.normal(5000, 500, (100, 100))

    # Bimodal: two distinct peaks (background + high fluorescence signal)
    bimodal_img = np.zeros((100, 100, 4), dtype=float)
    fluor_bimodal = np.zeros((100, 100), dtype=float)
    fluor_bimodal[:50, :] = np.random.normal(1000, 100, (50, 100))
    fluor_bimodal[50:, :] = np.random.normal(20000, 1000, (50, 100))
    bimodal_img[:, :, 3] = fluor_bimodal

    score_uni = engine._compute_histogram_bimodality(unimodal_img)
    score_bi = engine._compute_histogram_bimodality(bimodal_img)

    assert score_bi > score_uni, (
        f"Bimodal score ({score_bi:.4f}) should exceed unimodal score ({score_uni:.4f})"
    )


def test_quality_scorer():
    """
    Test _score_result with known good vs bad mock results.

    Asserts:
    - A valid segmentation matching signal scores higher than an empty/blank result.
    """
    engine = SampleCharacterizationEngine()
    cells = [{
        'center_row': 50,
        'center_col': 50,
        'radius': 15,
        'peak_intensity': 15000.0,
        'bg_intensity': 1000.0,
    }]
    image = _make_synthetic_image(ny=100, nx=100, cells=cells, fov_um=100.0)

    # Good segmentation result matching cell blob
    good_mask = np.zeros((100, 100), dtype=bool)
    yy, xx = np.ogrid[:100, :100]
    good_mask[((yy - 50)**2 + (xx - 50)**2) < 15**2] = True
    good_labeled = good_mask.astype(int)
    good_boxes = [{'label': 1, 'bbox_px': (35, 35, 65, 65), 'area_px': int(good_mask.sum())}]
    good_result = SegmentationResult(
        mask=good_mask,
        labeled=good_labeled,
        cell_boxes=good_boxes,
        algorithm_used=AlgorithmChoice.ALGO_A_SPARSE,
        quality_score=0.0,
    )

    # Bad segmentation result (empty)
    bad_mask = np.zeros((100, 100), dtype=bool)
    bad_labeled = np.zeros((100, 100), dtype=int)
    bad_boxes = []
    bad_result = SegmentationResult(
        mask=bad_mask,
        labeled=bad_labeled,
        cell_boxes=bad_boxes,
        algorithm_used=AlgorithmChoice.ALGO_A_SPARSE,
        quality_score=0.0,
    )

    score_good = engine._score_result(good_result.mask, good_result.labeled, good_result.cell_boxes, image)
    score_bad = engine._score_result(bad_result.mask, bad_result.labeled, bad_result.cell_boxes, image)

    assert score_good > score_bad, (
        f"Good result score ({score_good}) must exceed bad result score ({score_bad})"
    )


def test_output_format_compatibility():
    """
    Verify CharacterizationResult has segmentation_dict property with keys
    'roi_mask', 'component_labels', 'stats'.
    """
    cells = [{
        'center_row': 50,
        'center_col': 50,
        'radius': 15,
        'peak_intensity': 15000.0,
        'bg_intensity': 1000.0,
    }]
    image = _make_synthetic_image(ny=100, nx=100, cells=cells, fov_um=100.0)

    engine = SampleCharacterizationEngine()
    char_result = engine.characterize_and_segment(image)

    assert hasattr(char_result, 'segmentation_dict'), (
        "CharacterizationResult must have a 'segmentation_dict' property"
    )
    seg_dict = char_result.segmentation_dict

    assert isinstance(seg_dict, dict), "segmentation_dict must return a dictionary"
    assert 'roi_mask' in seg_dict, "segmentation_dict must contain 'roi_mask'"
    assert 'component_labels' in seg_dict, "segmentation_dict must contain 'component_labels'"
    assert 'stats' in seg_dict, "segmentation_dict must contain 'stats'"

    assert isinstance(seg_dict['roi_mask'], np.ndarray), "'roi_mask' must be a numpy ndarray"
    assert isinstance(seg_dict['component_labels'], np.ndarray), "'component_labels' must be a numpy ndarray"
    assert isinstance(seg_dict['stats'], list), "'stats' must be a list of dicts"


def test_visual_diagnostics():
    """
    Visual diagnostic: Save comparison plots to tests/output_visuals/sample_characterization/
    showing classification and segmentation results across Sparse, Dense, and Ambiguous scenarios.
    """
    os.makedirs(VISUALS_DIR, exist_ok=True)
    engine = SampleCharacterizationEngine()

    # 1. Sparse scenario
    sparse_cells = [
        {'center_row': 40, 'center_col': 40, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 40, 'center_col': 160, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 100, 'center_col': 100, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 160, 'center_col': 40, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
        {'center_row': 160, 'center_col': 160, 'radius': 15, 'peak_intensity': 15000.0, 'bg_intensity': 1000.0},
    ]

    # 2. Dense scenario (36 cells, heavy overlap)
    dense_cells = []
    for r in [25, 55, 85, 115, 145, 175]:
        for c in [25, 55, 85, 115, 145, 175]:
            dense_cells.append({
                'center_row': r,
                'center_col': c,
                'radius': 25,
                'peak_intensity': 50000.0,
                'bg_intensity': 3000.0,
            })

    # 3. Ambiguous scenario (12 cells, moderate overlap)
    ambiguous_cells = []
    for r in [40, 100, 160]:
        for c in [30, 75, 125, 170]:
            ambiguous_cells.append({
                'center_row': r,
                'center_col': c,
                'radius': 16,
                'peak_intensity': 25000.0,
                'bg_intensity': 2000.0,
            })

    scenarios = [
        ('Sparse Sample', sparse_cells),
        ('Dense Sample', dense_cells),
        ('Ambiguous Sample', ambiguous_cells),
    ]

    fig, axes = plt.subplots(len(scenarios), 3, figsize=(15, 5 * len(scenarios)))

    for i, (name, cells) in enumerate(scenarios):
        img = _make_synthetic_image(ny=200, nx=200, cells=cells, fov_um=200.0)
        res = engine.characterize_and_segment(img)
        fluor = img[:, :, 3]

        p2, p98 = np.percentile(fluor, (2, 98))
        if p98 <= p2:
            p98 = p2 + 1.0

        sample_type_str = (
            res.characterization.sample_type.name
            if hasattr(res.characterization.sample_type, 'name')
            else str(res.characterization.sample_type)
        )
        algo_str = (
            res.segmentation.algorithm_used.name
            if hasattr(res.segmentation.algorithm_used, 'name')
            else str(res.segmentation.algorithm_used)
        )

        # 1. Raw fluorescence image
        axes[i, 0].imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
        axes[i, 0].set_title(f"{name} - Raw Scan\nClassified: {sample_type_str}", fontsize=10, fontweight='bold')
        axes[i, 0].axis('off')

        # 2. Mask overlay
        axes[i, 1].imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
        mask_overlay = np.zeros((*fluor.shape, 4), dtype=float)
        mask_overlay[res.segmentation.mask] = [0, 1, 0, 0.4]  # Green overlay
        axes[i, 1].imshow(mask_overlay)
        axes[i, 1].set_title(
            f"Segmentation Mask ({algo_str})\nFG Fraction: {res.characterization.foreground_fraction:.1%}",
            fontsize=10,
            fontweight='bold',
        )
        axes[i, 1].axis('off')

        # 3. Labeled instances and bounding boxes
        axes[i, 2].imshow(res.segmentation.labeled, cmap='tab20', interpolation='nearest')
        for box in res.segmentation.cell_boxes:
            min_r, min_c, max_r, max_c = box['bbox_px']
            rect = plt.Rectangle(
                (min_c, min_r),
                max_c - min_c,
                max_r - min_r,
                fill=False,
                edgecolor='yellow',
                linewidth=1.0,
                linestyle='--',
            )
            axes[i, 2].add_patch(rect)
        axes[i, 2].set_title(
            f"Extracted Instances (N={len(res.segmentation.cell_boxes)})\n"
            f"Duel: {res.duel_performed} | Conf: {res.characterization.confidence:.2f}",
            fontsize=10,
            fontweight='bold',
        )
        axes[i, 2].axis('off')

    plt.tight_layout()
    out_png = os.path.join(VISUALS_DIR, 'characterization_summary.png')
    plt.savefig(out_png, dpi=150, bbox_inches='tight')
    plt.close(fig)

    assert os.path.exists(out_png), f"Visual diagnostic plot was not saved at {out_png}"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
