# -*- coding: utf-8 -*-
"""
Multi-Scale Auto NV Finder Logic — Full Experiment Loop Orchestrator

This Qudi module serves as the master orchestrator for the automated NV
center detection, verification, and pulsed measurement pipeline.  It
coordinates the complete coarse-to-fine zoom loop with integrated
experiment execution:

  1. Wide-field (coarse) scanning via ConfocalLogic.
  2. ROI Segmentation to identify cell regions.
  3. Queuing bounding boxes by priority.
  4. Micro-scanning (close scans) on each region.
  5. Cell region processing and POI extraction.
  6. POI deduplication (non-repetition radius filtering).
  7. Dispatching candidates to NVCandidateVerifier (hybrid mode).
  8. Running pulsed measurements (T1/ODMR) via PulsedMeasurementExecutor.
  9. Drift snapshot recording for calibration.
  10. Re-scanning ROI if more NVs are needed for a cell.
  11. Moving to the next cell ROI until all targets are met.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import logging
import traceback
import time
import os
import numpy as np
from qtpy import QtCore

from logic.generic_logic import GenericLogic
from core.connector import Connector
from core.statusvariable import StatusVar
from core.util.mutex import Mutex

# Standalone processing classes
from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.scan_region_queue import ScanRegionQueue
from logic.cell_region_processor import CellRegionProcessor
from logic.poi_extractor import POIExtractor
from logic.drift_tracker import DriftTracker
from logic.z_surface_finder import ZSurfaceFinder


class MultiScaleAutoNVFinderLogic(GenericLogic):
    """Master orchestrator for the full NV automation experiment loop.

    Implements the complete workflow:
    Start → MACRO scan → ROI Segmentation → Queue →
    [For Each Cell ROI]:
        → MICRO scan → CellProcessor → POIExtractor → Filter POIs →
        [For Each NV Candidate]:
            → NVCandidateVerifier (optical verification) →
            → PulsedMeasurementExecutor (T1/ODMR) →
            → Record drift → Append to poi_used_list →
        [Repeat until NVs per cell target met, re-scanning if needed]
    [Repeat until No. of cells target met]
    → COMPLETE
    """

    _modclass = 'MultiScaleAutoNVFinderLogic'
    _modtype = 'logic'

    # =====================================================================
    # Connectors
    # =====================================================================
    confocallogic = Connector(interface='ConfocalLogic')
    nvcandidateverifier = Connector(interface='NVCandidateVerifier')
    pulsedmeasurementexecutor = Connector(
        interface='PulsedMeasurementExecutor', optional=True)
    poimanagerlogic = Connector(interface='PoiManagerLogic', optional=True)

    # =====================================================================
    # StatusVars — Original scanning parameters
    # =====================================================================
    enable_multi_scale = StatusVar('enable_multi_scale', True)
    coarse_fov_um = StatusVar('coarse_fov_um', 200.0)
    coarse_resolution = StatusVar('coarse_resolution', 200)
    bbox_margin_fraction = StatusVar('bbox_margin_fraction', 0.15)
    micro_resolution = StatusVar('micro_resolution', 200)
    max_regions_per_run = StatusVar('max_regions_per_run', 10)
    min_cell_area_um2 = StatusVar('min_cell_area_um2', 50.0)

    # =====================================================================
    # StatusVars — Experiment loop parameters (from user notes)
    # =====================================================================
    target_cells = StatusVar('target_cells', 5)
    target_nvs_per_cell = StatusVar('target_nvs_per_cell', 3)
    measurement_ensemble_name = StatusVar('measurement_ensemble_name', '')
    laser_pulse_ensemble_name = StatusVar('laser_pulse_ensemble_name', '')
    z_scan_range_m = StatusVar('z_scan_range_m', 5.0e-6)
    z_depth_from_surface_m = StatusVar('z_depth_from_surface_m', 2.0e-6)
    poi_non_repetition_radius_m = StatusVar(
        'poi_non_repetition_radius_m', 1.0e-6)
    enable_pulsed_measurement = StatusVar('enable_pulsed_measurement', False)
    max_rescans_per_cell = StatusVar('max_rescans_per_cell', 3)

    # =====================================================================
    # Signals for GUI and task tracking
    # =====================================================================
    sigStateChanged = QtCore.Signal(str)
    sigMultiScaleComplete = QtCore.Signal(dict)
    sigLogMessage = QtCore.Signal(str)
    sigQueueUpdated = QtCore.Signal(int, int)       # (processed, total)
    sigVisualUpdate = QtCore.Signal(str, object)    # (name, dict_of_data)
    sigNVMeasured = QtCore.Signal(object)           # per-NV result dict
    sigCellComplete = QtCore.Signal(str, int)        # (region_id, nvs_measured)
    sigExperimentProgress = QtCore.Signal(object)   # progress summary dict

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.threadlock = Mutex()

        self._state = 'idle'
        self._stop_requested = False

        # Processing pipelines
        self._roi_segmenter = ROISegmentationLogic()
        self._queue = ScanRegionQueue()
        self._cell_processor = CellRegionProcessor()
        self._poi_extractor = POIExtractor()
        self._drift_tracker = DriftTracker()
        self._z_surface_finder = ZSurfaceFinder()

        # State tracking
        self._original_scan_params = None
        self._stats = {}
        self._current_region = None

        # POI used list — positions of all NVs that have been measured
        self._poi_used_list = []

        # Per-cell tracking
        self._cell_nv_count = 0
        self._cells_completed = 0
        self._total_nvs_measured = 0
        self._cell_rescan_count = 0

        # Pending candidate queue for current cell
        self._pending_candidates = []
        self._current_candidate_index = 0

        # Measurement results for current run
        self._measurement_results = []

    def on_activate(self):
        self._set_state('idle')
        self._stop_requested = False
        self.log.info('MultiScaleAutoNVFinderLogic activated.')

    def on_deactivate(self):
        if self._state != 'idle':
            self.stop_multi_scale_find()
        self.log.info('MultiScaleAutoNVFinderLogic deactivated.')

    # =====================================================================
    # PUBLIC API
    # =====================================================================

    @staticmethod
    def _val(val, default):
        """Helper to safely extract primitive value from StatusVar or descriptor."""
        if hasattr(val, 'default'):
            return val.default
        if val is None:
            return default
        try:
            return type(default)(val)
        except (TypeError, ValueError):
            return default

    @property
    def state(self):
        """Return the current orchestrator state."""
        return self._state

    @property
    def poi_used_list(self):
        """Return the list of all positions of measured NVs."""
        return list(self._poi_used_list)

    @property
    def drift_tracker(self):
        """Return the drift tracker instance for external inspection."""
        return self._drift_tracker

    @property
    def experiment_progress(self):
        """Return a summary dict of the current experiment progress."""
        return {
            'cells_completed': self._cells_completed,
            'target_cells': int(self._val(self.target_cells, 5)),
            'nvs_this_cell': self._cell_nv_count,
            'target_nvs_per_cell': int(self._val(self.target_nvs_per_cell, 3)),
            'total_nvs_measured': self._total_nvs_measured,
            'regions_processed': self._stats.get('regions_processed', 0),
            'regions_queued': self._stats.get('regions_queued', 0),
            'total_candidates_found': self._stats.get(
                'total_candidates', 0),
            'poi_used_count': len(self._poi_used_list),
            'rescans_this_cell': self._cell_rescan_count,
            'state': self._state,
        }

    def stop_multi_scale_find(self):
        """Request a graceful stop."""
        if self._state == 'idle':
            return
        self._log('Stop requested. Finishing current step...')
        self._stop_requested = True
        if self._state in ('macro_scanning', 'micro_scanning'):
            self.confocallogic().stop_scanning()
        elif self._state == 'verification':
            self.nvcandidateverifier().stop_verification()
        elif self._state == 'pulsed_measurement':
            executor = self._get_executor()
            if executor is not None:
                executor.stop_measurement()

    def start_multi_scale_find(self):
        """Begin the full automated NV detection and measurement pipeline."""
        if self._state != 'idle':
            self.log.warning('Multi-scale finder is already running.')
            return

        self._stop_requested = False
        self._stats = {
            'regions_processed': 0,
            'total_candidates': 0,
            'regions_queued': 0,
        }
        self._poi_used_list = []
        self._measurement_results = []
        self._cells_completed = 0
        self._total_nvs_measured = 0
        self._cell_nv_count = 0
        self._cell_rescan_count = 0
        self._drift_tracker.reset()

        # Save original confocal scan settings
        self._original_scan_params = {
            'x_range': list(self.confocallogic().image_x_range),
            'y_range': list(self.confocallogic().image_y_range),
            'xy_resolution': self.confocallogic().xy_resolution,
        }

        target_cells_num = int(self._val(self.target_cells, 5))
        target_nvs_num = int(self._val(self.target_nvs_per_cell, 3))
        pulsed_enabled = bool(self._val(self.enable_pulsed_measurement, False))

        self._log('Starting full NV automation pipeline. '
                  'Target: {0} cells, {1} NVs/cell, '
                  'pulsed measurement: {2}'.format(
                      target_cells_num,
                      target_nvs_num,
                      'ENABLED' if pulsed_enabled else 'disabled'))

        # Setup MACRO scan
        center_x = sum(self._original_scan_params['x_range']) / 2.0
        center_y = sum(self._original_scan_params['y_range']) / 2.0
        fov_m = float(self._val(self.coarse_fov_um, 200.0)) * 1e-6

        x_min, x_max = self.confocallogic().x_range
        y_min, y_max = self.confocallogic().y_range

        x_start = max(x_min, center_x - fov_m / 2)
        x_end = min(x_max, center_x + fov_m / 2)
        y_start = max(y_min, center_y - fov_m / 2)
        y_end = min(y_max, center_y + fov_m / 2)

        self.confocallogic().image_x_range = [x_start, x_end]
        self.confocallogic().image_y_range = [y_start, y_end]
        self.confocallogic().xy_resolution = int(self._val(self.coarse_resolution, 200))

        self._log('Starting MACRO scan ({0} um FOV)...'.format(
            self._val(self.coarse_fov_um, 200.0)))
        self._set_state('macro_scanning')

        self.confocallogic().signal_xy_image_updated.connect(
            self._check_macro_scan_complete, QtCore.Qt.QueuedConnection)

        self.confocallogic().start_scanning(zscan=False)

    # =====================================================================
    # INTERNAL: State management and utility
    # =====================================================================

    def _set_state(self, state):
        self._state = state
        self.sigStateChanged.emit(state)

    def _log(self, message):
        self.log.info(message)
        self.sigLogMessage.emit('[{0}] {1}'.format(
            time.strftime('%H:%M:%S'), message))

    def _emit_progress(self):
        """Emit the current experiment progress for GUI."""
        self.sigExperimentProgress.emit(self.experiment_progress)

    def _get_executor(self):
        """Safely resolve PulsedMeasurementExecutor, or return None."""
        try:
            return self.pulsedmeasurementexecutor()
        except Exception:
            return None

    # =====================================================================
    # INTERNAL: MACRO scan flow
    # =====================================================================

    def _check_macro_scan_complete(self):
        """Gate on module_state: signal_xy_image_updated fires per-line,
        so we wait until the confocal module unlocks (scan finished)."""
        if self.confocallogic().module_state() == 'locked':
            return  # Scan still in progress
        try:
            self.confocallogic().signal_xy_image_updated.disconnect(
                self._check_macro_scan_complete)
        except TypeError:
            pass
        self._on_macro_scan_complete()

    def _on_macro_scan_complete(self):
        if self._stop_requested:
            self._finish('Stopped during macro scan.')
            return

        self._set_state('macro_segmentation')
        self._log('MACRO scan complete. Running ROI segmentation...')

        image = self.confocallogic().xy_image
        self.sigVisualUpdate.emit('Macro Scan', image)
        x_coords = image[0, :, 0]
        y_coords = image[:, 0, 1]

        # 1. Segment ROI
        seg_result = self._roi_segmenter.segment_roi(
            image, min_cell_area_um2=float(self._val(self.min_cell_area_um2, 50.0)))

        # 2. Queue regions
        self._queue = ScanRegionQueue()
        self._queue.extract_regions_from_segmentation(
            seg_result, image, x_coords, y_coords,
            parent_scan_id='macro_{0}'.format(time.time()))
        self._queue.filter_false_positives()
        self._queue.prioritize_queue()

        self._stats['regions_queued'] = self._queue.queued_count
        self._log('ROI segmentation complete. {0} regions queued.'.format(
            self._queue.queued_count))
        self.sigQueueUpdated.emit(0, self._queue.queued_count)

        # Emit the Macro Scan Queue visualization event
        vis_data = {
            'image_data': image[:, :, 3] if image.ndim == 3 else image,
            'x_coords': x_coords,
            'y_coords': y_coords,
            'regions': self._queue.regions
        }
        self.sigVisualUpdate.emit('Macro Scan Queue', vis_data)

        QtCore.QTimer.singleShot(0, self._process_next_region)

    # =====================================================================
    # INTERNAL: Region processing loop (cell-level)
    # =====================================================================

    def _process_next_region(self):
        """Advance to the next queued region (cell)."""
        if self._stop_requested:
            self._finish('Stopped by user.')
            return

        target_cells_num = int(self._val(self.target_cells, 5))
        max_regions_num = int(self._val(self.max_regions_per_run, 10))

        # Check if we've met the target number of cells
        if self._cells_completed >= target_cells_num:
            self._finish('All target cells ({0}) completed.'.format(
                target_cells_num))
            return

        if not self._queue.has_queued_regions():
            self._finish('All regions processed (cells completed: {0}).'.format(
                self._cells_completed))
            return

        if self._stats['regions_processed'] >= max_regions_num:
            self._finish('Reached max_regions_per_run limit ({0}).'.format(
                max_regions_num))
            return

        region = self._queue.get_next_region()
        self._current_region = region
        self._queue.mark_region_status(region.region_id, 'scanning')

        # Reset per-cell counters
        self._cell_nv_count = 0
        self._cell_rescan_count = 0

        # Emit the cropped macro region for visualization
        if hasattr(region, 'cropped_image') and region.cropped_image is not None:
            vis_data = {
                'image_data': region.cropped_image
            }
            self.sigVisualUpdate.emit('Macro Crop (Region {0})'.format(region.region_id), vis_data)

        self._log('Preparing MICRO scan for region {0} '
                  '({1:.1f}x{2:.1f} um)...'.format(
                      region.region_id, region.width_um, region.height_um))
        self._start_micro_scan()

    def _start_micro_scan(self):
        """Configure and start a micro scan of the current region."""
        self._set_state('micro_scanning')

        scan_params = self._queue.compute_scan_parameters(
            self._current_region,
            margin_fraction=float(self._val(self.bbox_margin_fraction, 0.15)),
            resolution=int(self._val(self.micro_resolution, 200)),
            scanner_limits={
                'x_range': self.confocallogic().x_range,
                'y_range': self.confocallogic().y_range,
            })

        self.confocallogic().image_x_range = list(scan_params['x_range'])
        self.confocallogic().image_y_range = list(scan_params['y_range'])
        self.confocallogic().xy_resolution = int(scan_params['resolution'])

        self.confocallogic().signal_xy_image_updated.connect(
            self._check_micro_scan_complete, QtCore.Qt.QueuedConnection)

        self.confocallogic().start_scanning(zscan=False)

    def _check_micro_scan_complete(self):
        """Gate on module_state: wait until confocal module unlocks."""
        if self.confocallogic().module_state() == 'locked':
            return  # Scan still in progress
        try:
            self.confocallogic().signal_xy_image_updated.disconnect(
                self._check_micro_scan_complete)
        except TypeError:
            pass
        self._on_micro_scan_complete()

    def _on_micro_scan_complete(self):
        if self._stop_requested:
            self._finish('Stopped during micro scan.')
            return

        self._set_state('micro_processing')
        self._log('MICRO scan complete for {0}. Running CIP and '
                  'extractor...'.format(self._current_region.region_id))

        image = self.confocallogic().xy_image
        x_coords = image[0, :, 0]
        y_coords = image[:, 0, 1]
        z_current = image[0, 0, 2]

        # 1. Process cell region
        cell_result = self._cell_processor.process(image)
        
        # Save the micro scan data
        try:
            save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'micro_scans')
            os.makedirs(save_dir, exist_ok=True)
            filename = os.path.join(save_dir, 'region_{0}_{1}.npy'.format(self._current_region.region_id, int(time.time())))
            np.save(filename, image)
            self._log('Saved micro scan data for region {0} to {1}'.format(self._current_region.region_id, filename))
        except Exception as e:
            self._log('Failed to save micro scan data: {0}'.format(e))
            
        if hasattr(cell_result, 'processable_mask'):
            self.sigVisualUpdate.emit(
                'Processable Zone Mask', cell_result.processable_mask)

        # 2. Extract POI candidates
        extraction_result = self._poi_extractor.extract(
            cell_result, image, x_coords=x_coords, y_coords=y_coords,
            z_current=z_current, scan_region=self._current_region)

        strong_cands = extraction_result.strong_candidates
        self._stats['total_candidates'] += len(strong_cands)

        # 3. Filter out previously used POIs (non-repetition radius)
        filtered_cands = self._filter_used_pois(strong_cands)

        self._log('Found {0} candidates ({1} after POI filtering).'.format(
            len(strong_cands), len(filtered_cands)))

        if not filtered_cands:
            self._log('No new candidates in {0}. Moving to next '
                      'region.'.format(self._current_region.region_id))
            self._queue.mark_region_status(
                self._current_region.region_id, 'processed',
                nv_candidates_found=0)
            self._stats['regions_processed'] += 1
            self._cells_completed += 1
            self.sigCellComplete.emit(
                self._current_region.region_id, self._cell_nv_count)
            self.sigQueueUpdated.emit(
                self._stats['regions_processed'],
                self._queue.queued_count + self._stats['regions_processed'])
            self._emit_progress()
            QtCore.QTimer.singleShot(0, self._process_next_region)
            return

        # 4. Queue filtered candidates for sequential verification
        self._pending_candidates = filtered_cands
        self._current_candidate_index = 0

        # 5. Send all candidates to verifier as a batch
        self._set_state('verification')
        self._log('Sending {0} candidates to Verifier...'.format(
            len(filtered_cands)))

        verifier = self.nvcandidateverifier()
        verifier.sigCandidateAccepted.connect(
            self._on_candidate_accepted, QtCore.Qt.QueuedConnection)
        verifier.sigVerificationFinished.connect(
            self._on_verification_batch_complete,
            QtCore.Qt.QueuedConnection)

        verifier.verify_batch(
            filtered_cands,
            run_context={
                'region_id': self._current_region.region_id,
                'cell_nv_count': self._cell_nv_count,
                'rescan_number': self._cell_rescan_count,
            })

    # =====================================================================
    # INTERNAL: Verification + measurement flow (NV-level)
    # =====================================================================

    def _on_candidate_accepted(self, accepted_record):
        """Handle an optically verified candidate from NVCandidateVerifier.

        This is called for each individual candidate that passes optical
        gates. If pulsed measurement is enabled, we queue it for
        measurement. Otherwise we count it directly.
        """
        if self._stop_requested:
            return

        candidate_id = accepted_record.get('candidate_id', 'unknown')
        position = accepted_record.get('accepted_position_m', [0, 0, 0])

        self._log('Candidate {0} optically verified at [{1:.2f}, '
                  '{2:.2f}, {3:.2f}] um.'.format(
                      candidate_id,
                      position[0] * 1e6 if len(position) > 0 else 0,
                      position[1] * 1e6 if len(position) > 1 else 0,
                      position[2] * 1e6 if len(position) > 2 else 0))

        target_nvs = int(self._val(self.target_nvs_per_cell, 3))
        # Check if we already have enough NVs for this cell
        if self._cell_nv_count >= target_nvs:
            self._log('Target NVs/cell already met ({0}). Skipping '
                      'measurement for {1}.'.format(
                          self._cell_nv_count, candidate_id))
            return

        pulsed_enabled = bool(self._val(self.enable_pulsed_measurement, False))
        if pulsed_enabled:
            self._start_pulsed_measurement(accepted_record)
        else:
            # No pulsed measurement — just count the verified NV
            self._register_measured_nv(accepted_record, measurement_result=None)

    def _start_pulsed_measurement(self, accepted_record):
        """Start a pulsed measurement (T1/ODMR) on an accepted candidate."""
        executor = self._get_executor()
        if executor is None:
            self._log('WARNING: PulsedMeasurementExecutor not connected. '
                      'Counting NV without measurement.')
            self._register_measured_nv(
                accepted_record, measurement_result=None)
            return

        # Record pre-measurement drift snapshot
        position = accepted_record.get('accepted_position_m', [0, 0, 0])
        self._drift_tracker.record(
            event='pre_measurement',
            position_m=list(position),
            candidate_id=accepted_record.get('candidate_id', ''),
            region_id=accepted_record.get('region_id', ''))

        self._set_state('pulsed_measurement')
        self._log('Starting pulsed measurement for {0}...'.format(
            accepted_record.get('candidate_id', 'unknown')))

        # Connect to measurement completion signal
        executor.sigMeasurementComplete.connect(
            self._on_measurement_complete, QtCore.Qt.QueuedConnection)
        executor.sigMeasurementError.connect(
            self._on_measurement_error, QtCore.Qt.QueuedConnection)

        # Store current candidate for the measurement callback
        self._current_measurement_candidate = accepted_record

        meas_ensemble = str(self._val(self.measurement_ensemble_name, ''))
        pulse_ensemble = str(self._val(self.laser_pulse_ensemble_name, ''))

        executor.execute_measurement(
            candidate_record=accepted_record,
            measurement_name=meas_ensemble,
            laser_pulse_name=pulse_ensemble)

    def _on_measurement_complete(self, result):
        """Handle completed pulsed measurement."""
        executor = self._get_executor()
        if executor is not None:
            try:
                executor.sigMeasurementComplete.disconnect(
                    self._on_measurement_complete)
                executor.sigMeasurementError.disconnect(
                    self._on_measurement_error)
            except (TypeError, RuntimeError):
                pass

        if self._stop_requested:
            self._finish('Stopped during pulsed measurement.')
            return

        candidate = getattr(self, '_current_measurement_candidate', None)
        if candidate is None:
            self._log('WARNING: Measurement completed but no candidate '
                      'record found.')
            return

        # Record post-measurement drift snapshot
        position = candidate.get('accepted_position_m', [0, 0, 0])
        self._drift_tracker.record(
            event='post_measurement',
            position_m=list(position),
            candidate_id=candidate.get('candidate_id', ''),
            region_id=candidate.get('region_id', ''))

        # Compute and log drift for this measurement cycle
        drift = self._drift_tracker.compute_measurement_drift(
            candidate.get('candidate_id', ''))
        if drift is not None:
            self._log('Drift during measurement: dx={0:.1f}nm, '
                      'dy={1:.1f}nm, radial={2:.1f}nm'.format(
                          drift.get('delta_x_m', 0) * 1e9,
                          drift.get('delta_y_m', 0) * 1e9,
                          drift.get('radial_xy_m', 0) * 1e9))

        success = result.get('success', False)
        if success:
            self._register_measured_nv(candidate, measurement_result=result)
        else:
            self._log('Measurement FAILED for {0}: {1}'.format(
                candidate.get('candidate_id', 'unknown'),
                result.get('error', 'unknown error')))

        self._current_measurement_candidate = None
        if self._state == 'pulsed_measurement':
            self._set_state('verification')

    def _on_measurement_error(self, error_msg):
        """Handle pulsed measurement error."""
        self._log('Measurement error: {0}'.format(error_msg))

    def _register_measured_nv(self, accepted_record, measurement_result):
        """Register an NV as successfully measured."""
        position = accepted_record.get('accepted_position_m', [0, 0, 0])

        # Append to POI used list for non-repetition filtering
        self._poi_used_list.append(list(position))

        self._cell_nv_count += 1
        self._total_nvs_measured += 1

        target_nvs = int(self._val(self.target_nvs_per_cell, 3))

        # Store measurement result
        nv_record = {
            'candidate_id': accepted_record.get('candidate_id', ''),
            'poi_name': accepted_record.get('poi_name', ''),
            'position_m': list(position),
            'region_id': accepted_record.get('region_id', ''),
            'cell_number': self._cells_completed + 1,
            'nv_number_in_cell': self._cell_nv_count,
            'measurement_result': measurement_result,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        self._measurement_results.append(nv_record)

        self._log('NV {0} registered! (Cell {1}: {2}/{3} NVs, '
                  'Total: {4})'.format(
                      accepted_record.get('candidate_id', ''),
                      self._cells_completed + 1,
                      self._cell_nv_count,
                      target_nvs,
                      self._total_nvs_measured))

        self.sigNVMeasured.emit(nv_record)
        self._emit_progress()

    # =====================================================================
    # INTERNAL: Verification batch completion → advance to next cell
    # =====================================================================

    def _on_verification_batch_complete(self, verification_result):
        """Handle completion of a verification batch."""
        verifier = self.nvcandidateverifier()
        try:
            verifier.sigCandidateAccepted.disconnect(
                self._on_candidate_accepted)
            verifier.sigVerificationFinished.disconnect(
                self._on_verification_batch_complete)
        except (TypeError, RuntimeError):
            pass

        if self._stop_requested:
            self._finish('Stopped during verification.')
            return

        target_nvs = int(self._val(self.target_nvs_per_cell, 3))
        self._log('Verification batch complete for {0}. '
                  'NVs measured this cell: {1}/{2}'.format(
                      self._current_region.region_id,
                      self._cell_nv_count,
                      target_nvs))

        # Mark this cell region as completed in the queue and stats
        self._queue.mark_region_status(
            self._current_region.region_id, 'processed',
            nv_candidates_found=self._cell_nv_count)
        self._stats['regions_processed'] += 1
        self._cells_completed += 1

        self.sigCellComplete.emit(
            self._current_region.region_id, self._cell_nv_count)
        self.sigQueueUpdated.emit(
            self._stats['regions_processed'],
            self._queue.queued_count + self._stats['regions_processed'])
        self._emit_progress()

        # Advance to the next region
        QtCore.QTimer.singleShot(0, self._process_next_region)

    # =====================================================================
    # INTERNAL: POI filtering
    # =====================================================================

    def _filter_used_pois(self, candidates):
        """Remove candidates that fall within the non-repetition radius
        of any previously measured POI.
        """
        if not self._poi_used_list:
            return list(candidates)

        radius = float(self._val(self.poi_non_repetition_radius_m, 1.0e-6))
        if radius <= 0:
            return list(candidates)

        used_positions = np.array(self._poi_used_list)  # (N, 3)
        filtered = []

        for candidate in candidates:
            # Get candidate position
            if isinstance(candidate, dict):
                cx = candidate.get('x', 0.0)
                cy = candidate.get('y', 0.0)
            else:
                cx = getattr(candidate, 'x', 0.0)
                cy = getattr(candidate, 'y', 0.0)

            # Compute XY distances to all used positions
            distances = np.sqrt(
                (used_positions[:, 0] - cx) ** 2 +
                (used_positions[:, 1] - cy) ** 2)

            if np.all(distances > radius):
                filtered.append(candidate)

        removed = len(candidates) - len(filtered)
        if removed > 0:
            self._log('POI filter: removed {0} candidates within '
                      '{1:.1f} um of used POIs.'.format(
                          removed, radius * 1e6))

        return filtered

    # =====================================================================
    # INTERNAL: Finish
    # =====================================================================

    def _finish(self, reason):
        """Complete the pipeline run and restore settings."""
        self._log('Multi-Scale NV Find completed. Reason: {0}'.format(reason))
        self._log('Stats: cells={0}, total_nvs={1}, '
                  'regions_processed={2}'.format(
                      self._cells_completed,
                      self._total_nvs_measured,
                      self._stats.get('regions_processed', 0)))

        # Save drift tracking data
        if self._drift_tracker.records:
            try:
                drift_summary = self._drift_tracker.summary()
                self._log('Drift summary: {0}'.format(drift_summary))
            except Exception as e:
                self.log.warning(
                    'Could not compute drift summary: {0}'.format(e))

        # Restore original confocal params
        if self._original_scan_params:
            self.confocallogic().image_x_range = list(
                self._original_scan_params['x_range'])
            self.confocallogic().image_y_range = list(
                self._original_scan_params['y_range'])
            self.confocallogic().xy_resolution = (
                self._original_scan_params['xy_resolution'])
            self._original_scan_params = None

        final_stats = {
            'cells_completed': self._cells_completed,
            'total_nvs_measured': self._total_nvs_measured,
            'regions_processed': self._stats.get('regions_processed', 0),
            'regions_queued': self._stats.get('regions_queued', 0),
            'total_candidates': self._stats.get('total_candidates', 0),
            'poi_used_count': len(self._poi_used_list),
            'measurement_results': list(self._measurement_results),
            'drift_records': len(self._drift_tracker.records),
            'reason': reason,
        }

        self._set_state('idle')
        self.sigMultiScaleComplete.emit(final_stats)
        self._stop_requested = False
