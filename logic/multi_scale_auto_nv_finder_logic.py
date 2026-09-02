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
  7. Dispatching candidates to NVCandidateVerifier.
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
from logic.sample_characterization_engine import SampleCharacterizationEngine
from logic.scan_region_queue import ScanRegionQueue
from logic.cell_region_processor import CellRegionProcessor
from logic.poi_extractor import POIExtractor
from logic.drift_tracker import DriftTracker
from logic.z_surface_finder import ZSurfaceFinder
from logic.cell_data_logger import CellDataLogger


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
    min_fluorescence_counts_per_s = StatusVar(
        'min_fluorescence_counts_per_s', 50e3)    # 50 kc/s
    max_fluorescence_counts_per_s = StatusVar(
        'max_fluorescence_counts_per_s', 8e6)     # 8 Mc/s
    max_rescans_per_cell = StatusVar('max_rescans_per_cell', 3)
    save_annotated_images = StatusVar('save_annotated_images', True)
    output_data_dir = StatusVar('output_data_dir', '')

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
        # NOTE: ROISegmentationLogic replaced by SampleCharacterizationEngine
        # which intelligently selects between sparse and dense algorithms.
        self._sample_engine = SampleCharacterizationEngine()
        self._roi_segmenter = ROISegmentationLogic()  # kept as fallback
        self._queue = ScanRegionQueue()
        self._cell_processor = CellRegionProcessor()
        self._poi_extractor = POIExtractor()
        self._drift_tracker = DriftTracker()
        self._z_surface_finder = ZSurfaceFinder()
        self._cell_data_logger = None

        # State tracking
        self._original_scan_params = None
        self._stats = {}
        self._current_region = None

        # Current cell scan data & candidates for annotation & archiving
        self._current_micro_image = None
        self._current_micro_x_coords = None
        self._current_micro_y_coords = None
        self._current_micro_z = 0.0
        self._current_cell_result = None
        self._current_cell_candidates = []
        self._current_cell_verified_pois = []

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

        # Strict serialization flags — ensures no concurrent verify + measure
        self._pulsed_measurement_pending = False
        self._verification_batch_done = False
        self._current_measurement_candidate = None

        # Measurement results for current run
        self._measurement_results = []

    def on_activate(self):
        print('[MultiScaleAutoNVFinderLogic] on_activate START')
        self._set_state('idle')
        self._stop_requested = False

        # ------------------------------------------------------------------
        # Connect verifier and executor signals ONCE for the module lifetime.
        # This avoids the bug where per-batch connect/disconnect calls
        # accumulate duplicate handlers or leave stale connections on error
        # paths.
        # ------------------------------------------------------------------
        print('[MultiScaleAutoNVFinderLogic] Connecting verifier signals...')
        verifier = self.nvcandidateverifier()
        verifier.sigCandidateAccepted.connect(
            self._on_candidate_accepted, QtCore.Qt.QueuedConnection)
        verifier.sigCandidateRejected.connect(
            self._on_candidate_rejected, QtCore.Qt.QueuedConnection)
        verifier.sigVerificationFinished.connect(
            self._on_verification_batch_complete,
            QtCore.Qt.QueuedConnection)
        print('[MultiScaleAutoNVFinderLogic] Verifier signals connected OK')

        print('[MultiScaleAutoNVFinderLogic] Connecting executor signals...')
        executor = self._get_executor()
        if executor is not None:
            executor.sigMeasurementComplete.connect(
                self._on_measurement_complete, QtCore.Qt.QueuedConnection)
            executor.sigMeasurementError.connect(
                self._on_measurement_error, QtCore.Qt.QueuedConnection)
            print('[MultiScaleAutoNVFinderLogic] Executor signals connected OK')
        else:
            print('[MultiScaleAutoNVFinderLogic] No executor available (None)')

        self.log.info('MultiScaleAutoNVFinderLogic activated.')
        print('[MultiScaleAutoNVFinderLogic] on_activate COMPLETE')

    def on_deactivate(self):
        if self._state != 'idle':
            self.stop_multi_scale_find()

        # Disconnect verifier signals
        verifier = self.nvcandidateverifier()
        try:
            verifier.sigCandidateAccepted.disconnect(
                self._on_candidate_accepted)
            verifier.sigCandidateRejected.disconnect(
                self._on_candidate_rejected)
            verifier.sigVerificationFinished.disconnect(
                self._on_verification_batch_complete)
        except (TypeError, RuntimeError):
            pass

        # Disconnect executor signals
        executor = self._get_executor()
        if executor is not None:
            try:
                executor.sigMeasurementComplete.disconnect(
                    self._on_measurement_complete)
                executor.sigMeasurementError.disconnect(
                    self._on_measurement_error)
            except (TypeError, RuntimeError):
                pass

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

        # Stop confocal scanning if active
        if self._state in ('macro_scanning', 'micro_scanning'):
            self.confocallogic().stop_scanning()

        # Always stop the verifier — it may still have a running batch
        # even when state has moved to 'pulsed_measurement'
        try:
            self.nvcandidateverifier().stop_verification()
        except Exception:
            pass

        # Always stop the pulsed measurement executor
        executor = self._get_executor()
        if executor is not None:
            try:
                executor.stop_measurement()
            except Exception:
                pass

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
        self._current_cell_verified_pois = []
        self._current_cell_candidates = []
        self._drift_tracker.reset()

        # Initialize systematic session data logger
        out_dir = str(self._val(self.output_data_dir, '')).strip() or None
        target_cells_num = int(self._val(self.target_cells, 5))
        target_nvs_num = int(self._val(self.target_nvs_per_cell, 3))
        pulsed_enabled = bool(self._val(self.enable_pulsed_measurement, False))

        self._cell_data_logger = CellDataLogger(
            base_data_dir=out_dir,
            run_tag='AutoNV',
            config_metadata={
                'target_cells': target_cells_num,
                'target_nvs_per_cell': target_nvs_num,
                'enable_pulsed_measurement': pulsed_enabled,
                'measurement_ensemble_name': str(self._val(self.measurement_ensemble_name, '')),
                'laser_pulse_ensemble_name': str(self._val(self.laser_pulse_ensemble_name, '')),
                'coarse_fov_um': float(self._val(self.coarse_fov_um, 200.0)),
                'micro_resolution': int(self._val(self.micro_resolution, 200)),
                'poi_non_repetition_radius_m': float(self._val(self.poi_non_repetition_radius_m, 1.0e-6)),
            }
        )
        self._log('Initialized session data logger in: {0}'.format(
            self._cell_data_logger.output_directory))

        # Save original confocal scan settings
        self._original_scan_params = {
            'x_range': list(self.confocallogic().image_x_range),
            'y_range': list(self.confocallogic().image_y_range),
            'xy_resolution': self.confocallogic().xy_resolution,
        }

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

    def _get_poi_manager(self):
        """Safely resolve PoiManagerLogic, or return None."""
        try:
            return self.poimanagerlogic()
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
        if self._state == 'idle':
            return
        if self._stop_requested:
            self._finish('Stopped during macro scan.')
            return

        self._set_state('macro_segmentation')
        self._log('MACRO scan complete. Running sample characterization...')

        image = self.confocallogic().xy_image
        self.sigVisualUpdate.emit('Macro Scan', image)
        x_coords = image[0, :, 0]
        y_coords = image[:, 0, 1]

        # 1. Characterize sample and run optimal segmentation algorithm
        char_result = self._sample_engine.characterize_and_segment(
            image, min_cell_area_um2=float(self._val(self.min_cell_area_um2, 50.0)))
        seg_result = char_result.segmentation_dict
        self._log(
            'Sample classified as: {0} (confidence={1:.2f}, algo={2}). '
            '{3} cells detected.'.format(
                char_result.characterization.sample_type.value,
                char_result.characterization.confidence,
                char_result.segmentation.algorithm_used.value,
                char_result.characterization.estimated_cell_count))

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
        if self._state == 'idle':
            return
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

        # Reset per-cell counters and logging arrays
        self._cell_nv_count = 0
        self._cell_rescan_count = 0
        self._current_cell_verified_pois = []
        self._current_cell_candidates = []
        self._current_micro_image = None
        self._current_micro_x_coords = None
        self._current_micro_y_coords = None
        self._current_micro_z = 0.0
        self._current_cell_result = None

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
        if self._state == 'idle':
            return
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

        self._current_micro_image = image
        self._current_micro_x_coords = x_coords
        self._current_micro_y_coords = y_coords
        self._current_micro_z = z_current

        # Update PoiManagerLogic scan image so GUI displays this close-scan
        poi_mgr = self._get_poi_manager()
        if poi_mgr is not None:
            try:
                poi_mgr.set_scan_image(emit_change=True)
            except Exception as e:
                self._log('Note: could not update POI Manager scan image: {0}'.format(e))

        # 1. Process cell region
        cell_result = self._cell_processor.process(image, scan_region=self._current_region)
        self._current_cell_result = cell_result

        # --- Diagnostic: Cell processor results ---
        fluor = image[:, :, 3]
        self._log('  Image shape: {0}, fluor range: [{1:.0f}, {2:.0f}] c/s'.format(
            image.shape, float(fluor.min()), float(fluor.max())))
        self._log('  Cell interior: {0} px ({1:.1f}%), Nucleus: {2} px, '
                  'Bright clusters: {3} px'.format(
                      cell_result.diagnostics.get('cell_area_px', 0),
                      cell_result.diagnostics.get('cell_area_fraction', 0) * 100,
                      cell_result.diagnostics.get('nucleus_area_px', 0),
                      cell_result.diagnostics.get('bright_cluster_area_px', 0)))
        proc_area = cell_result.diagnostics.get('processable_area_px', 0)
        self._log('  Processable zone: {0} px, processable={1}'.format(
            proc_area, cell_result.zone_stats.get('processable', False)))
        if cell_result.zone_stats.get('processable', False):
            self._log('  Zone stats: median={0:.0f}, std={1:.0f}, '
                      'max={2:.0f} c/s'.format(
                          cell_result.zone_stats.get('median_intensity', 0),
                          cell_result.zone_stats.get('std_intensity', 0),
                          cell_result.zone_stats.get('max_intensity', 0)))
        else:
            self._log('  Zone NOT processable: {0}'.format(
                cell_result.zone_stats.get('reason', 'unknown')))

        if hasattr(cell_result, 'processable_mask'):
            self.sigVisualUpdate.emit(
                'Processable Zone Mask', cell_result.processable_mask)

        # 2. Extract POI candidates
        extraction_result = self._poi_extractor.extract(
            cell_result, image, x_coords=x_coords, y_coords=y_coords,
            z_current=z_current, scan_region=self._current_region)

        self._current_cell_candidates = getattr(
            extraction_result, 'all_candidates', extraction_result.candidates)

        # --- Diagnostic: Extraction pipeline results ---
        diag = extraction_result.diagnostics
        self._log('  CIP: noise={0:.1f}, threshold={1:.1f}, spot_px={2}'.format(
            diag.get('noise_sigma', 0),
            diag.get('threshold_used', 0),
            diag.get('spot_px', 0)))
        self._log('  CIP stages: above_thr={0}, maxima={1}, in_zone={2}, '
                  'shape_ok={3}, clustered={4}, total_det={5}'.format(
                      diag.get('n_above_threshold', 0),
                      diag.get('n_maxima', 0),
                      diag.get('n_zone_maxima', 0),
                      diag.get('n_shape_valid', 0),
                      diag.get('n_clustered', 0),
                      extraction_result.stats.get('total_detected', 0)))
        if diag.get('early_exit_stage'):
            self._log('  CIP early exit at stage: {0}'.format(
                diag['early_exit_stage']))
        if 'reason' in diag:
            self._log('  Early exit reason: {0}'.format(diag['reason']))
        if diag.get('otsu_fallback_triggered'):
            self._log('  WARNING: scikit-image Otsu threshold was too high, fell back to median.')
        if extraction_result.stats.get('total_detected', 0) > 0:
            self._log('  After narrowing: strong={0}, marginal={1}, '
                      'rejected={2}'.format(
                          extraction_result.stats.get('n_strong', 0),
                          extraction_result.stats.get('n_marginal', 0),
                          extraction_result.stats.get('n_rejected', 0)))

        strong_cands = extraction_result.strong_candidates
        self._stats['total_candidates'] += len(strong_cands)

        # 3. Filter out previously used POIs (non-repetition radius)
        filtered_cands = self._filter_used_pois(strong_cands)

        self._log('Found {0} candidates ({1} after POI filtering).'.format(
            len(strong_cands), len(filtered_cands)))

        if not filtered_cands:
            self._log('No new candidates in {0}. Completing cell.'.format(
                self._current_region.region_id))
            self._complete_current_cell()
            return

        # 4. Queue filtered candidates for one-at-a-time serial processing.
        #    HARDWARE CONSTRAINT: The verifier (optimizer/confocal) and the
        #    pulsed measurement (pulse generator / fast counter) cannot run
        #    concurrently.  We send candidates to the verifier ONE AT A TIME
        #    and wait for each candidate's full lifecycle (verify → measure)
        #    to complete before starting the next.
        self._pending_candidates = filtered_cands
        self._current_candidate_index = 0
        self._pulsed_measurement_pending = False
        self._verification_batch_done = False

        self._log('Queued {0} candidates for serial verify+measure.'.format(
            len(filtered_cands)))

        # 5. Start verifying the FIRST candidate only
        self._verify_next_candidate()

    # =====================================================================
    # INTERNAL: One-at-a-time verification + serial measurement flow
    # =====================================================================

    def _verify_next_candidate(self):
        """Send the next single candidate to the verifier.

        HARDWARE CONSTRAINT: This must only be called when no pulsed
        measurement is running and no verifier batch is active.  The
        verifier (optimizer/confocal hardware) and the pulsed measurement
        (pulse generator / fast counter) cannot operate concurrently.
        """
        if self._state == 'idle':
            return
        if self._stop_requested:
            self._finish('Stopped by user.')
            return

        target_nvs = int(self._val(self.target_nvs_per_cell, 3))
        if self._cell_nv_count >= target_nvs:
            self._log('Target NVs/cell met ({0}). Advancing to '
                      'next cell.'.format(self._cell_nv_count))
            self._complete_current_cell()
            return

        if self._current_candidate_index >= len(self._pending_candidates):
            self._log('All candidates exhausted for cell {0}.'.format(
                self._current_region.region_id))
            self._complete_current_cell()
            return

        candidate = self._pending_candidates[self._current_candidate_index]
        cand_id = (getattr(candidate, 'candidate_id', None)
                   or str(self._current_candidate_index))

        self._set_state('verification')
        self._verification_batch_done = False
        self._pulsed_measurement_pending = False

        self._log('Verifying candidate {0} ({1}/{2})...'.format(
            cand_id,
            self._current_candidate_index + 1,
            len(self._pending_candidates)))

        # Send a SINGLE candidate as a batch-of-one.  The verifier will
        # complete the full optical check for this one candidate before
        # sigVerificationFinished fires.
        verifier = self.nvcandidateverifier()
        # Push fluorescence count rate gates from orchestrator to verifier
        verifier.min_fluorescence_counts_per_s = float(
            self._val(self.min_fluorescence_counts_per_s, 50e3))
        verifier.max_fluorescence_counts_per_s = float(
            self._val(self.max_fluorescence_counts_per_s, 8e6))
        verifier.verify_batch(
            [candidate],
            run_context={
                'region_id': self._current_region.region_id,
                'cell_nv_count': self._cell_nv_count,
                'candidate_index': self._current_candidate_index,
                'rescan_number': self._cell_rescan_count,
            })

    def _on_candidate_accepted(self, accepted_record):
        """Handle an optically verified candidate from NVCandidateVerifier.

        If pulsed measurement is enabled, start it and set the pending
        flag so that `_on_verification_batch_complete` knows to wait.
        The next candidate will only be dispatched once BOTH the
        verification batch and the pulsed measurement have finished.
        """
        if self._state == 'idle':
            return
        if self._stop_requested:
            return

        candidate_id = accepted_record.get('candidate_id', 'unknown')
        position = accepted_record.get('accepted_position_m', [0, 0, 0])
        poi_name = accepted_record.get('poi_name') or 'NV_{0}_{1}'.format(
            self._current_region.region_id if self._current_region else 'R0', candidate_id)

        # Ensure POI is registered in PoiManagerLogic if not already done by verifier
        poi_mgr = self._get_poi_manager()
        if poi_mgr is not None and accepted_record.get('registration_status') != 'registered':
            try:
                if poi_name not in poi_mgr.poi_names:
                    poi_mgr.add_poi(position=np.array(position), name=poi_name)
            except Exception as e:
                self._log('Note: POI registration in PoiManagerLogic: {0}'.format(e))

        # Record candidate in current cell's verified POI list for annotation & archiving
        poi_entry = {
            'candidate_id': candidate_id,
            'poi_name': poi_name,
            'accepted_position_m': list(position),
            'seed_position_m': accepted_record.get('seed_position_m', list(position)),
            'region_id': accepted_record.get('region_id', self._current_region.region_id if self._current_region else ''),
            'overall_score': accepted_record.get('overall_score'),
            'optical_stats': accepted_record.get('optical_stats', {}),
            'pulsed_measurement': None,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        self._current_cell_verified_pois.append(poi_entry)

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
            # Mark pending BEFORE starting — _on_verification_batch_complete
            # will check this flag and wait for measurement to finish.
            self._pulsed_measurement_pending = True
            self._start_pulsed_measurement(accepted_record)
            # DO NOT advance to next candidate here.  The serial flow
            # continues in _on_measurement_complete → _advance_after_measurement.
        else:
            # No pulsed measurement — just count the verified NV
            self._register_measured_nv(accepted_record, measurement_result=None)

    def _on_candidate_rejected(self, rejected_record):
        """Handle a rejected candidate.  Logged for diagnostics."""
        if self._state == 'idle':
            return
        cand_id = rejected_record.get('candidate_id', 'unknown')
        reason = rejected_record.get('rejection_reason', 'unknown reason')
        details = rejected_record.get('rejection_details', [])
        self._log('Candidate {0} REJECTED by verifier. Reason: {1}'.format(cand_id, reason))
        print('[MultiScaleAutoNVFinderLogic] Candidate {0} REJECTED. Reason: {1}'.format(cand_id, reason))
        if details:
            for d in details:
                reason_str = ' -> ' + d['reason'] if d.get('reason') else ''
                detail_str = '  * {0}: {1}{2}'.format(
                    d.get('label', d.get('gate_name', '')),
                    d.get('measured_value', ''),
                    reason_str)
                self._log(detail_str)
                print(detail_str)

    def _start_pulsed_measurement(self, accepted_record):
        """Start a pulsed measurement (T1/ODMR) on an accepted candidate."""
        executor = self._get_executor()
        if executor is None:
            self._log('WARNING: PulsedMeasurementExecutor not connected. '
                      'Counting NV without measurement.')
            self._pulsed_measurement_pending = False
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

        # Store current candidate for the measurement callback
        self._current_measurement_candidate = accepted_record

        meas_ensemble = str(self._val(self.measurement_ensemble_name, ''))
        pulse_ensemble = str(self._val(self.laser_pulse_ensemble_name, ''))

        executor.execute_measurement(
            candidate_record=accepted_record,
            measurement_name=meas_ensemble,
            laser_pulse_name=pulse_ensemble)

    def _on_measurement_complete(self, result):
        """Handle completed pulsed measurement.

        After processing the result, advance to the next candidate.
        This is the ONLY place that resumes the serial pipeline after a
        pulsed measurement.  The verifier is never started until this
        method runs.
        """
        if self._state == 'idle':
            return

        if self._stop_requested:
            self._pulsed_measurement_pending = False
            self._finish('Stopped during pulsed measurement.')
            return

        candidate = self._current_measurement_candidate
        if candidate is None:
            self._log('WARNING: Measurement completed but no candidate '
                      'record found.')
            self._pulsed_measurement_pending = False
            self._advance_after_measurement()
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
        self._pulsed_measurement_pending = False

        # Advance to the next candidate in the serial pipeline.
        self._advance_after_measurement()

    def _on_measurement_error(self, error_msg):
        """Handle pulsed measurement error (non-fatal logging)."""
        self._log('Measurement error: {0}'.format(error_msg))

    def _advance_after_measurement(self):
        """Advance to the next candidate after a measurement completes.

        This is called from _on_measurement_complete.  It waits for the
        verification batch to have finished (which it usually has, since
        we send single-candidate batches) and then dispatches the next
        candidate.
        """
        if self._state == 'idle' or self._stop_requested:
            return

        self._current_candidate_index += 1
        self._set_state('verification')
        QtCore.QTimer.singleShot(0, self._verify_next_candidate)

    def _register_measured_nv(self, accepted_record, measurement_result):
        """Register an NV as successfully measured."""
        position = accepted_record.get('accepted_position_m', [0, 0, 0])
        cand_id = accepted_record.get('candidate_id', '')

        # Update current cell verified POI record with measurement result
        for poi in self._current_cell_verified_pois:
            if poi.get('candidate_id') == cand_id:
                poi['pulsed_measurement'] = measurement_result
                break

        # Append to POI used list for non-repetition filtering
        self._poi_used_list.append(list(position))

        self._cell_nv_count += 1
        self._total_nvs_measured += 1

        target_nvs = int(self._val(self.target_nvs_per_cell, 3))

        # Store measurement result
        nv_record = {
            'candidate_id': cand_id,
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
                      cand_id,
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
        """Handle completion of a single-candidate verification batch.

        In the one-at-a-time flow, this fires after each candidate's
        optical verification finishes.  If a pulsed measurement is
        pending for this candidate, we do NOT advance — the serial flow
        will continue from `_on_measurement_complete` instead.  If no
        measurement is pending (candidate was rejected, or pulsed
        measurement is disabled), we advance to the next candidate.
        """
        if self._state == 'idle':
            return

        self._verification_batch_done = True

        if self._stop_requested:
            self._finish('Stopped during verification.')
            return

        if self._pulsed_measurement_pending:
            # A pulsed measurement is still running for this candidate.
            # _on_measurement_complete will call _advance_after_measurement
            # once the measurement finishes.  Do NOT start the next
            # candidate now — the hardware is busy.
            self._log('Verification batch done; waiting for pulsed '
                      'measurement to finish before advancing.')
            return

        # No measurement pending — candidate was rejected or pulsed
        # measurement is disabled.  Advance to the next candidate.
        self._current_candidate_index += 1
        QtCore.QTimer.singleShot(0, self._verify_next_candidate)

    def _complete_current_cell(self):
        """Mark the current cell as done, save annotated close-scan data, and advance to the next region."""
        target_nvs = int(self._val(self.target_nvs_per_cell, 3))
        region_id_str = self._current_region.region_id if self._current_region else 'unknown'
        self._log('Cell {0} complete. NVs measured: {1}/{2}'.format(
            region_id_str, self._cell_nv_count, target_nvs))

        # Save annotated close-scan image and systematic cell data
        if (self._cell_data_logger is not None and
                self._current_micro_image is not None and
                self._current_region is not None):
            try:
                save_pdf = bool(self._val(self.save_annotated_images, True))
                cell_diag = self._current_cell_result.diagnostics if self._current_cell_result else {}
                cell_summary = self._cell_data_logger.save_cell_data(
                    scan_region=self._current_region,
                    image_data=self._current_micro_image,
                    x_coords_m=self._current_micro_x_coords,
                    y_coords_m=self._current_micro_y_coords,
                    z_current_m=self._current_micro_z,
                    verified_pois=self._current_cell_verified_pois,
                    all_candidates=self._current_cell_candidates,
                    cell_diagnostics=cell_diag,
                    save_pdf=save_pdf
                )
                self._log('Archived cell {0} data & annotated close-scan plot ({1} verified NVs) to: {2}'.format(
                    region_id_str,
                    len(self._current_cell_verified_pois),
                    cell_summary.get('cell_folder', '')))
                self.sigVisualUpdate.emit('Cell POIs Annotated', cell_summary)
            except Exception as e:
                self._log('WARNING: Could not save annotated cell data: {0}'.format(e))
                traceback.print_exc()

        if self._current_region is not None:
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

        # Finalize data logger session
        if self._cell_data_logger is not None:
            try:
                run_report = self._cell_data_logger.finalize_run(
                    run_stats=final_stats, final_reason=reason)
                out_dir = run_report.get('output_directory', '')
                self._log('Experiment run finalized. Manifest and master POI list saved in:\n  {0}'.format(out_dir))
                final_stats['output_directory'] = out_dir
            except Exception as e:
                self.log.warning('Could not finalize run in CellDataLogger: {0}'.format(e))

        self._set_state('idle')
        self.sigMultiScaleComplete.emit(final_stats)
        self._stop_requested = False
