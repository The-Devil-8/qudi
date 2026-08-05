# -*- coding: utf-8 -*-

"""
Auto NV Finder dock widget for the POI Manager GUI.

This widget provides a user interface for controlling the automated NV center
detection pipeline using CIP (Color Image Processing) techniques. It integrates
as a dock widget in the POI Manager main window.

See documentation/automation/09_gui_integration.md for design details.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import os
import numpy as np
import pyqtgraph as pg

from qtpy import QtCore, QtGui, QtWidgets, uic


class CandidateMarker(pg.EllipseROI):
    """Visual marker for an NV candidate on the confocal color image.

    Displayed as a colored circle whose color indicates the candidate's status:
    - Yellow:  pending (not yet optimized)
    - Blue:    currently being optimized
    - Green:   accepted (optimization passed)
    - Red:     rejected (optimization failed)
    """

    STATUS_PENS = {
        'pending':    {'color': 'FF0', 'width': 2},    # Yellow
        'optimizing': {'color': '00F', 'width': 3},    # Blue (thicker)
        'accepted':   {'color': '0F0', 'width': 2},    # Green
        'rejected':   {'color': 'F00', 'width': 1},    # Red (thinner)
        'skipped':    {'color': '888', 'width': 1},    # Gray
    }

    def __init__(self, position, radius, name='', status='pending',
                 view_widget=None, **kwargs):
        """
        @param tuple position: (x, y) physical position in meters
        @param float radius: marker radius in physical units
        @param str name: candidate name/label
        @param str status: initial status
        @param view_widget: pyqtgraph ViewBox to add to
        """
        self._name = name
        self._view_widget = view_widget
        self._position = np.array(position[:2], dtype=float)
        self._status = status

        pen = self.STATUS_PENS.get(status, self.STATUS_PENS['pending'])
        size = (2 * radius, 2 * radius)

        super().__init__(
            pos=(self._position[0] - radius, self._position[1] - radius),
            size=size, pen=pen, movable=False, removable=False, **kwargs)

        self.label = pg.TextItem(text=name, anchor=(0, 1), color=pen['color'])
        label_offset = radius / np.sqrt(2)
        self.label.setPos(self._position[0] + label_offset,
                          self._position[1] + label_offset)

        # Draw behind POI markers
        self.setZValue(-1)
        self.label.setZValue(-1)

    def _addHandles(self):
        """Override to prevent drag handles from appearing."""
        pass

    def set_status(self, status):
        """Update the marker appearance based on candidate status.

        @param str status: one of 'pending', 'optimizing', 'accepted', 'rejected', 'skipped'
        """
        self._status = status
        pen = self.STATUS_PENS.get(status, self.STATUS_PENS['pending'])
        self.setPen(pen)
        self.label.setColor(pen['color'])

    def add_to_view(self, view_widget=None):
        """Add this marker and its label to a pyqtgraph view widget."""
        if view_widget is not None:
            self._view_widget = view_widget
        if self._view_widget is not None:
            self._view_widget.addItem(self)
            self._view_widget.addItem(self.label)

    def remove_from_view(self):
        """Remove this marker and its label from the view widget."""
        if self._view_widget is not None:
            self._view_widget.removeItem(self.label)
            self._view_widget.removeItem(self)


class AutoNVFinderWidget(QtWidgets.QDockWidget):
    """Dock widget for controlling the automated NV center finder.

    This widget connects to AutoNVFinderLogic and provides:
    - Start/Stop controls
    - Detection parameter adjustment (threshold, min intensity, spot diameter)
    - Progress bar
    - Candidate table with status icons
    - Real-time log output
    - Color-coded candidate markers on the confocal image
    """

    def __init__(self, auto_nv_finder_logic, view_widget, poi_diameter=0.5e-6,
                 parent=None):
        """
        @param auto_nv_finder_logic: reference to AutoNVFinderLogic instance
        @param view_widget: pyqtgraph ViewWidget for overlaying candidate markers
        @param float poi_diameter: marker display size in physical units
        @param parent: parent QWidget
        """
        super().__init__('Auto NV Finder', parent)

        self._logic = auto_nv_finder_logic
        self._view_widget = view_widget
        self._poi_diameter = poi_diameter
        self._candidate_markers = []

        # Load UI
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_auto_nv_finder.ui')
        self._ui = QtWidgets.QDockWidget()
        uic.loadUi(ui_file, self._ui)

        # Extract widgets from loaded UI
        self.setWidget(self._ui.findChild(QtWidgets.QWidget, 'dockWidgetContents'))

        # Get references to UI elements
        self.start_button = self.findChild(QtWidgets.QPushButton,
                                                'start_auto_find_PushButton')
        self.stop_button = self.findChild(QtWidgets.QPushButton,
                                               'stop_auto_find_PushButton')
        self.state_label = self.findChild(QtWidgets.QLabel, 'state_Label')
        self.progress_bar = self.findChild(QtWidgets.QProgressBar,
                                                'progress_ProgressBar')
        self.threshold_spinbox = self.findChild(QtWidgets.QDoubleSpinBox,
                                                     'threshold_sigma_DoubleSpinBox')
        self.min_intensity_spinbox = self.findChild(QtWidgets.QSpinBox,
                                                         'min_intensity_SpinBox')
        self.spot_diameter_spinbox = self.findChild(QtWidgets.QDoubleSpinBox,
                                                         'spot_diameter_DoubleSpinBox')
        self.auto_register_checkbox = self.findChild(QtWidgets.QCheckBox,
                                                          'auto_register_CheckBox')
        self.z_optimization_checkbox = self.findChild(QtWidgets.QCheckBox,
                                                           'z_optimization_CheckBox')
        self.candidates_table = self.findChild(QtWidgets.QTableWidget,
                                                    'candidates_TableWidget')
        self.log_textedit = self.findChild(QtWidgets.QTextEdit, 'log_TextEdit')

        # Initialize from logic state
        self._sync_params_from_logic()

        # Connect GUI → Logic signals
        self.start_button.clicked.connect(self._on_start_clicked)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.threshold_spinbox.valueChanged.connect(self._on_threshold_changed)
        self.min_intensity_spinbox.valueChanged.connect(self._on_min_intensity_changed)
        self.spot_diameter_spinbox.valueChanged.connect(self._on_spot_diameter_changed)
        self.auto_register_checkbox.toggled.connect(self._on_auto_register_toggled)
        self.z_optimization_checkbox.toggled.connect(self._on_z_opt_toggled)
        self.candidates_table.cellClicked.connect(self._on_candidate_clicked)

        # Connect Logic → GUI signals
        self._logic.sigStateChanged.connect(self._update_state,
                                             QtCore.Qt.QueuedConnection)
        self._logic.sigCandidatesFound.connect(self._populate_candidates,
                                                QtCore.Qt.QueuedConnection)
        self._logic.sigCandidateUpdate.connect(self._update_candidate_row,
                                                QtCore.Qt.QueuedConnection)
        self._logic.sigProgressUpdate.connect(self._update_progress,
                                               QtCore.Qt.QueuedConnection)
        self._logic.sigAutoFindComplete.connect(self._on_complete,
                                                 QtCore.Qt.QueuedConnection)
        self._logic.sigLogMessage.connect(self._append_log,
                                           QtCore.Qt.QueuedConnection)

    def cleanup(self):
        """Remove all candidate markers from the view widget."""
        self._clear_markers()

    # =========================================================================
    #                   PARAMETER SYNC
    # =========================================================================

    def _sync_params_from_logic(self):
        """Initialize GUI widgets from logic parameter values."""
        self.threshold_spinbox.setValue(self._logic.detection_threshold_sigma)
        self.min_intensity_spinbox.setValue(int(self._logic.min_spot_intensity))
        self.spot_diameter_spinbox.setValue(self._logic.spot_diameter * 1e6)  # m → μm
        self.auto_register_checkbox.setChecked(self._logic.auto_register_poi)
        self.z_optimization_checkbox.setChecked(self._logic.enable_z_optimization)

    # =========================================================================
    #                   GUI → LOGIC HANDLERS
    # =========================================================================

    @QtCore.Slot()
    def _on_start_clicked(self):
        """Handle Start button click."""
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.candidates_table.setRowCount(0)
        self._clear_markers()
        self.log_textedit.clear()
        self.progress_bar.setValue(0)
        self._logic.start_auto_find()

    @QtCore.Slot()
    def _on_stop_clicked(self):
        """Handle Stop button click."""
        self._logic.stop_auto_find()
        self.stop_button.setEnabled(False)

    @QtCore.Slot(float)
    def _on_threshold_changed(self, value):
        self._logic.set_threshold(value)

    @QtCore.Slot(int)
    def _on_min_intensity_changed(self, value):
        self._logic.set_min_intensity(float(value))

    @QtCore.Slot(float)
    def _on_spot_diameter_changed(self, value):
        self._logic.set_spot_diameter(value * 1e-6)  # μm → m

    @QtCore.Slot(bool)
    def _on_auto_register_toggled(self, checked):
        self._logic.auto_register_poi = checked

    @QtCore.Slot(bool)
    def _on_z_opt_toggled(self, checked):
        self._logic.enable_z_optimization = checked

    @QtCore.Slot(int, int)
    def _on_candidate_clicked(self, row, column):
        """Handle click on a candidate table row — highlight its marker."""
        # Deselect all markers
        for marker in self._candidate_markers:
            if marker is not None:
                marker.set_status(marker._status)

        # Highlight selected
        if 0 <= row < len(self._candidate_markers):
            marker = self._candidate_markers[row]
            if marker is not None:
                marker.setPen({'color': 'FFF', 'width': 3})

    # =========================================================================
    #                   LOGIC → GUI HANDLERS
    # =========================================================================

    @QtCore.Slot(str)
    def _update_state(self, state):
        """Update the state label and button states."""
        state_display = {
            'idle': 'Idle',
            'scanning': '🔍 Scanning...',
            'detecting': '🎯 Detecting...',
            'optimizing': '⚙️ Optimizing...',
            'registering': '📌 Registering...',
        }.get(state, state.capitalize())

        self.state_label.setText(state_display)

        if state == 'idle':
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    @QtCore.Slot(list)
    def _populate_candidates(self, candidates_list):
        """Populate the candidate table and add markers to the image."""
        self._clear_markers()
        table = self.candidates_table
        table.setRowCount(len(candidates_list))

        marker_radius = self._poi_diameter / 2

        for i, cand in enumerate(candidates_list):
            # Table row
            table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))

            name = cand.get('poi_name', f'cand_{i+1:03d}')
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(name))

            x_um = cand.get('x', 0) * 1e6
            y_um = cand.get('y', 0) * 1e6
            pos_str = f'({x_um:.2f}, {y_um:.2f})'
            table.setItem(i, 2, QtWidgets.QTableWidgetItem(pos_str))

            intensity = cand.get('intensity', 0)
            table.setItem(i, 3, QtWidgets.QTableWidgetItem(f'{intensity:,.0f}'))

            status = cand.get('status', 'pending')
            status_icon = self._status_icon(status)
            table.setItem(i, 4, QtWidgets.QTableWidgetItem(status_icon))

            # Image marker
            position = (cand.get('x', 0), cand.get('y', 0))
            marker = CandidateMarker(
                position=position,
                radius=marker_radius,
                name=name,
                status=status,
                view_widget=self._view_widget
            )
            marker.add_to_view()
            self._candidate_markers.append(marker)

        table.resizeColumnsToContents()

    @QtCore.Slot(int, dict)
    def _update_candidate_row(self, index, candidate_dict):
        """Update a single candidate row and its marker."""
        table = self.candidates_table
        if index < 0 or index >= table.rowCount():
            return

        status = candidate_dict.get('status', 'pending')
        poi_name = candidate_dict.get('poi_name', '')

        # Update table
        if poi_name:
            table.setItem(index, 1, QtWidgets.QTableWidgetItem(poi_name))
        table.setItem(index, 4, QtWidgets.QTableWidgetItem(
            self._status_icon(status)))

        # Update marker color
        if index < len(self._candidate_markers) and self._candidate_markers[index] is not None:
            self._candidate_markers[index].set_status(status)
            if poi_name:
                self._candidate_markers[index].label.setText(poi_name)

    @QtCore.Slot(int, int)
    def _update_progress(self, current, total):
        """Update the progress bar."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    @QtCore.Slot(dict)
    def _on_complete(self, results):
        """Handle pipeline completion."""
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.state_label.setText('✅ Complete')

        accepted = results.get('accepted', 0)
        total = results.get('total_detected', 0)
        self._append_log(
            f'[DONE] {accepted}/{total} NV centers confirmed and registered.')

    @QtCore.Slot(str)
    def _append_log(self, message):
        """Append a message to the log panel."""
        self.log_textedit.append(message)
        # Auto-scroll to bottom
        scrollbar = self.log_textedit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # =========================================================================
    #                   HELPERS
    # =========================================================================

    def _clear_markers(self):
        """Remove all candidate markers from the image view."""
        for marker in self._candidate_markers:
            if marker is not None:
                marker.remove_from_view()
        self._candidate_markers.clear()


    @staticmethod
    def _status_icon(status):
        """Return a status string with icon for the table."""
        icons = {
            'pending':    '🟡 Pending',
            'optimizing': '🔵 Optimizing',
            'accepted':   '✅ Accepted',
            'rejected':   '❌ Rejected',
            'skipped':    '⏭️ Skipped',
            'measured':   '📊 Measured',
            'measuring':  '⏳ Measuring...',
        }
        return icons.get(status, status)


class ExperimentControlPanel(QtWidgets.QGroupBox):
    """Control panel for the full NV experiment loop parameters.

    Provides GUI controls for:
    - Target cells and NVs per cell
    - Measurement and laser pulse ensemble names
    - Z scan range and depth from surface
    - POI non-repetition radius
    - Pulsed measurement enable/disable
    - Real-time experiment progress display

    This panel is designed to be embedded alongside the existing
    AutoNVFinderWidget in the POI Manager GUI.

    See documentation/automation/24_full_experiment_loop.md for details.
    """

    def __init__(self, multi_scale_logic, parent=None):
        """
        Parameters
        ----------
        multi_scale_logic : MultiScaleAutoNVFinderLogic
            Reference to the orchestrator logic module.
        parent : QWidget, optional
            Parent widget.
        """
        super().__init__('Experiment Loop Control', parent)
        self._logic = multi_scale_logic
        self._build_ui()
        self._connect_signals()
        self._sync_from_logic()

    def _build_ui(self):
        """Construct the UI programmatically."""
        layout = QtWidgets.QVBoxLayout(self)

        # --- Target parameters group ---
        targets_group = QtWidgets.QGroupBox('Targets')
        targets_layout = QtWidgets.QFormLayout(targets_group)

        self.target_cells_spinbox = QtWidgets.QSpinBox()
        self.target_cells_spinbox.setRange(1, 100)
        self.target_cells_spinbox.setToolTip(
            'Number of cell ROIs to analyze')
        targets_layout.addRow('No. of cells:', self.target_cells_spinbox)

        self.nvs_per_cell_spinbox = QtWidgets.QSpinBox()
        self.nvs_per_cell_spinbox.setRange(1, 20)
        self.nvs_per_cell_spinbox.setToolTip(
            'Target number of NVs to measure per cell')
        targets_layout.addRow('NVs per cell:', self.nvs_per_cell_spinbox)

        layout.addWidget(targets_group)

        # --- Measurement parameters group ---
        measurement_group = QtWidgets.QGroupBox('Pulsed Measurement')
        measurement_layout = QtWidgets.QFormLayout(measurement_group)

        self.enable_pulsed_checkbox = QtWidgets.QCheckBox()
        self.enable_pulsed_checkbox.setToolTip(
            'Enable T1/ODMR measurement after each verified NV')
        measurement_layout.addRow(
            'Enable pulsed measurement:', self.enable_pulsed_checkbox)

        self.measurement_name_edit = QtWidgets.QLineEdit()
        self.measurement_name_edit.setPlaceholderText(
            'e.g. T1_measurement')
        self.measurement_name_edit.setToolTip(
            'Ensemble name for the T1/ODMR measurement')
        measurement_layout.addRow(
            'Measurement ensemble:', self.measurement_name_edit)

        self.laser_pulse_name_edit = QtWidgets.QLineEdit()
        self.laser_pulse_name_edit.setPlaceholderText(
            'e.g. laser_pulse_532nm')
        self.laser_pulse_name_edit.setToolTip(
            'Ensemble name for the laser re-pump pulse')
        measurement_layout.addRow(
            'Laser pulse ensemble:', self.laser_pulse_name_edit)

        layout.addWidget(measurement_group)

        # --- Scanning parameters group ---
        scanning_group = QtWidgets.QGroupBox('Scanning')
        scanning_layout = QtWidgets.QFormLayout(scanning_group)

        self.z_scan_range_spinbox = QtWidgets.QDoubleSpinBox()
        self.z_scan_range_spinbox.setRange(0.1, 50.0)
        self.z_scan_range_spinbox.setDecimals(1)
        self.z_scan_range_spinbox.setSuffix(' µm')
        self.z_scan_range_spinbox.setToolTip(
            'Z scan range for surface finding (next iteration)')
        scanning_layout.addRow('Z scan range:', self.z_scan_range_spinbox)

        self.z_depth_spinbox = QtWidgets.QDoubleSpinBox()
        self.z_depth_spinbox.setRange(0.0, 20.0)
        self.z_depth_spinbox.setDecimals(1)
        self.z_depth_spinbox.setSuffix(' µm')
        self.z_depth_spinbox.setToolTip(
            'Depth below surface line to image '
            '(Z = Z_SL - Z_depth)')
        scanning_layout.addRow(
            'Z depth from surface:', self.z_depth_spinbox)

        self.poi_radius_spinbox = QtWidgets.QDoubleSpinBox()
        self.poi_radius_spinbox.setRange(0.1, 10.0)
        self.poi_radius_spinbox.setDecimals(1)
        self.poi_radius_spinbox.setSuffix(' µm')
        self.poi_radius_spinbox.setToolTip(
            'POI non-repetition radius: candidates within this distance '
            'of previously measured NVs are filtered out')
        scanning_layout.addRow(
            'POI non-repetition radius:', self.poi_radius_spinbox)

        layout.addWidget(scanning_group)

        # --- Progress display ---
        progress_group = QtWidgets.QGroupBox('Experiment Progress')
        progress_layout = QtWidgets.QFormLayout(progress_group)

        self.cells_progress_label = QtWidgets.QLabel('0 / 0')
        progress_layout.addRow('Cells completed:', self.cells_progress_label)

        self.nvs_cell_progress_label = QtWidgets.QLabel('0 / 0')
        progress_layout.addRow(
            'NVs (this cell):', self.nvs_cell_progress_label)

        self.total_nvs_label = QtWidgets.QLabel('0')
        progress_layout.addRow('Total NVs measured:', self.total_nvs_label)

        self.state_label = QtWidgets.QLabel('idle')
        progress_layout.addRow('Pipeline state:', self.state_label)

        layout.addWidget(progress_group)

        # --- Control buttons ---
        button_layout = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton('▶ Start Experiment')
        self.start_button.setToolTip('Start the full NV automation pipeline')
        self.stop_button = QtWidgets.QPushButton('⏹ Stop')
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip('Gracefully stop the pipeline')
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout)

    def _connect_signals(self):
        """Connect GUI widgets to logic and logic signals to GUI."""
        # GUI → Logic
        self.start_button.clicked.connect(self._on_start)
        self.stop_button.clicked.connect(self._on_stop)
        self.target_cells_spinbox.valueChanged.connect(
            lambda v: setattr(self._logic, 'target_cells', v))
        self.nvs_per_cell_spinbox.valueChanged.connect(
            lambda v: setattr(self._logic, 'target_nvs_per_cell', v))
        self.enable_pulsed_checkbox.toggled.connect(
            lambda v: setattr(self._logic, 'enable_pulsed_measurement', v))
        self.measurement_name_edit.textChanged.connect(
            lambda v: setattr(
                self._logic, 'measurement_ensemble_name', v))
        self.laser_pulse_name_edit.textChanged.connect(
            lambda v: setattr(
                self._logic, 'laser_pulse_ensemble_name', v))
        self.z_scan_range_spinbox.valueChanged.connect(
            lambda v: setattr(self._logic, 'z_scan_range_m', v * 1e-6))
        self.z_depth_spinbox.valueChanged.connect(
            lambda v: setattr(
                self._logic, 'z_depth_from_surface_m', v * 1e-6))
        self.poi_radius_spinbox.valueChanged.connect(
            lambda v: setattr(
                self._logic, 'poi_non_repetition_radius_m', v * 1e-6))

        # Logic → GUI
        self._logic.sigStateChanged.connect(
            self._update_state, QtCore.Qt.QueuedConnection)
        self._logic.sigExperimentProgress.connect(
            self._update_progress, QtCore.Qt.QueuedConnection)
        self._logic.sigMultiScaleComplete.connect(
            self._on_complete, QtCore.Qt.QueuedConnection)

    def _sync_from_logic(self):
        """Initialize GUI values from the logic StatusVars."""
        self.target_cells_spinbox.setValue(int(self._logic.target_cells))
        self.nvs_per_cell_spinbox.setValue(
            int(self._logic.target_nvs_per_cell))
        self.enable_pulsed_checkbox.setChecked(
            bool(self._logic.enable_pulsed_measurement))
        self.measurement_name_edit.setText(
            str(self._logic.measurement_ensemble_name))
        self.laser_pulse_name_edit.setText(
            str(self._logic.laser_pulse_ensemble_name))
        self.z_scan_range_spinbox.setValue(
            float(self._logic.z_scan_range_m) * 1e6)
        self.z_depth_spinbox.setValue(
            float(self._logic.z_depth_from_surface_m) * 1e6)
        self.poi_radius_spinbox.setValue(
            float(self._logic.poi_non_repetition_radius_m) * 1e6)

    @QtCore.Slot()
    def _on_start(self):
        """Handle Start Experiment button click."""
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._logic.start_multi_scale_find()

    @QtCore.Slot()
    def _on_stop(self):
        """Handle Stop button click."""
        self._logic.stop_multi_scale_find()
        self.stop_button.setEnabled(False)

    @QtCore.Slot(str)
    def _update_state(self, state):
        """Update state display label."""
        state_display = {
            'idle': '⚪ Idle',
            'macro_scanning': '🔍 Macro Scanning...',
            'macro_segmentation': '🧩 Segmenting...',
            'micro_scanning': '🔬 Micro Scanning...',
            'micro_processing': '🎯 Processing...',
            'verification': '⚙️ Verifying...',
            'pulsed_measurement': '📊 Measuring...',
        }.get(state, state.capitalize())
        self.state_label.setText(state_display)

        if state == 'idle':
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    @QtCore.Slot(object)
    def _update_progress(self, progress):
        """Update progress labels from experiment progress dict."""
        self.cells_progress_label.setText('{0} / {1}'.format(
            progress.get('cells_completed', 0),
            progress.get('target_cells', 0)))
        self.nvs_cell_progress_label.setText('{0} / {1}'.format(
            progress.get('nvs_this_cell', 0),
            progress.get('target_nvs_per_cell', 0)))
        self.total_nvs_label.setText(str(
            progress.get('total_nvs_measured', 0)))

    @QtCore.Slot(dict)
    def _on_complete(self, final_stats):
        """Handle experiment completion."""
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.state_label.setText('✅ Complete ({0} cells, {1} NVs)'.format(
            final_stats.get('cells_completed', 0),
            final_stats.get('total_nvs_measured', 0)))
