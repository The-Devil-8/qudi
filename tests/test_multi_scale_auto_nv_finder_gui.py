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

import qtpy
from qtpy import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

# Add the GUI directory to the path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'gui', 'poimanager'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from multi_scale_auto_nv_finder_widget import MultiScaleAutoNVFinderWidget, RegionMarker

@pytest.fixture
def mock_logic():
    logic = MagicMock()
    logic.coarse_fov_um = 150.0
    logic.bbox_margin_fraction = 0.2
    logic.max_regions_per_run = 10
    logic.sigStateChanged = MagicMock()
    logic.sigMultiScaleComplete = MagicMock()
    logic.sigLogMessage = MagicMock()
    logic.sigQueueUpdated = MagicMock()
    logic.sigVisualUpdate = MagicMock()
    return logic

class TestMultiScaleGUI:

    @pytest.fixture
    def app(self):
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication(sys.argv)
        return app

    @pytest.fixture
    def widget(self, app, mock_logic):
        w = MultiScaleAutoNVFinderWidget(mock_logic, view_widget=pg.ViewBox())
        yield w

    def test_initialization(self, widget, mock_logic):
        assert widget._logic == mock_logic
        assert widget.fov_spinbox.value() == 150.0
        assert widget.margin_spinbox.value() == 0.2
        assert widget.max_regions_spinbox.value() == 10

    def test_gui_controls_call_logic(self, widget, mock_logic):
        # Trigger the slots
        widget._on_start_clicked()
        mock_logic.start_multi_scale_find.assert_called_once()
        
        widget._on_stop_clicked()
        mock_logic.stop_multi_scale_find.assert_called_once()

    def test_overlay_clears(self, widget):
        # Mock some region markers
        marker1 = MagicMock()
        marker2 = MagicMock()
        widget._region_markers = {'R1': marker1, 'R2': marker2}
        
        widget._clear_markers()
        
        assert len(widget._region_markers) == 0
        marker1.remove_from_view.assert_called_once()
        marker2.remove_from_view.assert_called_once()

    def test_visual_update_slot(self, widget):
        import numpy as np
        fake_array = np.zeros((10, 10))
        widget.image_view.setImage = MagicMock()
        widget.tabs.setCurrentWidget = MagicMock()
        widget._on_visual_update('TestMask', fake_array)
        
        # Verify the label updated and the image was set
        assert widget.visuals_label.text() == 'Visual: TestMask'
        widget.image_view.setImage.assert_called()
        widget.tabs.setCurrentWidget.assert_called_with(widget.visuals_widget)
