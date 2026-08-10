# -*- coding: utf-8 -*-
"""
Multi-Scale Auto NV Finder GUI Widget

Integrates with the POI Manager to visualize the automated coarse-to-fine 
zoom loop, displaying queued regions, progress, real-time streaming logs,
intermediate visuals, and configurable settings in a clean tabbed interface.

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
        self.main_widget = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(6)
        self.setWidget(self.main_widget)

        # =====================================================================
        # 1. Top Section: Run Controls & State
        # =====================================================================
        controls_layout = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("▶ Start Multi-Scale")
        self.start_btn.setStyleSheet("font-weight: bold;")
        self.stop_btn = QtWidgets.QPushButton("⏹ Stop")
        self.stop_btn.setEnabled(False)
        controls_layout.addWidget(self.start_btn)
        controls_layout.addWidget(self.stop_btn)
        self.main_layout.addLayout(controls_layout)

        # State & Progress Bar
        state_layout = QtWidgets.QHBoxLayout()
        self.state_label = QtWidgets.QLabel("State: Idle")
        self.state_label.setStyleSheet("font-weight: bold; color: #2a82da;")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        state_layout.addWidget(self.state_label)
        state_layout.addWidget(self.progress_bar)
        self.main_layout.addLayout(state_layout)

        # =====================================================================
        # 2. Experiment Progress Summary Box
        # =====================================================================
        progress_group = QtWidgets.QGroupBox("Experiment Progress")
        progress_grid = QtWidgets.QGridLayout(progress_group)
        progress_grid.setContentsMargins(8, 6, 8, 6)
        progress_grid.setHorizontalSpacing(12)

        lbl_cells = QtWidgets.QLabel("Cells Completed:")
        lbl_cells.setStyleSheet("color: #888;")
        self.cells_progress_label = QtWidgets.QLabel("0 / 0")
        self.cells_progress_label.setStyleSheet("font-weight: bold; font-size: 13px;")

        lbl_nvs_cell = QtWidgets.QLabel("NVs (This Cell):")
        lbl_nvs_cell.setStyleSheet("color: #888;")
        self.nvs_cell_progress_label = QtWidgets.QLabel("0 / 0")
        self.nvs_cell_progress_label.setStyleSheet("font-weight: bold; font-size: 13px;")

        lbl_total_nvs = QtWidgets.QLabel("Total NVs Measured:")
        lbl_total_nvs.setStyleSheet("color: #888;")
        self.total_nvs_label = QtWidgets.QLabel("0")
        self.total_nvs_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #27ae60;")

        progress_grid.addWidget(lbl_cells, 0, 0)
        progress_grid.addWidget(self.cells_progress_label, 0, 1)
        progress_grid.addWidget(lbl_nvs_cell, 1, 0)
        progress_grid.addWidget(self.nvs_cell_progress_label, 1, 1)
        progress_grid.addWidget(lbl_total_nvs, 2, 0)
        progress_grid.addWidget(self.total_nvs_label, 2, 1)

        self.main_layout.addWidget(progress_group)

        # =====================================================================
        # 3. Main Tabs (Logs, Intermediate Visuals, Settings)
        # =====================================================================
        self.tabs = QtWidgets.QTabWidget()
        self.main_layout.addWidget(self.tabs, stretch=1)

        # --- Tab 1: Logs ---
        self.logs_tab = QtWidgets.QWidget()
        logs_layout = QtWidgets.QVBoxLayout(self.logs_tab)
        logs_layout.setContentsMargins(4, 4, 4, 4)

        logs_header_layout = QtWidgets.QHBoxLayout()
        self.clear_logs_btn = QtWidgets.QPushButton("Clear Logs")
        self.clear_logs_btn.setMaximumWidth(90)
        self.autoscroll_checkbox = QtWidgets.QCheckBox("Auto-scroll")
        self.autoscroll_checkbox.setChecked(True)
        logs_header_layout.addWidget(self.autoscroll_checkbox)
        logs_header_layout.addStretch()
        logs_header_layout.addWidget(self.clear_logs_btn)
        logs_layout.addLayout(logs_header_layout)

        self.log_textedit = QtWidgets.QTextEdit()
        self.log_textedit.setReadOnly(True)
        self.log_textedit.setFontFamily("Consolas, Courier New, monospace")
        logs_layout.addWidget(self.log_textedit)
        self.tabs.addTab(self.logs_tab, "Logs")

        # --- Tab 2: Intermediate Visuals ---
        self.visuals_widget = QtWidgets.QWidget()
        self.visuals_layout = QtWidgets.QVBoxLayout(self.visuals_widget)
        self.visuals_layout.setContentsMargins(4, 4, 4, 4)

        visuals_header_layout = QtWidgets.QHBoxLayout()
        self.visuals_label = QtWidgets.QLabel("Waiting for scan visuals...")
        self.visuals_label.setStyleSheet("font-weight: bold;")
        self.auto_switch_visuals_cb = QtWidgets.QCheckBox("Auto-switch tab")
        self.auto_switch_visuals_cb.setChecked(False)
        self.auto_switch_visuals_cb.setToolTip(
            "Automatically switch to Visuals tab when a new intermediate image arrives")
        visuals_header_layout.addWidget(self.visuals_label)
        visuals_header_layout.addStretch()
        visuals_header_layout.addWidget(self.auto_switch_visuals_cb)
        self.visuals_layout.addLayout(visuals_header_layout)

        self.image_view = pg.ImageView()
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        self.visuals_layout.addWidget(self.image_view)
        self.tabs.addTab(self.visuals_widget, "Intermediate Visuals")

        # --- Tab 3: Settings ---
        self.settings_tab = QtWidgets.QWidget()
        settings_scroll = QtWidgets.QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_container = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_container)
        settings_layout.setContentsMargins(6, 6, 6, 6)

        # Scan Parameters Group
        scan_group = QtWidgets.QGroupBox("Scan Parameters")
        scan_form = QtWidgets.QFormLayout(scan_group)

        self.fov_spinbox = QtWidgets.QDoubleSpinBox()
        self.fov_spinbox.setRange(10.0, 500.0)
        self.fov_spinbox.setSuffix(" μm")
        scan_form.addRow("Macro FOV:", self.fov_spinbox)

        self.margin_spinbox = QtWidgets.QDoubleSpinBox()
        self.margin_spinbox.setRange(0.0, 1.0)
        self.margin_spinbox.setSingleStep(0.05)
        scan_form.addRow("Micro Margin:", self.margin_spinbox)

        self.max_regions_spinbox = QtWidgets.QSpinBox()
        self.max_regions_spinbox.setRange(1, 100)
        scan_form.addRow("Max Regions:", self.max_regions_spinbox)
        settings_layout.addWidget(scan_group)

        # Experiment Loop Group
        loop_group = QtWidgets.QGroupBox("Experiment Loop")
        loop_form = QtWidgets.QFormLayout(loop_group)

        self.target_cells_spinbox = QtWidgets.QSpinBox()
        self.target_cells_spinbox.setRange(1, 100)
        self.target_cells_spinbox.setToolTip('Number of cell ROIs to analyze')
        loop_form.addRow('No. of cells:', self.target_cells_spinbox)

        self.nvs_per_cell_spinbox = QtWidgets.QSpinBox()
        self.nvs_per_cell_spinbox.setRange(1, 20)
        self.nvs_per_cell_spinbox.setToolTip('Target NVs to measure per cell')
        loop_form.addRow('NVs per cell:', self.nvs_per_cell_spinbox)

        self.poi_radius_spinbox = QtWidgets.QDoubleSpinBox()
        self.poi_radius_spinbox.setRange(0.1, 10.0)
        self.poi_radius_spinbox.setDecimals(1)
        self.poi_radius_spinbox.setSuffix(' \u00b5m')
        self.poi_radius_spinbox.setToolTip(
            'POI non-repetition radius: candidates within this distance '
            'of previously measured NVs are filtered out')
        loop_form.addRow('POI non-repetition radius:', self.poi_radius_spinbox)

        self.enable_pulsed_checkbox = QtWidgets.QCheckBox()
        self.enable_pulsed_checkbox.setToolTip(
            'Enable T1/ODMR measurement after each verified NV')
        loop_form.addRow('Enable pulsed measurement:', self.enable_pulsed_checkbox)

        self.measurement_name_edit = QtWidgets.QLineEdit()
        self.measurement_name_edit.setPlaceholderText('e.g. T1_measurement')
        self.measurement_name_edit.setToolTip(
            'PulsedMasterLogic ensemble name for T1/ODMR')
        loop_form.addRow('Measurement ensemble:', self.measurement_name_edit)

        self.laser_pulse_name_edit = QtWidgets.QLineEdit()
        self.laser_pulse_name_edit.setPlaceholderText('e.g. laser_pulse_532nm')
        self.laser_pulse_name_edit.setToolTip('Ensemble name for laser re-pump pulse')
        loop_form.addRow('Laser pulse ensemble:', self.laser_pulse_name_edit)

        settings_layout.addWidget(loop_group)
        settings_layout.addStretch()

        settings_scroll.setWidget(settings_container)
        settings_tab_layout = QtWidgets.QVBoxLayout(self.settings_tab)
        settings_tab_layout.setContentsMargins(0, 0, 0, 0)
        settings_tab_layout.addWidget(settings_scroll)
        self.tabs.addTab(self.settings_tab, "Settings")

    def _sync_params_from_logic(self):
        val = getattr(self._logic, '_val', lambda v, d: v if isinstance(v, (int, float, str, bool)) else getattr(v, 'default', d))
        self.fov_spinbox.setValue(float(val(self._logic.coarse_fov_um, 200.0)))
        self.margin_spinbox.setValue(float(val(self._logic.bbox_margin_fraction, 0.15)))
        self.max_regions_spinbox.setValue(int(val(self._logic.max_regions_per_run, 10)))
        
        # Experiment loop parameters
        self.target_cells_spinbox.setValue(int(val(self._logic.target_cells, 5)))
        self.nvs_per_cell_spinbox.setValue(int(val(self._logic.target_nvs_per_cell, 3)))
        self.enable_pulsed_checkbox.setChecked(bool(val(self._logic.enable_pulsed_measurement, False)))
        self.measurement_name_edit.setText(str(val(self._logic.measurement_ensemble_name, '')))
        self.laser_pulse_name_edit.setText(str(val(self._logic.laser_pulse_ensemble_name, '')))
        self.poi_radius_spinbox.setValue(float(val(self._logic.poi_non_repetition_radius_m, 1.0e-6)) * 1e6)

    def _connect_signals(self):
        # GUI -> Logic
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.clear_logs_btn.clicked.connect(self.log_textedit.clear)

        self.fov_spinbox.valueChanged.connect(lambda v: setattr(self._logic, 'coarse_fov_um', v))
        self.margin_spinbox.valueChanged.connect(lambda v: setattr(self._logic, 'bbox_margin_fraction', v))
        self.max_regions_spinbox.valueChanged.connect(lambda v: setattr(self._logic, 'max_regions_per_run', v))

        self.target_cells_spinbox.valueChanged.connect(
            lambda v: setattr(self._logic, 'target_cells', v))
        self.nvs_per_cell_spinbox.valueChanged.connect(
            lambda v: setattr(self._logic, 'target_nvs_per_cell', v))
        self.enable_pulsed_checkbox.toggled.connect(
            lambda v: setattr(self._logic, 'enable_pulsed_measurement', v))
        self.measurement_name_edit.textChanged.connect(
            lambda v: setattr(self._logic, 'measurement_ensemble_name', v))
        self.laser_pulse_name_edit.textChanged.connect(
            lambda v: setattr(self._logic, 'laser_pulse_ensemble_name', v))
        self.poi_radius_spinbox.valueChanged.connect(
            lambda v: setattr(self._logic, 'poi_non_repetition_radius_m', v * 1e-6))

        # Logic -> GUI
        self._logic.sigStateChanged.connect(self._update_state, QtCore.Qt.QueuedConnection)
        self._logic.sigMultiScaleComplete.connect(self._on_complete, QtCore.Qt.QueuedConnection)
        self._logic.sigLogMessage.connect(self._append_log, QtCore.Qt.QueuedConnection)
        self._logic.sigQueueUpdated.connect(self._update_progress, QtCore.Qt.QueuedConnection)
        self._logic.sigVisualUpdate.connect(self._on_visual_update, QtCore.Qt.QueuedConnection)
        self._logic.sigExperimentProgress.connect(
            self._update_experiment_progress, QtCore.Qt.QueuedConnection)

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
        self.state_label.setText('State: {0}'.format(state.replace('_', ' ').title()))
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
        self.state_label.setText('State: Complete ({0} cells, {1} NVs)'.format(
            stats.get('cells_completed', 0),
            stats.get('total_nvs_measured', 0)))
        self._sync_regions_to_overlay()

    @QtCore.Slot(object)
    def _update_experiment_progress(self, progress):
        """Update experiment progress labels from sigExperimentProgress."""
        self.cells_progress_label.setText('{0} / {1}'.format(
            progress.get('cells_completed', 0),
            progress.get('target_cells', 0)))
        self.nvs_cell_progress_label.setText('{0} / {1}'.format(
            progress.get('nvs_this_cell', 0),
            progress.get('target_nvs_per_cell', 0)))
        self.total_nvs_label.setText(str(
            progress.get('total_nvs_measured', 0)))

    @QtCore.Slot(str)
    def _append_log(self, message):
        self.log_textedit.append(message)
        if self.autoscroll_checkbox.isChecked():
            scrollbar = self.log_textedit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    @QtCore.Slot(str, object)
    def _on_visual_update(self, name, array_data):
        if name.startswith('Macro Crop') or name.startswith('Macro Scan Queue'):
            return  # Handled by other dedicated widgets
            
        self.visuals_label.setText('Visual: {0}'.format(name))
        
        # Handle new dict format with x/y coords
        if isinstance(array_data, dict):
            image = array_data.get('image_data')
            if image is not None and isinstance(image, np.ndarray):
                self.image_view.setImage(image.T, autoRange=True, autoLevels=True)
                if self.auto_switch_visuals_cb.isChecked():
                    self.tabs.setCurrentWidget(self.visuals_widget)
                    
        # Handle legacy numpy array format
        elif isinstance(array_data, np.ndarray):
            self.image_view.setImage(array_data.T, autoRange=True, autoLevels=True)
            if self.auto_switch_visuals_cb.isChecked():
                self.tabs.setCurrentWidget(self.visuals_widget)

    # --- Overlay Helpers ---

    def _sync_regions_to_overlay(self):
        if not hasattr(self._logic, '_queue') or self._logic._queue is None:
            return
            
        queue = self._logic._queue
        for region in queue.regions:
            rid = region.region_id
            status = getattr(region, 'status', 'queued')
            
            if rid not in self._region_markers:
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
