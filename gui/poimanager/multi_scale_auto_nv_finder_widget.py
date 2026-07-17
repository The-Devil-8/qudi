# -*- coding: utf-8 -*-
"""
Multi-Scale Auto NV Finder GUI Widget

Integrates with the POI Manager to visualize the automated coarse-to-fine 
zoom loop, displaying queued regions, progress, and processing steps.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets, QtGui
import time

class RegionMarker(pg.RectROI):
    """Marker for a queued or processed scan region on the macro image."""
    
    STATUS_PENS = {
        'queued': {'color': 'FF0', 'width': 2},      # Yellow
        'scanning': {'color': '00F', 'width': 3},    # Blue
        'processed': {'color': '0F0', 'width': 2},   # Green
        'skipped': {'color': '888', 'width': 1},     # Gray
    }

    def __init__(self, region_id, pos, size, status='queued', view_widget=None, **kwargs):
        self.region_id = region_id
        self._view_widget = view_widget
        self._status = status
        
        pen = self.STATUS_PENS.get(status, self.STATUS_PENS['queued'])
        
        super().__init__(pos=pos, size=size, pen=pen, movable=False, removable=False, **kwargs)
        
        self.label = pg.TextItem(text=region_id, anchor=(0, 1), color=pen['color'])
        self.label.setPos(pos[0], pos[1] + size[1])
        
        self.setZValue(-1)
        self.label.setZValue(-1)

    def _addHandles(self):
        pass

    def set_status(self, status):
        self._status = status
        pen = self.STATUS_PENS.get(status, self.STATUS_PENS['queued'])
        self.setPen(pen)
        self.label.setColor(pen['color'])

    def add_to_view(self):
        if self._view_widget:
            self._view_widget.addItem(self)
            self._view_widget.addItem(self.label)

    def remove_from_view(self):
        if self._view_widget:
            self._view_widget.removeItem(self.label)
            self._view_widget.removeItem(self)


class MultiScaleAutoNVFinderWidget(QtWidgets.QDockWidget):
    """Dock widget for controlling and visualizing the multi-scale finder."""

    def __init__(self, multi_scale_logic, view_widget, parent=None):
        super().__init__('Multi-Scale Auto NV Finder', parent)

        self._logic = multi_scale_logic
        self._view_widget = view_widget
        self._region_markers = {}

        self._setup_ui()
        self._sync_params_from_logic()
        self._connect_signals()

    def _setup_ui(self):
        # Main widget and layout
        self.main_widget = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(self.main_widget)
        self.setWidget(self.main_widget)

        # --- Controls ---
        controls_layout = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("▶ Start Multi-Scale")
        self.stop_btn = QtWidgets.QPushButton("⏹ Stop")
        self.stop_btn.setEnabled(False)
        controls_layout.addWidget(self.start_btn)
        controls_layout.addWidget(self.stop_btn)
        self.main_layout.addLayout(controls_layout)

        # --- State & Progress ---
        progress_layout = QtWidgets.QHBoxLayout()
        self.state_label = QtWidgets.QLabel("State: Idle")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.state_label)
        progress_layout.addWidget(self.progress_bar)
        self.main_layout.addLayout(progress_layout)

        # --- Parameters ---
        param_group = QtWidgets.QGroupBox("Settings")
        param_layout = QtWidgets.QFormLayout(param_group)
        
        self.fov_spinbox = QtWidgets.QDoubleSpinBox()
        self.fov_spinbox.setRange(10.0, 500.0)
        self.fov_spinbox.setSuffix(" μm")
        
        self.margin_spinbox = QtWidgets.QDoubleSpinBox()
        self.margin_spinbox.setRange(0.0, 1.0)
        self.margin_spinbox.setSingleStep(0.05)
        
        self.max_regions_spinbox = QtWidgets.QSpinBox()
        self.max_regions_spinbox.setRange(1, 100)
        
        param_layout.addRow("Macro FOV:", self.fov_spinbox)
        param_layout.addRow("Micro Margin:", self.margin_spinbox)
        param_layout.addRow("Max Regions:", self.max_regions_spinbox)
        self.main_layout.addWidget(param_group)

        # --- Tabs for Log and Visuals ---
        self.tabs = QtWidgets.QTabWidget()
        
        # Log Tab
        self.log_textedit = QtWidgets.QTextEdit()
        self.log_textedit.setReadOnly(True)
        self.tabs.addTab(self.log_textedit, "Logs")
        
        # Visuals Tab
        self.visuals_widget = QtWidgets.QWidget()
        self.visuals_layout = QtWidgets.QVBoxLayout(self.visuals_widget)
        
        self.visuals_label = QtWidgets.QLabel("Waiting for micro-scan visuals...")
        self.visuals_layout.addWidget(self.visuals_label)
        
        self.image_view = pg.ImageView()
        # Disable ROI and Menu in ImageView to keep it clean
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        self.visuals_layout.addWidget(self.image_view)
        
        self.tabs.addTab(self.visuals_widget, "Intermediate Visuals")
        
        self.main_layout.addWidget(self.tabs)

    def _sync_params_from_logic(self):
        self.fov_spinbox.setValue(float(self._logic.coarse_fov_um))
        self.margin_spinbox.setValue(float(self._logic.bbox_margin_fraction))
        self.max_regions_spinbox.setValue(int(self._logic.max_regions_per_run))

    def _connect_signals(self):
        # GUI -> Logic
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.fov_spinbox.valueChanged.connect(lambda v: setattr(self._logic, 'coarse_fov_um', v))
        self.margin_spinbox.valueChanged.connect(lambda v: setattr(self._logic, 'bbox_margin_fraction', v))
        self.max_regions_spinbox.valueChanged.connect(lambda v: setattr(self._logic, 'max_regions_per_run', v))

        # Logic -> GUI
        self._logic.sigStateChanged.connect(self._update_state, QtCore.Qt.QueuedConnection)
        self._logic.sigMultiScaleComplete.connect(self._on_complete, QtCore.Qt.QueuedConnection)
        self._logic.sigLogMessage.connect(self._append_log, QtCore.Qt.QueuedConnection)
        self._logic.sigQueueUpdated.connect(self._update_progress, QtCore.Qt.QueuedConnection)
        self._logic.sigVisualUpdate.connect(self._on_visual_update, QtCore.Qt.QueuedConnection)

    # --- Slots ---

    @QtCore.Slot()
    def _on_start_clicked(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log_textedit.clear()
        self._clear_markers()
        self._logic.start_multi_scale_find()

    @QtCore.Slot()
    def _on_stop_clicked(self):
        self._logic.stop_multi_scale_find()
        self.stop_btn.setEnabled(False)

    @QtCore.Slot(str)
    def _update_state(self, state):
        self.state_label.setText(f"State: {state.replace('_', ' ').title()}")
        if state == 'idle':
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    @QtCore.Slot(int, int)
    def _update_progress(self, processed, total):
        if total > 0:
            self.progress_bar.setMaximum(total)
        else:
            self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(processed)
        self._sync_regions_to_overlay()

    @QtCore.Slot(dict)
    def _on_complete(self, stats):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._update_state('idle')
        self.state_label.setText('State: Complete')
        self._sync_regions_to_overlay()

    @QtCore.Slot(str)
    def _append_log(self, message):
        self.log_textedit.append(message)
        scrollbar = self.log_textedit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @QtCore.Slot(str, object)
    def _on_visual_update(self, name, array_data):
        self.visuals_label.setText(f"Visual: {name}")
        # pyqtgraph ImageView expects image array. Adjust shape if needed.
        # numpy arrays might need rotation for correct display in pg (transpose)
        if isinstance(array_data, np.ndarray):
            self.image_view.setImage(array_data.T, autoRange=True, autoLevels=True)
            self.tabs.setCurrentWidget(self.visuals_widget)

    # --- Overlay Helpers ---

    def _sync_regions_to_overlay(self):
        # We fetch the queue from logic if possible to draw regions
        if not hasattr(self._logic, '_queue') or self._logic._queue is None:
            return
            
        queue = self._logic._queue
        for region in queue.regions:
            rid = region.region_id
            status = getattr(region, 'status', 'queued')
            
            if rid not in self._region_markers:
                # Use bbox_physical (x_min, x_max, y_min, y_max) for precise positioning
                x_min, x_max, y_min, y_max = region.bbox_physical
                pos = (x_min, y_min)
                size = (x_max - x_min, y_max - y_min)
                
                marker = RegionMarker(rid, pos, size, status=status, view_widget=self._view_widget)
                marker.add_to_view()
                self._region_markers[rid] = marker
            else:
                self._region_markers[rid].set_status(status)

    def _clear_markers(self):
        for marker in self._region_markers.values():
            marker.remove_from_view()
        self._region_markers.clear()

    def cleanup(self):
        self._clear_markers()
