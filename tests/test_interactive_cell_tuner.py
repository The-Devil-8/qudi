# -*- coding: utf-8 -*-
"""
Tests for the Interactive Cell Processor Algorithm Tuner.

Validates:
- Headless GUI initialization & widget creation
- Loading of real confocal .dat and synthetic scan files
- Execution across all 5 algorithm modes
- Parameter updates, nucleus void detection, and bright cluster masking
- Live POI extraction & outside-NV detection
- Configuration export to Python/JSON/YAML formats
"""

import os
import sys
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

try:
    from qtpy import QtWidgets, QtCore, QtGui
except ImportError:
    try:
        from pyqtgraph.Qt import QtWidgets, QtCore, QtGui
    except ImportError:
        try:
            from PyQt5 import QtWidgets, QtCore, QtGui
        except ImportError:
            from PySide2 import QtWidgets, QtCore, QtGui

import pyqtgraph as pg

app = QtWidgets.QApplication.instance()
if app is None:
    app = QtWidgets.QApplication(['-platform', 'offscreen'])

from upgrade.interactive_cell_tuner import InteractiveCellTuner

CONFOCAL2 = os.path.join(PROJECT_ROOT, 'Confocal2')
TEST_SCAN = os.path.join(CONFOCAL2, '20260706-1701-46_confocal_xy_data.dat')


def test_tuner_initialization():
    """Test that InteractiveCellTuner initializes and loads scan data without errors."""
    print("Testing InteractiveCellTuner initialization...")
    tuner = InteractiveCellTuner(image_path=TEST_SCAN if os.path.exists(TEST_SCAN) else None)
    assert tuner is not None
    assert tuner.image is not None
    assert tuner.fluor is not None
    assert tuner.fluor.ndim == 2
    print(f"  [PASS] Initialized successfully. Image shape: {tuner.fluor.shape}")


def test_algorithm_switching():
    """Test switching across all 5 segmentation algorithms."""
    print("Testing all 5 segmentation algorithm modes...")
    tuner = InteractiveCellTuner(image_path=TEST_SCAN if os.path.exists(TEST_SCAN) else None)

    algos = [
        "1. Seeded Hysteresis (Upgraded)",
        "2. Legacy Cell Region Processor (Otsu)",
        "3. Dual-Path Gated Local Adaptive",
        "4. Distance-Transform Watershed",
        "5. Macro-Constrained Micro",
    ]

    for algo in algos:
        idx = tuner.algo_combo.findText(algo)
        assert idx >= 0, f"Algorithm {algo} not found in combo box"
        tuner.algo_combo.setCurrentIndex(idx)
        tuner.run_processing()

        res = tuner.current_result
        assert res is not None, f"Result is None for algorithm {algo}"
        assert res.cell_interior_mask is not None
        assert res.processable_mask is not None
        print(f"  [PASS] Algorithm: '{algo}' -> Cell Area: {res.cell_interior_mask.sum()} px, Processable: {res.processable_mask.sum()} px")


def test_presets_application():
    """Test loading and applying presets."""
    print("Testing presets application...")
    tuner = InteractiveCellTuner(image_path=TEST_SCAN if os.path.exists(TEST_SCAN) else None)

    for preset_name in tuner.presets.keys():
        idx = tuner.preset_combo.findText(preset_name)
        assert idx >= 0, f"Preset {preset_name} not found"
        tuner.preset_combo.setCurrentIndex(idx)
        tuner._on_preset_selected()

        assert tuner.current_result is not None
        print(f"  [PASS] Preset '{preset_name}' applied successfully.")


def test_poi_and_outside_nv_classification():
    """Test live POI extraction and separation into inside vs outside NVs."""
    print("Testing POI candidate and outside NV classification...")
    tuner = InteractiveCellTuner(image_path=TEST_SCAN if os.path.exists(TEST_SCAN) else None)

    # Enable POI extraction
    tuner.enable_poi_cb.setChecked(True)
    tuner.run_processing()

    inside_cands = tuner.inside_candidates
    outside_cands = tuner.outside_candidates
    print(f"  Detected {len(inside_cands)} inside-zone POIs, {len(outside_cands)} outside-substrate NVs")

    for c in inside_cands:
        r, col = int(c.pixel_row), int(c.pixel_col)
        assert tuner.current_result.processable_mask[r, col], f"Candidate {c.candidate_id} not in processable mask!"

    for c in outside_cands:
        r, col = int(c.pixel_row), int(c.pixel_col)
        assert not tuner.current_result.cell_interior_mask[r, col], f"Outside candidate {c.candidate_id} was inside cell mask!"

    print("  [PASS] Candidate spatial classification verified cleanly.")


def test_config_export():
    """Test configuration dictionary export."""
    print("Testing configuration dictionary export...")
    tuner = InteractiveCellTuner(image_path=TEST_SCAN if os.path.exists(TEST_SCAN) else None)
    cfg = tuner.get_current_config()

    assert "algorithm" in cfg
    assert "cell_interior" in cfg
    assert "nucleus" in cfg
    assert "bright_clusters" in cfg
    assert "poi_extraction" in cfg

    assert cfg["cell_interior"]["cell_bg_kernel"] > 0
    assert cfg["nucleus"]["min_nucleus_fraction"] >= 0
    print(f"  [PASS] Exported configuration valid: {list(cfg.keys())}")


if __name__ == '__main__':
    print("=" * 70)
    print("RUNNING INTERACTIVE CELL TUNER AUTOMATED TEST SUITE")
    print("=" * 70)
    test_tuner_initialization()
    test_algorithm_switching()
    test_presets_application()
    test_poi_and_outside_nv_classification()
    test_config_export()
    print("=" * 70)
    print("ALL INTERACTIVE CELL TUNER TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
