# -*- coding: utf-8 -*-
"""
Unit tests for MultiScaleAutoNVFinderWidget.

Tests that the GUI widget instantiates correctly, creates its layout programmatically, 
and syncs parameters with the logic module.

Run with: python -m pytest tests/test_multi_scale_auto_nv_finder_gui.py -v
"""

import sys
import os
import pytest
from unittest.mock import MagicMock
import numpy as np

import qtpy
from qtpy import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

# Add the GUI directory to the path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'gui', 'poimanager'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from multi_scale_auto_nv_finder_widget import MultiScaleAutoNVFinderWidget, RegionMarker


class MockMultiScaleLogic(QtCore.QObject):
    sigStateChanged = QtCore.Signal(str)
    sigMultiScaleComplete = QtCore.Signal(dict)
    sigLogMessage = QtCore.Signal(str)
    sigQueueUpdated = QtCore.Signal(int, int)
    sigVisualUpdate = QtCore.Signal(str, object)
    sigExperimentProgress = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.coarse_fov_um = 150.0
        self.bbox_margin_fraction = 0.2
        self.max_regions_per_run = 10
        self.target_cells = 5
        self.target_nvs_per_cell = 3
        self.enable_pulsed_measurement = False
        self.measurement_ensemble_name = 'test_meas'
        self.laser_pulse_ensemble_name = 'test_laser'
        self.poi_non_repetition_radius_m = 1.0e-6
        self.min_fluorescence_counts_per_s = 50e3
        self.max_fluorescence_counts_per_s = 8e6

        self.start_multi_scale_find = MagicMock()
        self.stop_multi_scale_find = MagicMock()


@pytest.fixture
def mock_logic():
    return MockMultiScaleLogic()


class TestMultiScaleGUI:

    @pytest.fixture(scope="class")
    def app(self):
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication(sys.argv)
        return app

    @pytest.fixture(scope="class")
    def widget(self, app):
        logic = MockMultiScaleLogic()
        w = MultiScaleAutoNVFinderWidget(logic, view_widget=MagicMock())
        yield w

    def test_initialization(self, widget):
        assert widget._logic is not None
        assert widget.fov_spinbox.value() == 150.0
        assert widget.margin_spinbox.value() == 0.2
        assert widget.max_regions_spinbox.value() == 10
        assert widget.target_cells_spinbox.value() == 5
        assert widget.nvs_per_cell_spinbox.value() == 3
        assert widget.measurement_name_edit.text() == 'test_meas'
        assert widget.laser_pulse_name_edit.text() == 'test_laser'
        assert widget.poi_radius_spinbox.value() == 1.0
        assert widget.min_fluorescence_spinbox.value() == 50.0   # 50 kc/s
        assert widget.max_fluorescence_spinbox.value() == 8.0    # 8 Mc/s

    def test_gui_controls_call_logic(self, widget):
        # Trigger the slots
        widget._on_start_clicked()
        widget._logic.start_multi_scale_find.assert_called_once()
        
        widget._on_stop_clicked()
        widget._logic.stop_multi_scale_find.assert_called_once()

    def test_experiment_progress_update(self, widget):
        progress = {
            'cells_completed': 2,
            'target_cells': 5,
            'nvs_this_cell': 1,
            'target_nvs_per_cell': 3,
            'total_nvs_measured': 4,
        }
        widget._update_experiment_progress(progress)
        assert widget.cells_progress_label.text() == '2 / 5'
        assert widget.nvs_cell_progress_label.text() == '1 / 3'
        assert widget.total_nvs_label.text() == '4'

    def test_log_streaming_and_clearing(self, widget):
        widget.log_textedit.clear()
        widget._append_log("Test log entry 1")
        widget._append_log("Test log entry 2")
        assert "Test log entry 1" in widget.log_textedit.toPlainText()
        assert "Test log entry 2" in widget.log_textedit.toPlainText()
        
        widget.clear_logs_btn.click()
        assert widget.log_textedit.toPlainText() == ""

    def test_overlay_clears(self, widget):
        marker1 = MagicMock()
        marker2 = MagicMock()
        widget._region_markers = {'R1': marker1, 'R2': marker2}
        
        widget._clear_markers()
        
        assert len(widget._region_markers) == 0
        marker1.remove_from_view.assert_called_once()
        marker2.remove_from_view.assert_called_once()

    def test_visual_update_slot(self, widget):
        fake_array = np.zeros((10, 10))
        widget.image_view.setImage = MagicMock()
        widget.tabs.setCurrentWidget = MagicMock()
        
        # Default: auto-switch is unchecked, so setCurrentWidget shouldn't be called
        widget._on_visual_update('TestMask', fake_array)
        assert widget.visuals_label.text() == 'Visual: TestMask'
        widget.image_view.setImage.assert_called()
        widget.tabs.setCurrentWidget.assert_not_called()
        
        # Enable auto-switch
        widget.auto_switch_visuals_cb.setChecked(True)
        widget._on_visual_update('TestMask2', fake_array)
        widget.tabs.setCurrentWidget.assert_called_with(widget.visuals_widget)
