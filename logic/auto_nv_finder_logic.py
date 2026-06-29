# -*- coding: utf-8 -*-

"""
Automated NV center finding logic using CIP (Color Image Processing).

This module orchestrates the full NV-finding pipeline:
    scan → CIP detection → optimization → POI registration

It operates on the fluorescence intensity data from the confocal scanner —
the same data that is rendered as a color image (Inferno colormap) in the GUI.

See documentation/automation/ for full documentation.

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

import numpy as np
import time
from qtpy import QtCore

from logic.generic_logic import GenericLogic
from core.connector import Connector
from core.statusvariable import StatusVar
from core.util.mutex import Mutex
from logic.image_analysis import ConfocalImageAnalysis


class CandidateNV:
    """Data container for a detected NV center candidate."""

    def __init__(self, x, y, z_estimate, pixel_row, pixel_col,
                 intensity, confidence=0.0, circularity=0.0):
        self.x = float(x)                      # Physical X position (meters)
        self.y = float(y)                      # Physical Y position (meters)
        self.z_estimate = float(z_estimate)    # Estimated Z (current focus plane)
        self.pixel_row = int(pixel_row)        # Row in scan image
        self.pixel_col = int(pixel_col)        # Column in scan image
        self.intensity = float(intensity)      # Peak fluorescence (counts/s)
        self.confidence = float(confidence)    # Detection confidence [0, 1]
        self.circularity = float(circularity)  # Spot shape score [0, 1]
        self.status = 'pending'                # pending|optimizing|accepted|rejected
        self.rejection_reason = ''             # Why rejected (if applicable)
        self.optimized_pos = None              # (x, y, z) after optimization
        self.poi_name = ''                     # Assigned POI name (if registered)
        self.fit_quality = 0.0                 # Optimizer fit R²

    def to_dict(self):
        """Convert to dictionary for signal emission."""
        return {
            'x': self.x,
            'y': self.y,
            'z_estimate': self.z_estimate,
            'pixel_row': self.pixel_row,
            'pixel_col': self.pixel_col,
            'intensity': self.intensity,
            'confidence': self.confidence,
            'circularity': self.circularity,
            'status': self.status,
            'rejection_reason': self.rejection_reason,
            'optimized_pos': self.optimized_pos,
            'poi_name': self.poi_name,
            'fit_quality': self.fit_quality
        }


class AutoNVFinderLogic(GenericLogic):
    """Automated NV center finding using CIP (Color Image Processing).

    This module analyzes the fluorescence color/intensity image from the
    confocal scanner to detect NV center candidates, then optimizes and
    registers each one as a Point of Interest (POI).

    The CIP pipeline processes the same intensity data that produces the
    color image displayed in the GUI via the Inferno colormap.
    """

    _modclass = 'AutoNVFinderLogic'
    _modtype = 'logic'

    # Connectors
    confocallogic = Connector(interface='ConfocalLogic')
    optimizerlogic = Connector(interface='OptimizerLogic')
    poimanagerlogic = Connector(interface='PoiManagerLogic')
    fitlogic = Connector(interface='FitLogic')

    # CIP Detection parameters
    detection_threshold_sigma = StatusVar('detection_threshold_sigma', 5.0)
    min_spot_intensity = StatusVar('min_spot_intensity', 1000.0)
    max_candidates = StatusVar('max_candidates', 50)
    spot_diameter = StatusVar('spot_diameter', 1.5e-6)  # meters
    background_filter_size = StatusVar('background_filter_size', 15)  # pixels

    # Optimization parameters
    optimization_timeout = StatusVar('optimization_timeout', 30.0)  # seconds
    min_optimization_quality = StatusVar('min_optimization_quality', 0.5)
    enable_z_optimization = StatusVar('enable_z_optimization', True)

    # Behavior parameters
    auto_register_poi = StatusVar('auto_register_poi', True)
    auto_color_range = StatusVar('auto_color_range', True)

    # Signals for GUI updates
    sigStateChanged = QtCore.Signal(str)              # New state name
    sigScanComplete = QtCore.Signal()                 # Scan image ready
    sigCandidatesFound = QtCore.Signal(list)          # List of candidate dicts
    sigCandidateUpdate = QtCore.Signal(int, dict)     # (index, candidate_dict)
    sigProgressUpdate = QtCore.Signal(int, int)       # (current, total)
    sigAutoFindComplete = QtCore.Signal(dict)         # Summary results
    sigLogMessage = QtCore.Signal(str)                # Log message for GUI

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.threadlock = Mutex()
        self._state = 'idle'
        self._stop_requested = False
        self._candidates = []
        self._current_candidate_index = -1
        self._results = {
            'total_detected': 0,
            'accepted': 0,
            'rejected': 0,
            'skipped': 0
        }
        # CIP analysis engine
        self._cip = ConfocalImageAnalysis()

    def on_activate(self):
        """Initialize module."""
        self._state = 'idle'
        self._stop_requested = False
        self._candidates = []
        self.log.info('AutoNVFinderLogic activated.')

    def on_deactivate(self):
        """Clean up."""
        if self._state != 'idle':
            self.stop_auto_find()
        self.log.info('AutoNVFinderLogic deactivated.')

    # =========================================================================
    #                          PUBLIC API
    # =========================================================================

    @property
    def state(self):
        """Current state of the auto-finder."""
        return self._state

    @property
    def candidates(self):
        """List of current CandidateNV objects."""
        return list(self._candidates)

    @property
    def is_running(self):
        """Whether the auto-finder is currently active."""
        return self._state != 'idle'

    def start_auto_find(self):
        """Begin the full automated NV-finding pipeline.

        Pipeline: scan → CIP detection → optimize each → register as POI
        """
        if self._state != 'idle':
            self.log.warning('Auto NV finder is already running.')
            return

        self._stop_requested = False
        self._candidates = []
        self._current_candidate_index = -1
        self._results = {
            'total_detected': 0, 'accepted': 0, 'rejected': 0, 'skipped': 0
        }

        self._set_state('scanning')
        self._log('Starting automated NV center finding...')

        # Use the current confocal scan image (don't trigger a new scan)
        # The user should have already performed a scan
        self._on_scan_complete()

    def start_auto_find_with_scan(self):
        """Begin pipeline with a fresh confocal scan first.

        Triggers a new XY scan, then runs CIP detection when it completes.
        """
        if self._state != 'idle':
            self.log.warning('Auto NV finder is already running.')
            return

        self._stop_requested = False
        self._candidates = []
        self._current_candidate_index = -1
        self._results = {
            'total_detected': 0, 'accepted': 0, 'rejected': 0, 'skipped': 0
        }

        self._set_state('scanning')
        self._log('Starting fresh confocal scan for NV detection...')

        # Connect to scan completion signal
        self.confocallogic().signal_xy_image_updated.connect(
            self._check_scan_complete, QtCore.Qt.QueuedConnection)

        # Start a new XY scan
        self.confocallogic().start_scanning(zscan=False)

    def stop_auto_find(self):
        """Gracefully stop the auto-finding pipeline.

        The current optimization (if any) will complete before stopping.
        All results so far are preserved.
        """
        if self._state == 'idle':
            return

        self._log('Stop requested — finishing current operation...')
        self._stop_requested = True

        # If we're in detection phase, just stop
        if self._state in ('scanning', 'detecting'):
            self._finish('Stopped by user before optimization.')

    def set_threshold(self, sigma):
        """Set the detection threshold in noise sigma units."""
        self.detection_threshold_sigma = max(1.0, float(sigma))
        self.log.info('Detection threshold set to {0:.1f} sigma'.format(
            self.detection_threshold_sigma))

    def set_min_intensity(self, intensity):
        """Set the minimum absolute intensity threshold."""
        self.min_spot_intensity = max(0.0, float(intensity))

    def set_spot_diameter(self, diameter):
        """Set the expected NV spot diameter in meters."""
        self.spot_diameter = max(0.1e-6, float(diameter))

    # =========================================================================
    #                       INTERNAL PIPELINE
    # =========================================================================

    def _set_state(self, state):
        """Update the internal state and notify GUI."""
        self._state = state
        self.sigStateChanged.emit(state)

    def _log(self, message):
        """Log a message and emit it for the GUI."""
        self.log.info(message)
        self.sigLogMessage.emit(
            '[{0}] {1}'.format(
                time.strftime('%H:%M:%S'), message))

    def _check_scan_complete(self):
        """Check if the confocal scan has finished (for scan-triggered mode)."""
        # Disconnect after first call — we only need to know when scan finishes
        if not self.confocallogic().module_state() == 'locked':
            try:
                self.confocallogic().signal_xy_image_updated.disconnect(
                    self._check_scan_complete)
            except TypeError:
                pass
            self._on_scan_complete()

    def _on_scan_complete(self):
        """Handle scan completion — run CIP detection."""
        self.sigScanComplete.emit()
        self._log('Scan image acquired. Running CIP detection...')

        if self._stop_requested:
            self._finish('Stopped by user after scan.')
            return

        self._set_state('detecting')

        # Get the fluorescence image data
        scan_image = self.confocallogic().xy_image
        if scan_image is None or scan_image.size == 0:
            self._log('ERROR: No scan image available. Run a confocal XY scan first.')
            self._finish('No scan image available.')
            return

        # Run CIP detection
        candidates = self._detect_candidates(scan_image)
        self._candidates = candidates
        self._results['total_detected'] = len(candidates)

        if len(candidates) == 0:
            self._log('CIP detection found 0 candidates. '
                      'Try lowering the threshold or scanning a different area.')
            self._finish('No candidates found.')
            return

        self._log('CIP detection found {0} candidates.'.format(len(candidates)))
        self.sigCandidatesFound.emit([c.to_dict() for c in candidates])

        # Start optimization loop
        self._current_candidate_index = 0
        self._optimize_next_candidate()

    def _detect_candidates(self, scan_image):
        """Run the CIP (Color Image Processing) detection pipeline.

        This analyzes the fluorescence intensity data — the same data that
        produces the color image in the GUI — to find NV center candidates.

        @param np.ndarray scan_image: confocal xy_image array (rows × cols × 4+)
        @return list[CandidateNV]: detected candidates sorted by intensity
        """
        cip = self._cip

        # Extract fluorescence channel (the data behind the color image)
        fluorescence = scan_image[:, :, 3].astype(float)
        nrows, ncols = fluorescence.shape

        if nrows < 3 or ncols < 3:
            self.log.error('Scan image too small for CIP analysis.')
            return []

        # Extract coordinate arrays for physical position mapping
        x_coords = scan_image[0, :, 0]   # X positions along columns
        y_coords = scan_image[:, 0, 1]   # Y positions along rows
        z_current = float(scan_image[0, 0, 2])  # Current Z

        # Calculate pixel size for neighborhood conversion
        if ncols > 1:
            pixel_size_x = abs(x_coords[-1] - x_coords[0]) / (ncols - 1)
        else:
            pixel_size_x = self.spot_diameter
        if nrows > 1:
            pixel_size_y = abs(y_coords[-1] - y_coords[0]) / (nrows - 1)
        else:
            pixel_size_y = self.spot_diameter

        pixel_size = min(pixel_size_x, pixel_size_y)

        # Convert spot diameter to pixels
        spot_pixels = max(3, int(self.spot_diameter / pixel_size))
        # Ensure odd
        if spot_pixels % 2 == 0:
            spot_pixels += 1

        self._log('CIP: pixel size = {0:.2e} m, spot size = {1} px'.format(
            pixel_size, spot_pixels))

        # ---- Stage 1: Background estimation & subtraction ----
        background = cip.estimate_background(
            fluorescence, kernel_size=self.background_filter_size)
        corrected = cip.subtract_background(fluorescence, background)

        # ---- Stage 2: Intensity normalization (auto color range) ----
        # (Used internally; the actual color bar in the GUI is separate)
        normalized = cip.normalize_intensity(corrected)

        # ---- Stage 3: Noise estimation ----
        noise_sigma = cip.estimate_noise_level(corrected)
        self._log('CIP: noise sigma = {0:.1f} counts/s'.format(noise_sigma))

        if noise_sigma <= 0:
            noise_sigma = 1.0  # Prevent division by zero

        # ---- Stage 4: Intensity thresholding ----
        threshold = max(
            self.detection_threshold_sigma * noise_sigma,
            self.min_spot_intensity
        )
        mask = cip.threshold_intensity(corrected, threshold)
        n_above = np.sum(mask)
        self._log('CIP: threshold = {0:.1f} c/s, {1} pixels above'.format(
            threshold, n_above))

        if n_above == 0:
            return []

        # ---- Stage 5: Local maxima detection ----
        maxima_positions = cip.detect_local_maxima(
            corrected, mask, neighborhood_size=spot_pixels)
        self._log('CIP: {0} local maxima found'.format(len(maxima_positions)))

        if len(maxima_positions) == 0:
            return []

        # ---- Stage 6: Spot shape validation ----
        radius = max(1, spot_pixels // 2)
        valid_candidates = []
        for pos in maxima_positions:
            row, col = int(pos[0]), int(pos[1])
            is_valid, circularity = cip.validate_spot_shape(
                corrected, row, col, radius)
            if is_valid:
                valid_candidates.append((row, col, circularity))

        self._log('CIP: {0} candidates pass shape validation'.format(
            len(valid_candidates)))

        if len(valid_candidates) == 0:
            return []

        # ---- Stage 7: Spatial clustering ----
        positions = np.array([(r, c) for r, c, _ in valid_candidates])
        intensities = np.array([
            corrected[r, c] for r, c, _ in valid_candidates])
        circularities = {(r, c): circ for r, c, circ in valid_candidates}

        clustered = cip.cluster_detections(
            positions, intensities, min_distance=spot_pixels)
        self._log('CIP: {0} candidates after clustering'.format(len(clustered)))

        # ---- Stage 8: Sub-pixel Gaussian refinement ----
        candidates = []
        for (pos, intensity) in clustered[:self.max_candidates]:
            row, col = int(pos[0]), int(pos[1])
            refined = cip.refine_position_gaussian_2d(
                corrected, row, col, radius,
                x_coords=x_coords, y_coords=y_coords)

            # Use refined physical coordinates if available, else use pixel coords
            x_phys = refined['x'] if refined['x'] is not None else float(x_coords[min(col, ncols - 1)])
            y_phys = refined['y'] if refined['y'] is not None else float(y_coords[min(row, nrows - 1)])

            circ = circularities.get((row, col), 0.5)
            snr = intensity / noise_sigma if noise_sigma > 0 else 0.0
            confidence = cip.compute_detection_confidence(
                snr=snr, circularity=circ, fit_quality=refined['quality'])

            candidate = CandidateNV(
                x=x_phys, y=y_phys, z_estimate=z_current,
                pixel_row=row, pixel_col=col,
                intensity=intensity, confidence=confidence,
                circularity=circ
            )
            candidates.append(candidate)

        # ---- Stage 9: Sort by intensity (brightest first) ----
        candidates.sort(key=lambda c: c.intensity, reverse=True)

        return candidates

    def _optimize_next_candidate(self):
        """Start optimization on the next candidate in the queue."""
        if self._stop_requested:
            # Mark remaining as skipped
            for i in range(self._current_candidate_index, len(self._candidates)):
                self._candidates[i].status = 'skipped'
                self._results['skipped'] += 1
            self._finish('Stopped by user during optimization.')
            return

        if self._current_candidate_index >= len(self._candidates):
            self._finish('All candidates processed.')
            return

        candidate = self._candidates[self._current_candidate_index]
        idx = self._current_candidate_index

        self._set_state('optimizing')
        candidate.status = 'optimizing'
        self.sigCandidateUpdate.emit(idx, candidate.to_dict())
        self.sigProgressUpdate.emit(idx + 1, len(self._candidates))

        self._log('Optimizing candidate {0}/{1} at ({2:.2e}, {3:.2e}) m — '
                  '{4:.0f} c/s'.format(
                      idx + 1, len(self._candidates),
                      candidate.x, candidate.y, candidate.intensity))

        # Connect to optimizer completion signal
        self.optimizerlogic().sigRefocusFinished.connect(
            self._on_optimization_complete, QtCore.Qt.QueuedConnection)

        # Record start time for timeout check
        self._optimization_start_time = time.time()

        # Start the refocus at the candidate position
        initial_pos = [candidate.x, candidate.y, candidate.z_estimate]
        self.optimizerlogic().start_refocus(
            initial_pos=initial_pos,
            caller_tag='auto_nv_finder'
        )

    def _on_optimization_complete(self, caller_tag, optimal_pos):
        """Handle optimizer completion for the current candidate.

        @param str caller_tag: should be 'auto_nv_finder'
        @param list optimal_pos: [x, y, z] optimal position from optimizer
        """
        # Only handle our own refocus calls
        if caller_tag != 'auto_nv_finder':
            return

        # Disconnect the signal to avoid duplicate calls
        try:
            self.optimizerlogic().sigRefocusFinished.disconnect(
                self._on_optimization_complete)
        except TypeError:
            pass

        idx = self._current_candidate_index
        if idx < 0 or idx >= len(self._candidates):
            return

        candidate = self._candidates[idx]
        elapsed = time.time() - self._optimization_start_time

        # Check timeout
        if elapsed > self.optimization_timeout:
            candidate.status = 'rejected'
            candidate.rejection_reason = 'timeout ({0:.1f}s)'.format(elapsed)
            self._results['rejected'] += 1
            self._log('❌ Candidate {0} rejected: optimization timeout'.format(idx + 1))
        else:
            # Evaluate optimization quality
            opt_x, opt_y, opt_z = optimal_pos[0], optimal_pos[1], optimal_pos[2]
            distance = np.sqrt(
                (opt_x - candidate.x) ** 2 + (opt_y - candidate.y) ** 2)

            # Accept if the optimized position is within reasonable range
            max_displacement = self.spot_diameter * 2
            if distance < max_displacement:
                candidate.status = 'accepted'
                candidate.optimized_pos = (opt_x, opt_y, opt_z)
                self._results['accepted'] += 1
                self._log('✅ Candidate {0} accepted at ({1:.2e}, {2:.2e}) m — '
                          'displacement {3:.2e} m'.format(
                              idx + 1, opt_x, opt_y, distance))

                # Register as POI
                if self.auto_register_poi:
                    self._register_candidate_as_poi(candidate, idx)
            else:
                candidate.status = 'rejected'
                candidate.rejection_reason = (
                    'position displacement too large ({0:.2e} m)'.format(distance))
                self._results['rejected'] += 1
                self._log('❌ Candidate {0} rejected: {1}'.format(
                    idx + 1, candidate.rejection_reason))

        # Update GUI
        self.sigCandidateUpdate.emit(idx, candidate.to_dict())

        # Move to next candidate
        self._current_candidate_index += 1
        # Use a timer to allow GUI to update before processing next
        QtCore.QTimer.singleShot(100, self._optimize_next_candidate)

    def _register_candidate_as_poi(self, candidate, index):
        """Register an accepted candidate as a POI in the POI Manager.

        @param CandidateNV candidate: the accepted candidate
        @param int index: candidate index (for naming)
        """
        poi_pos = np.array(candidate.optimized_pos)
        name = 'NV_{0:03d}'.format(index + 1)

        try:
            self.poimanagerlogic().add_poi(position=poi_pos, name=name)
            candidate.poi_name = name
            self._set_state('registering')
            self._log('Registered {0} as POI at ({1:.2e}, {2:.2e}, {3:.2e}) m'.format(
                name, poi_pos[0], poi_pos[1], poi_pos[2]))
        except Exception as e:
            self.log.error('Failed to register POI: {0}'.format(str(e)))
            candidate.poi_name = 'registration_failed'

    def _finish(self, message):
        """Finish the auto-find pipeline and emit results.

        @param str message: summary message
        """
        self._log('Auto NV Find complete: {0}'.format(message))
        self._log('Results: {0} detected, {1} accepted, {2} rejected, {3} skipped'.format(
            self._results['total_detected'],
            self._results['accepted'],
            self._results['rejected'],
            self._results['skipped']
        ))

        self._set_state('idle')
        self.sigAutoFindComplete.emit(self._results.copy())
        self._stop_requested = False
