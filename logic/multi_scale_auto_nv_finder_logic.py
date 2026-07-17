# -*- coding: utf-8 -*-
"""
Multi-Scale Auto NV Finder Logic

This Qudi module serves as the master orchestrator for the coarse-to-fine 
zoom loop. It coordinates:
  1. Wide-field (coarse) scanning via ConfocalLogic.
  2. ROI Segmentation to find cell regions.
  3. Queuing bounding boxes.
  4. Micro-scanning (close scans) on each region.
  5. Cell region processing and POI extraction.
  6. Dispatching candidates to NVCandidateVerifier.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import time
import numpy as np
from qtpy import QtCore

from logic.generic_logic import GenericLogic
from core.connector import Connector
from core.statusvariable import StatusVar
from core.util.mutex import Mutex

# Import our standalone processing classes
from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.scan_region_queue import ScanRegionQueue
from logic.cell_region_processor import CellRegionProcessor
from logic.poi_extractor import POIExtractor


class MultiScaleAutoNVFinderLogic(GenericLogic):
    _modclass = 'MultiScaleAutoNVFinderLogic'
    _modtype = 'logic'

    # Connectors
    confocallogic = Connector(interface='ConfocalLogic')
    nvcandidateverifier = Connector(interface='NVCandidateVerifier')

    # StatusVars
    enable_multi_scale = StatusVar('enable_multi_scale', True)
    coarse_fov_um = StatusVar('coarse_fov_um', 200.0)
    coarse_resolution = StatusVar('coarse_resolution', 200)
    bbox_margin_fraction = StatusVar('bbox_margin_fraction', 0.15)
    micro_resolution = StatusVar('micro_resolution', 200)
    max_regions_per_run = StatusVar('max_regions_per_run', 10)
    min_cell_area_um2 = StatusVar('min_cell_area_um2', 50.0)

    # Signals for GUI and Task tracking
    sigStateChanged = QtCore.Signal(str)
    sigMultiScaleComplete = QtCore.Signal(dict)
    sigLogMessage = QtCore.Signal(str)
    sigQueueUpdated = QtCore.Signal(int, int) # (processed, total)
    sigVisualUpdate = QtCore.Signal(str, object) # (name, numpy_array)

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.threadlock = Mutex()
        
        self._state = 'idle'
        self._stop_requested = False
        
        # Pipelines
        self._roi_segmenter = ROISegmentationLogic()
        self._queue = ScanRegionQueue()
        self._cell_processor = CellRegionProcessor()
        self._poi_extractor = POIExtractor()
        
        # State tracking
        self._original_scan_params = None
        self._stats = {}
        self._current_region = None

    def on_activate(self):
        self._set_state('idle')
        self._stop_requested = False
        self.log.info('MultiScaleAutoNVFinderLogic activated.')

    def on_deactivate(self):
        if self._state != 'idle':
            self.stop_multi_scale_find()
        self.log.info('MultiScaleAutoNVFinderLogic deactivated.')

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    @property
    def state(self):
        return self._state

    def stop_multi_scale_find(self):
        """Request a graceful stop."""
        if self._state == 'idle':
            return
        self._log('Stop requested. Finishing current step...')
        self._stop_requested = True
        if self._state in ('macro_scanning', 'micro_scanning'):
            self.confocallogic().stop_scanning()

    def start_multi_scale_find(self):
        """Begin the coarse-to-fine zoom orchestration loop."""
        if self._state != 'idle':
            self.log.warning('Multi-scale finder is already running.')
            return

        self._stop_requested = False
        self._stats = {'regions_processed': 0, 'total_candidates': 0, 'regions_queued': 0}
        
        # Save original confocal scan settings
        self._original_scan_params = {
            'x_range': list(self.confocallogic().image_x_range),
            'y_range': list(self.confocallogic().image_y_range),
            'xy_resolution': self.confocallogic().xy_resolution
        }

        # Setup MACRO scan
        center_x = sum(self._original_scan_params['x_range']) / 2.0
        center_y = sum(self._original_scan_params['y_range']) / 2.0
        fov_m = float(self.coarse_fov_um) * 1e-6

        x_min, x_max = self.confocallogic().x_range
        y_min, y_max = self.confocallogic().y_range

        x_start = max(x_min, center_x - fov_m/2)
        x_end = min(x_max, center_x + fov_m/2)
        y_start = max(y_min, center_y - fov_m/2)
        y_end = min(y_max, center_y + fov_m/2)

        self.confocallogic().image_x_range = [x_start, x_end]
        self.confocallogic().image_y_range = [y_start, y_end]
        self.confocallogic().xy_resolution = int(self.coarse_resolution)

        self._log('Starting MACRO scan ({0} um FOV)...'.format(self.coarse_fov_um))
        self._set_state('macro_scanning')
        
        self.confocallogic().signal_xy_image_updated.connect(
            self._check_macro_scan_complete, QtCore.Qt.QueuedConnection)
        
        self.confocallogic().start_scanning(zscan=False)

    # =========================================================================
    # INTERNAL LOOP
    # =========================================================================

    def _set_state(self, state):
        self._state = state
        self.sigStateChanged.emit(state)

    def _log(self, message):
        self.log.info(message)
        self.sigLogMessage.emit('[{0}] {1}'.format(time.strftime('%H:%M:%S'), message))

    def _check_macro_scan_complete(self):
        """Gate on module_state: signal_xy_image_updated fires per-line,
        so we wait until the confocal module unlocks (scan finished)."""
        if self.confocallogic().module_state() == 'locked':
            return  # Scan still in progress, wait for next line signal
        # Scan is done — disconnect and proceed
        try:
            self.confocallogic().signal_xy_image_updated.disconnect(self._check_macro_scan_complete)
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
            image, min_cell_area_um2=float(self.min_cell_area_um2))
        
        # 2. Queue regions
        self._queue = ScanRegionQueue()
        num_regions = self._queue.extract_regions_from_segmentation(
            seg_result, image, x_coords, y_coords,
            parent_scan_id='macro_{0}'.format(time.time())
        )
        self._queue.filter_false_positives()
        self._queue.prioritize_queue()
        
        self._stats['regions_queued'] = self._queue.queued_count
        self._log('ROI segmentation complete. {0} regions queued.'.format(self._queue.queued_count))
        self.sigQueueUpdated.emit(0, self._queue.queued_count)

        QtCore.QTimer.singleShot(0, self._process_next_region)

    def _process_next_region(self):
        if self._stop_requested:
            self._finish('Stopped by user.')
            return

        if not self._queue.has_queued_regions():
            self._finish('All regions processed.')
            return
            
        if self._stats['regions_processed'] >= int(self.max_regions_per_run):
            self._finish('Reached max_regions_per_run limit ({0}).'.format(self.max_regions_per_run))
            return

        region = self._queue.get_next_region()
        self._current_region = region
        self._queue.mark_region_status(region.region_id, 'scanning')

        self._log('Preparing MICRO scan for region {0} ({1:.1f}x{2:.1f} um)...'.format(
            region.region_id, region.width_um, region.height_um))
        self._set_state('micro_scanning')

        # Configure micro scan window
        scan_params = self._queue.compute_scan_parameters(
            region, margin_fraction=float(self.bbox_margin_fraction),
            resolution=int(self.micro_resolution),
            scanner_limits={'x_range': self.confocallogic().x_range,
                            'y_range': self.confocallogic().y_range}
        )
        
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
            self.confocallogic().signal_xy_image_updated.disconnect(self._check_micro_scan_complete)
        except TypeError:
            pass
        self._on_micro_scan_complete()

    def _on_micro_scan_complete(self):

        if self._stop_requested:
            self._finish('Stopped during micro scan.')
            return

        self._set_state('micro_processing')
        self._log('MICRO scan complete for {0}. Running CIP and extractor...'.format(
            self._current_region.region_id))

        image = self.confocallogic().xy_image
        x_coords = image[0, :, 0]
        y_coords = image[:, 0, 1]
        z_current = image[0, 0, 2]

        # 1. Process cell region
        cell_result = self._cell_processor.process(image)
        if hasattr(cell_result, 'processable_mask'):
            # Emit processable zone mask for GUI visualization
            self.sigVisualUpdate.emit('Processable Zone Mask', cell_result.processable_mask)
        
        # 2. Extract POI candidates
        extraction_result = self._poi_extractor.extract(
            cell_result, image, x_coords=x_coords, y_coords=y_coords,
            z_current=z_current, scan_region=self._current_region
        )

        strong_cands = extraction_result.strong_candidates
        self._stats['total_candidates'] += len(strong_cands)
        
        self._queue.mark_region_status(
            self._current_region.region_id, 'processed',
            nv_candidates_found=len(strong_cands)
        )

        self._stats['regions_processed'] += 1
        self.sigQueueUpdated.emit(self._stats['regions_processed'], 
                                  self._queue.queued_count + self._stats['regions_processed'])

        if not strong_cands:
            self._log('No strong candidates found in {0}. Skipping verification.'.format(
                self._current_region.region_id))
            QtCore.QTimer.singleShot(0, self._process_next_region)
            return

        self._log('Found {0} candidates. Sending to Verifier...'.format(len(strong_cands)))
        self._set_state('verification')
        
        # Hook up verifier signal
        self.nvcandidateverifier().sigVerificationFinished.connect(
            self._on_verification_complete, QtCore.Qt.QueuedConnection)
        
        # Launch verifier batch
        self.nvcandidateverifier().verify_batch(
            strong_cands, run_context={'region_id': self._current_region.region_id}
        )

    def _on_verification_complete(self, verification_result):
        try:
            self.nvcandidateverifier().sigVerificationFinished.disconnect(self._on_verification_complete)
        except TypeError:
            pass
        
        if self._stop_requested:
            self._finish('Stopped during verification.')
            return

        self._log('Verification batch complete for {0}.'.format(self._current_region.region_id))
        QtCore.QTimer.singleShot(0, self._process_next_region)

    def _finish(self, reason):
        self._log('Multi-Scale NV Find completed. Reason: {0}'.format(reason))
        self._log('Stats: {0}'.format(self._stats))
        
        # Restore original confocal params
        if self._original_scan_params:
            self.confocallogic().image_x_range = list(self._original_scan_params['x_range'])
            self.confocallogic().image_y_range = list(self._original_scan_params['y_range'])
            self.confocallogic().xy_resolution = self._original_scan_params['xy_resolution']
            self._original_scan_params = None

        self._set_state('idle')
        self.sigMultiScaleComplete.emit(self._stats.copy())
        self._stop_requested = False
