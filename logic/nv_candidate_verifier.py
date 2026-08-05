# -*- coding: utf-8 -*-
"""Repeated optical verification for extracted NV candidates.

This module deliberately wraps the existing :class:`OptimizerLogic` without
changing it.  The legacy optimizer only publishes a final coordinate and may
fall back to its seed when a fit fails, so this module archives its raw XY scan
and re-analyses that scan with :class:`logic.optimizer2.Optimizer2D`.

Supports three operating modes:

  - ``diagnostic`` (default): collects calibration data only.
    No automatic acceptance/rejection or POI registration.
  - ``hybrid``: applies optical acceptance gates AND registers accepted POIs
    to PoiManagerLogic, while still collecting the full calibration audit.
    This is the recommended mode for initial production use.
  - ``production``: applies gates and registers POIs.  Suppresses verbose
    per-attempt logging to reduce overhead for high-throughput runs.

The ``hybrid`` mode preserves the calibration pipeline established in
diagnostic mode, adding only acceptance gates and POI registration on top.
This enables real experiment loop integration while continuing to build
the calibration dataset needed for future drift compensation modules.
"""

from __future__ import division

import datetime
import json
import os
import re
import time
import uuid

import numpy as np
from qtpy import QtCore

from core.connector import Connector
from core.statusvariable import StatusVar
from logic.generic_logic import GenericLogic
from logic.optimizer2 import Optimizer2D
from logic.poi_verification_logger import POIVerificationLogger


def _utc_timestamp():
    """Return a sortable UTC timestamp with millisecond resolution."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def _json_value(value):
    """Convert NumPy values and tuples into values accepted by ``json``."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _safe_slug(value, fallback):
    """Return a filesystem-safe stable fragment, never an empty string."""
    slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('-._')
    return slug or fallback


def candidate_to_record(candidate, index):
    """Normalize a POIExtractor candidate or mapping for hardware use."""
    if isinstance(candidate, dict):
        getter = candidate.get
    else:
        getter = lambda name, default=None: getattr(candidate, name, default)

    candidate_id = str(getter('candidate_id', '') or 'candidate-{0:03d}'.format(index + 1))
    try:
        position = (float(getter('x')), float(getter('y')),
                    float(getter('z_estimate', 0.0)))
    except (TypeError, ValueError):
        raise ValueError('candidate {0} must provide finite x, y, z_estimate coordinates'.format(candidate_id))
    if not all(np.isfinite(position)):
        raise ValueError('candidate {0} has non-finite coordinates'.format(candidate_id))

    return {
        'candidate_id': candidate_id,
        'candidate_label': _safe_slug(candidate_id, 'candidate-{0:03d}'.format(index + 1)),
        'seed_position_m': list(position),
        'region_id': str(getter('region_id', '') or ''),
        'pixel_row': getter('pixel_row', None),
        'pixel_col': getter('pixel_col', None),
        'classification': str(getter('classification', '') or ''),
        'overall_score': getter('overall_score', None),
        'attempts': [],
        'status': 'queued',
    }


def optimizer2_result_record(result):
    """Turn an ``Optimizer2DResult`` into an audit-friendly dictionary."""
    return {
        'success': bool(result.success),
        'position_m': None if result.position_m is None else list(result.position_m),
        'sigma_m': None if result.sigma_m is None else list(result.sigma_m),
        'amplitude': result.amplitude,
        'offset': result.offset,
        'r_squared': result.r_squared,
        'sampled_bounds_m': list(result.sampled_bounds_m),
        'pitch_m': list(result.pitch_m),
        'sample_shape': list(result.sample_shape),
        'is_edge_fit': bool(result.is_edge_fit),
        'error': result.error,
    }


def xy_offset_record(seed_position_m, measured_position_m):
    """Return signed X/Y displacement and radial magnitude, or ``None``.

    This is deliberately a recorded calibration observable, not a current
    acceptance gate.  The bounds check belongs to the actual sampled support,
    while the acceptable seed offset must be calibrated on live data.
    """
    if measured_position_m is None:
        return None
    try:
        delta_x = float(measured_position_m[0]) - float(seed_position_m[0])
        delta_y = float(measured_position_m[1]) - float(seed_position_m[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not np.isfinite(delta_x) or not np.isfinite(delta_y):
        return None
    return {
        'delta_x_m': delta_x,
        'delta_y_m': delta_y,
        'radial_m': float(np.hypot(delta_x, delta_y)),
    }


def xy_distance_m(first_position, second_position):
    """Return radial XY distance in metres, or ``None``."""
    offset = xy_offset_record(first_position, second_position)
    return None if offset is None else offset['radial_m']


def analysis_gate_failures(analysis, min_r_squared=0.6,
                           sigma_range_m=(0.05e-6, 0.4e-6)):
    """Return failed optical gates for one bounded XY analysis record."""
    failures = []
    if not analysis or not bool(analysis.get('success')):
        failures.append('xy_fit_failed')
        return failures
    if bool(analysis.get('is_edge_fit')):
        failures.append('edge_fit')
    r_squared = analysis.get('r_squared')
    if r_squared is None or not np.isfinite(float(r_squared)):
        failures.append('r2_missing')
    elif float(r_squared) <= float(min_r_squared):
        failures.append('r2_low')
    sigma = analysis.get('sigma_m')
    if sigma is None or len(sigma) < 2:
        failures.append('sigma_missing')
    else:
        sigma_min, sigma_max = (float(sigma_range_m[0]), float(sigma_range_m[1]))
        try:
            sigma_x = float(sigma[0])
            sigma_y = float(sigma[1])
        except (TypeError, ValueError):
            failures.append('sigma_malformed')
        else:
            if (not np.isfinite(sigma_x) or not np.isfinite(sigma_y) or
                    sigma_x < sigma_min or sigma_x > sigma_max or
                    sigma_y < sigma_min or sigma_y > sigma_max):
                failures.append('sigma_out_of_range')
    position = analysis.get('position_m')
    bounds = analysis.get('sampled_bounds_m')
    if position is None or bounds is None or len(bounds) < 4:
        failures.append('sampled_support_missing')
    else:
        x_min, x_max, y_min, y_max = [float(value) for value in bounds[:4]]
        center_x, center_y = [float(value) for value in position[:2]]
        if not (x_min <= center_x <= x_max and y_min <= center_y <= y_max):
            failures.append('outside_sampled_support')
    return failures


def is_worthy_analysis(analysis, min_r_squared=0.6,
                       sigma_range_m=(0.05e-6, 0.4e-6)):
    """Return whether an XY analysis passes the configured worthy gates."""
    return not analysis_gate_failures(analysis, min_r_squared, sigma_range_m)


def analyse_legacy_xy_scan(xy_refocus_image, x_values, y_values, seed_position_m,
                           optimization_channel=0):
    """Boundedly re-fit the raw legacy XY scan, returning a diagnostic record.

    This function does not treat the legacy coordinate as fit evidence.  A
    missing or malformed scan becomes an explicit failed analysis record.
    """
    try:
        image = np.asarray(xy_refocus_image, dtype=float)
        x_values = np.asarray(x_values, dtype=float)
        y_values = np.asarray(y_values, dtype=float)
        channel = 3 + int(optimization_channel)
        if image.ndim != 3 or channel >= image.shape[2]:
            raise ValueError('legacy XY image has no requested count channel')
        result = Optimizer2D().fit_local(
            image[:, :, channel], x_values, y_values,
            seed_position_m=seed_position_m[:2])
        record = optimizer2_result_record(result)
        record['fitted_offset_from_seed_xy_m'] = xy_offset_record(
            seed_position_m, result.position_m)
        return record
    except (TypeError, ValueError, IndexError) as error:
        return {
            'success': False,
            'position_m': None,
            'sigma_m': None,
            'amplitude': None,
            'offset': None,
            'r_squared': None,
            'sampled_bounds_m': None,
            'pitch_m': None,
            'sample_shape': None,
            'is_edge_fit': False,
            'fitted_offset_from_seed_xy_m': None,
            'error': 'legacy XY re-analysis unavailable: {0}'.format(error),
        }


class DiagnosticRetryPolicy:
    """Conservative 2--4 attempt policy used only for data collection.

    A normally bounded, non-edge re-analysis can complete after the minimum
    attempts.  Failures and edge results consume the whole budget and remain
    ``unresolved``.  The policy intentionally has no ``accepted`` or
    ``rejected`` action.
    """

    def __init__(self, minimum_attempts=2, maximum_attempts=4):
        if int(minimum_attempts) < 1 or int(maximum_attempts) < int(minimum_attempts):
            raise ValueError('require 1 <= minimum_attempts <= maximum_attempts')
        self.minimum_attempts = int(minimum_attempts)
        self.maximum_attempts = int(maximum_attempts)

    @staticmethod
    def is_normal_attempt(attempt):
        analysis = attempt.get('optimizer2_xy', {})
        return (attempt.get('outcome') == 'completed' and
                bool(analysis.get('success')) and
                not bool(analysis.get('is_edge_fit')))

    def next_action(self, attempts):
        """Return ``retry``, ``diagnostic_complete``, or ``unresolved``."""
        count = len(attempts)
        if count < self.minimum_attempts:
            return 'retry'
        if all(self.is_normal_attempt(attempt) for attempt in attempts):
            return 'diagnostic_complete'
        if count < self.maximum_attempts:
            return 'retry'
        return 'unresolved'


class VerificationAuditStore:
    """Persist a crash-resilient JSON manifest plus one NPZ per attempt."""

    def __init__(self, root_directory, run_id, metadata=None):
        self.run_directory = os.path.abspath(os.path.join(root_directory, run_id))
        os.makedirs(self.run_directory, exist_ok=True)
        self.manifest_path = os.path.join(self.run_directory, 'manifest.json')
        self.manifest = {
            'schema_version': 1,
            'run_id': run_id,
            'created_utc': _utc_timestamp(),
            'diagnostic_only': True,
            'metadata': _json_value(metadata or {}),
            'attempts': [],
            'terminal': False,
        }
        self._write_manifest()

    def _write_manifest(self):
        temporary_path = self.manifest_path + '.tmp'
        with open(temporary_path, 'w', encoding='utf-8') as stream:
            json.dump(_json_value(self.manifest), stream, indent=2, sort_keys=True)
        os.replace(temporary_path, self.manifest_path)

    def record_attempt(self, candidate_label, attempt_number, attempt, arrays=None):
        """Store an immutable raw capture and append its metadata to manifest."""
        record = dict(attempt)
        archive_name = None
        arrays = arrays or {}
        finite_arrays = {name: np.asarray(value) for name, value in arrays.items()
                         if value is not None}
        if finite_arrays:
            archive_name = 'attempt_{0}_a{1:02d}.npz'.format(
                _safe_slug(candidate_label, 'candidate'), int(attempt_number))
            np.savez_compressed(os.path.join(self.run_directory, archive_name), **finite_arrays)
        record['raw_archive'] = archive_name
        self.manifest['attempts'].append(_json_value(record))
        self._write_manifest()
        return archive_name

    def finish(self, result):
        self.manifest['terminal'] = True
        self.manifest['finished_utc'] = _utc_timestamp()
        self.manifest['result'] = _json_value(result)
        self._write_manifest()


class NVCandidateVerifier(GenericLogic):
    """Run repeat optical diagnostics through the legacy optimizer safely."""

    optimizerlogic = Connector(interface='OptimizerLogic')
    savelogic = Connector(interface='SaveLogic', optional=True)
    poimanagerlogic = Connector(interface='PoiManagerLogic', optional=True)

    operating_mode = StatusVar('operating_mode', 'diagnostic')
    minimum_attempts = StatusVar('minimum_attempts', 2)
    maximum_attempts = StatusVar('maximum_attempts', 4)
    stage1_max_attempts = StatusVar('stage1_max_attempts', 4)
    stage2_max_attempts = StatusVar('stage2_max_attempts', 5)
    worthy_min_xy_r_squared = StatusVar('worthy_min_xy_r_squared', 0.6)
    worthy_sigma_min_m = StatusVar('worthy_sigma_min_m', 0.05e-6)
    worthy_sigma_max_m = StatusVar('worthy_sigma_max_m', 0.4e-6)
    poi_gaussian_center_tolerance_m = StatusVar(
        'poi_gaussian_center_tolerance_m', 50e-9)
    update_seed_after_optimizer = StatusVar('update_seed_after_optimizer', True)
    auto_register_poi = StatusVar('auto_register_poi', True)
    attempt_timeout_s = StatusVar('attempt_timeout_s', 90.0)
    timeout_cleanup_s = StatusVar('timeout_cleanup_s', 10.0)
    audit_subdirectory = StatusVar('audit_subdirectory', 'NVCandidateVerifier')

    sigVerificationProgress = QtCore.Signal(str, str, int, int)
    sigCandidateVerificationUpdated = QtCore.Signal(object)
    sigVerificationFinished = QtCore.Signal(object)
    sigVerificationError = QtCore.Signal(str, str)
    sigCandidateAccepted = QtCore.Signal(object)
    sigCandidateRejected = QtCore.Signal(object)

    # ------------------------------------------------------------------
    # Backward-compatible property for configs still using diagnostic_only
    # ------------------------------------------------------------------
    @property
    def diagnostic_only(self):
        """Backward-compatible read accessor.

        Returns True when operating_mode is 'diagnostic', False otherwise.
        Existing code that checks ``self.diagnostic_only`` will continue to
        work without modification.
        """
        return self._effective_mode() == 'diagnostic'

    @diagnostic_only.setter
    def diagnostic_only(self, value):
        """Backward-compatible write accessor.

        If a Qudi config still contains ``diagnostic_only: True``, this maps
        it to ``operating_mode = 'diagnostic'``.  ``diagnostic_only: False``
        maps to ``operating_mode = 'hybrid'``.
        """
        if isinstance(value, bool):
            self.operating_mode = 'diagnostic' if value else 'hybrid'

    def _effective_mode(self):
        """Return the validated operating mode string."""
        mode = str(self.operating_mode).strip().lower()
        if mode not in ('diagnostic', 'hybrid', 'production'):
            self.log.warning(
                'Unknown operating_mode "{0}", falling back to "diagnostic".'
                .format(self.operating_mode))
            return 'diagnostic'
        return mode

    def on_activate(self):
        """Connect once to the optimizer and initialise the watchdog."""
        self.log.info('NVCandidateVerifier activating in "{0}" mode.'.format(
            self._effective_mode()))
        self._optimizer = self.optimizerlogic()
        self._active = False
        self._stop_requested = False
        self._current_index = -1
        self._attempt_started_at = None
        self._attempt_started_utc = None
        self._active_tag = None
        self._batch = None
        self._audit = None
        self._poi_logger = None
        self._timeout_requested = False
        self._current_stage = 'stage1'
        self._watchdog = QtCore.QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_attempt_timeout)
        self._timeout_cleanup = QtCore.QTimer(self)
        self._timeout_cleanup.setSingleShot(True)
        self._timeout_cleanup.timeout.connect(self._on_timeout_cleanup)
        self._optimizer.sigRefocusFinished.connect(
            self._on_refocus_finished, QtCore.Qt.QueuedConnection)
        return 0

    def on_deactivate(self):
        """Disconnect only this module's slot; never disturb other callers."""
        if hasattr(self, '_watchdog'):
            self._watchdog.stop()
            self._timeout_cleanup.stop()
        if hasattr(self, '_optimizer'):
            try:
                self._optimizer.sigRefocusFinished.disconnect(self._on_refocus_finished)
            except (TypeError, RuntimeError):
                pass
        return 0

    def _audit_root(self):
        save_logic = self.savelogic()
        if save_logic is not None:
            return save_logic.get_path_for_module(module_name=self.audit_subdirectory)
        return os.path.join(os.getcwd(), 'data', self.audit_subdirectory)

    def verify_batch(self, candidates, run_context=None):
        """Start a non-blocking staged verification batch and return run ID."""
        if self._active:
            raise RuntimeError('an NVCandidateVerifier batch is already active')

        records = [candidate_to_record(candidate, index)
                   for index, candidate in enumerate(candidates)]
        if not records:
            raise ValueError('verify_batch requires at least one candidate')
        labels = [record['candidate_label'] for record in records]
        if len(set(labels)) != len(labels):
            raise ValueError('candidate_id values must be unique within one verification batch')
        policy = self._policy_snapshot()
        run_id = 'nvverify_{0}_{1}'.format(_utc_timestamp(), uuid.uuid4().hex[:8])
        metadata = {
            'run_context': run_context or {},
            'policy': policy,
            'legacy_optimizer_settings': self._optimizer_settings_snapshot(),
        }
        self._poi_logger = POIVerificationLogger(
            self._audit_root(), run_id=run_id,
            run_context=run_context or {}, policy_snapshot=metadata)
        for index, candidate in enumerate(candidates):
            self._poi_logger.start_candidate(candidate, index=index)
        self._batch = {
            'run_id': run_id,
            'audit_directory': self._poi_logger.run_directory,
            'started_utc': _utc_timestamp(),
            'diagnostic_only': bool(self.diagnostic_only),
            'operating_mode': self._effective_mode(),
            'run_context': run_context or {},
            'candidates': records,
            'status': 'running',
            'policy': policy,
        }
        self._policy = policy
        self._active = True
        self._stop_requested = False
        self._current_index = 0
        self._current_stage = 'stage1'
        QtCore.QTimer.singleShot(0, self._start_current_attempt)
        return run_id

    def stop_verification(self):
        """Request a safe stop and retain an audit record for the active run."""
        if not self._active:
            return
        self._stop_requested = True
        if self._active_tag is not None:
            self._optimizer.stop_refocus()
            self._timeout_cleanup.start(max(1, int(float(self.timeout_cleanup_s) * 1000)))
        else:
            self._finish_batch('stopped')

    def _optimizer_settings_snapshot(self):
        names = ('refocus_XY_size', 'optimizer_XY_res', 'refocus_Z_size',
                 'optimizer_Z_res', 'optimization_sequence', 'opt_channel',
                 '_clock_frequency')
        return {name: _json_value(getattr(self._optimizer, name, None))
                for name in names}

    def _policy_snapshot(self):
        return {
            'operating_mode': self._effective_mode(),
            'diagnostic_only': bool(self.diagnostic_only),
            'stage1_max_attempts': int(self.stage1_max_attempts),
            'stage2_max_attempts': int(self.stage2_max_attempts),
            'worthy_min_xy_r_squared': float(self.worthy_min_xy_r_squared),
            'worthy_sigma_min_m': float(self.worthy_sigma_min_m),
            'worthy_sigma_max_m': float(self.worthy_sigma_max_m),
            'poi_gaussian_center_tolerance_m': float(
                self.poi_gaussian_center_tolerance_m),
            'update_seed_after_optimizer': bool(self.update_seed_after_optimizer),
            'auto_register_poi': bool(self.auto_register_poi),
            'attempt_timeout_s': float(self.attempt_timeout_s),
            'timeout_cleanup_s': float(self.timeout_cleanup_s),
        }

    def _sigma_range_m(self):
        return (float(self.worthy_sigma_min_m), float(self.worthy_sigma_max_m))

    def _start_current_attempt(self):
        if not self._active:
            return
        if self._stop_requested:
            self._finish_batch('stopped')
            return
        if self._current_index >= len(self._batch['candidates']):
            self._finish_batch('completed')
            return

        candidate = self._batch['candidates'][self._current_index]
        candidate.setdefault('current_seed_position_m',
                             list(candidate['seed_position_m']))
        candidate.setdefault('stage', 'stage1')
        candidate.setdefault('stage1_attempts', 0)
        candidate.setdefault('final_state_attempts', 0)
        self._current_stage = candidate['stage']
        attempt_number = (
            int(candidate['stage1_attempts']) + 1
            if self._current_stage == 'stage1'
            else int(candidate['final_state_attempts']) + 1)
        if self._optimizer.module_state() != 'idle':
            self._record_non_scan_attempt(candidate, attempt_number, 'hardware_busy',
                                          'legacy optimizer module is not idle')
            candidate['status'] = 'unresolved'
            self._advance_candidate()
            return

        self._active_tag = '{0}:{1}:a{2:02d}'.format(
            self._batch['run_id'], candidate['candidate_label'],
            len(candidate['attempts']) + 1)
        self._attempt_started_at = time.monotonic()
        self._attempt_started_utc = _utc_timestamp()
        self._timeout_requested = False
        candidate['status'] = 'scanning_{0}'.format(self._current_stage)
        self.sigVerificationProgress.emit(self._batch['run_id'], candidate['candidate_id'],
                                          attempt_number,
                                          self._stage_max_attempts(self._current_stage))
        self.sigCandidateVerificationUpdated.emit(dict(candidate))
        self._watchdog.start(max(1, int(float(self.attempt_timeout_s) * 1000)))
        try:
            self._optimizer.start_refocus(
                initial_pos=list(candidate['current_seed_position_m']),
                caller_tag=self._active_tag)
        except Exception as error:
            self._watchdog.stop()
            self._active_tag = None
            self._record_non_scan_attempt(candidate, attempt_number, 'hardware_error', str(error))
            candidate['status'] = 'unresolved'
            self._advance_candidate()

    def _stage_max_attempts(self, stage):
        if stage == 'stage1':
            return int(self.stage1_max_attempts)
        return int(self.stage2_max_attempts)

    def _on_attempt_timeout(self):
        """Ask the legacy optimizer to stop, then await its correlated signal."""
        if self._active and self._active_tag is not None:
            self._timeout_requested = True
            self._optimizer.stop_refocus()
            self._timeout_cleanup.start(max(1, int(float(self.timeout_cleanup_s) * 1000)))

    def _on_timeout_cleanup(self):
        """Persist a timeout if the legacy optimizer never emits completion.

        No further candidate is started because hardware ownership is unknown;
        a later legacy signal is ignored rather than mis-correlated to another
        candidate.
        """
        if not self._active or self._active_tag is None:
            return
        candidate = self._batch['candidates'][self._current_index]
        attempt_number = self._current_stage_attempt_number(candidate)
        elapsed = time.monotonic() - self._attempt_started_at
        attempt = {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'attempt_number': attempt_number,
            'caller_tag': self._active_tag,
            'started_utc': self._attempt_started_utc,
            'elapsed_s': elapsed,
            'outcome': 'stopped' if self._stop_requested else 'timeout',
            'stage': self._current_stage,
            'seed_position_m': candidate.get('current_seed_position_m',
                                             candidate['seed_position_m']),
            'error': 'no matching sigRefocusFinished after stop_refocus cleanup interval',
            'optimizer2_xy': {
                'success': False,
                'is_edge_fit': False,
                'error': 'no raw scan received before timeout cleanup',
            },
        }
        self._record_attempt(candidate, attempt_number, attempt, None)
        candidate['status'] = 'unresolved'
        self._finalize_logged_candidate(candidate, 'unresolved',
                                        rejection_reason=attempt['error'])
        self.sigVerificationError.emit(self._batch['run_id'], attempt['error'])
        self._active_tag = None
        self._finish_batch('stopped' if self._stop_requested else 'timed_out')

    def _on_refocus_finished(self, caller_tag, optimal_position):
        if not self._active or caller_tag != self._active_tag:
            return
        self._watchdog.stop()
        self._timeout_cleanup.stop()
        candidate = self._batch['candidates'][self._current_index]
        attempt_number = self._current_stage_attempt_number(candidate)
        elapsed = time.monotonic() - self._attempt_started_at
        attempt, arrays = self._capture_attempt(candidate, attempt_number,
                                                optimal_position, elapsed)
        if self._stop_requested:
            attempt['outcome'] = 'stopped'
        elif self._timeout_requested:
            attempt['outcome'] = 'timeout'
        self._record_attempt(candidate, attempt_number, attempt, arrays)
        self._active_tag = None

        if self._stop_requested:
            candidate['status'] = 'stopped'
            self._finalize_logged_candidate(candidate, 'skipped',
                                            rejection_reason='stopped by user')
            self._finish_batch('stopped')
            return
        action = self._evaluate_after_attempt(candidate, attempt)
        candidate['status'] = action
        self.sigCandidateVerificationUpdated.emit(dict(candidate))
        if action == 'retry':
            QtCore.QTimer.singleShot(0, self._start_current_attempt)
        elif action == 'enter_final_state':
            candidate['stage'] = 'final_state'
            QtCore.QTimer.singleShot(0, self._start_current_attempt)
        else:
            self._advance_candidate()

    def _current_stage_attempt_number(self, candidate):
        if self._current_stage == 'stage1':
            return int(candidate.get('stage1_attempts', 0)) + 1
        return int(candidate.get('final_state_attempts', 0)) + 1

    def _capture_attempt(self, candidate, attempt_number, optimal_position, elapsed):
        xy_image = getattr(self._optimizer, 'xy_refocus_image', None)
        x_values = getattr(self._optimizer, '_X_values', None)
        y_values = getattr(self._optimizer, '_Y_values', None)
        opt_channel = getattr(self._optimizer, 'opt_channel', 0)
        seed_position = candidate.get('current_seed_position_m',
                                      candidate['seed_position_m'])
        analysis = analyse_legacy_xy_scan(xy_image, x_values, y_values,
                                          seed_position, opt_channel)
        legacy_sigma = [getattr(self._optimizer, 'optim_sigma_x', None),
                        getattr(self._optimizer, 'optim_sigma_y', None),
                        getattr(self._optimizer, 'optim_sigma_z', None)]
        legacy_xy_fit_evidence = all(value is not None and np.isfinite(value) and value > 0
                                     for value in legacy_sigma[:2])
        arrays = {
            'xy_refocus_image': xy_image,
            'xy_x_values_m': x_values,
            'xy_y_values_m': y_values,
            'z_refocus_line': getattr(self._optimizer, 'z_refocus_line', None),
            'z_values_m': getattr(self._optimizer, '_zimage_Z_values', None),
            'z_fit_data': getattr(self._optimizer, 'z_fit_data', None),
            'seed_position_m': seed_position,
            'legacy_return_position_m': optimal_position,
        }
        gate_failures = analysis_gate_failures(
            analysis, self.worthy_min_xy_r_squared, self._sigma_range_m())
        poi_gaussian_distance = xy_distance_m(optimal_position,
                                             analysis.get('position_m'))
        final_tolerance = float(self.poi_gaussian_center_tolerance_m)
        final_gate_failures = list(gate_failures)
        if self._current_stage == 'final_state':
            if poi_gaussian_distance is None:
                final_gate_failures.append('poi_gaussian_distance_missing')
            elif poi_gaussian_distance > final_tolerance:
                final_gate_failures.append('poi_gaussian_distance_large')
        return {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'stage': self._current_stage,
            'attempt_number': attempt_number,
            'caller_tag': self._active_tag,
            'started_utc': self._attempt_started_utc,
            'elapsed_s': elapsed,
            'outcome': 'completed',
            'seed_position_m': seed_position,
            'legacy_return_position_m': _json_value(optimal_position),
            'legacy_return_offset_from_seed_xy_m': xy_offset_record(
                seed_position, optimal_position),
            'legacy_sigma_m': _json_value(legacy_sigma),
            'legacy_xy_fit_evidence': bool(legacy_xy_fit_evidence),
            'legacy_xy_fit_note': ('positive legacy sigmas observed; raw bounded re-analysis remains authoritative'
                                   if legacy_xy_fit_evidence else
                                   'indeterminate: zero/absent legacy sigma can represent a fallback'),
            'optimizer2_xy': analysis,
            'gate_failures': final_gate_failures,
            'worthy_candidate': not gate_failures,
            'final_state_fit': (
                self._current_stage == 'final_state' and not final_gate_failures),
            'poi_gaussian_distance_xy_m': poi_gaussian_distance,
        }, arrays

    def _record_non_scan_attempt(self, candidate, attempt_number, outcome, error):
        attempt = {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'stage': self._current_stage,
            'attempt_number': attempt_number,
            'caller_tag': None,
            'started_utc': _utc_timestamp(),
            'elapsed_s': 0.0,
            'outcome': outcome,
            'seed_position_m': candidate.get('current_seed_position_m',
                                             candidate['seed_position_m']),
            'error': error,
            'gate_failures': [outcome],
            'optimizer2_xy': {'success': False, 'is_edge_fit': False, 'error': error},
        }
        self._record_attempt(candidate, attempt_number, attempt, None)
        self._finalize_logged_candidate(candidate, 'unresolved',
                                        rejection_reason=error)
        self.sigVerificationError.emit(self._batch['run_id'], str(error))

    def _record_attempt(self, candidate, attempt_number, attempt, arrays):
        next_seed, next_seed_source = self._choose_next_seed(candidate, attempt)
        if bool(self.update_seed_after_optimizer) and next_seed is not None:
            attempt['next_seed_position_m'] = next_seed
            attempt['next_seed_source'] = next_seed_source
        else:
            attempt['next_seed_position_m'] = None
            attempt['next_seed_source'] = 'unchanged'
        if self._poi_logger is not None:
            analysis = attempt.get('optimizer2_xy', {})
            self._poi_logger.log_attempt(
                candidate['candidate_id'],
                stage=attempt.get('stage', self._current_stage),
                attempt_number=attempt_number,
                seed_position_m=attempt.get('seed_position_m'),
                optimizer_return_position_m=attempt.get('legacy_return_position_m'),
                gaussian_center_xy_m=analysis.get('position_m'),
                poi_center_xy_m=attempt.get('legacy_return_position_m'),
                next_seed_position_m=attempt.get('next_seed_position_m'),
                outcome=attempt.get('outcome', 'completed'),
                r_squared_xy=analysis.get('r_squared'),
                sigma_xy_m=analysis.get('sigma_m'),
                candidate_score=candidate.get('overall_score'),
                gate_failures=attempt.get('gate_failures', []),
                elapsed_s=attempt.get('elapsed_s'),
                raw_arrays=arrays,
                error=attempt.get('error'),
                metadata={
                    'caller_tag': attempt.get('caller_tag'),
                    'legacy_sigma_m': attempt.get('legacy_sigma_m'),
                    'legacy_xy_fit_evidence': attempt.get('legacy_xy_fit_evidence'),
                    'next_seed_source': attempt.get('next_seed_source'),
                    'raw_optimizer2_xy': analysis,
                })
        if attempt.get('stage') == 'stage1':
            candidate['stage1_attempts'] = int(candidate.get('stage1_attempts', 0)) + 1
        elif attempt.get('stage') == 'final_state':
            candidate['final_state_attempts'] = int(
                candidate.get('final_state_attempts', 0)) + 1
        candidate['attempts'].append(attempt)
        if bool(self.update_seed_after_optimizer) and next_seed is not None:
            candidate['current_seed_position_m'] = next_seed

    def _choose_next_seed(self, candidate, attempt):
        seed = attempt.get('seed_position_m') or candidate.get('current_seed_position_m')
        z_value = None
        legacy_return = attempt.get('legacy_return_position_m')
        if legacy_return is not None and len(legacy_return) >= 3:
            try:
                z_value = float(legacy_return[2])
            except (TypeError, ValueError):
                z_value = None
        if z_value is None and seed is not None and len(seed) >= 3:
            z_value = float(seed[2])
        analysis_position = attempt.get('optimizer2_xy', {}).get('position_m')
        if analysis_position is not None:
            try:
                return [float(analysis_position[0]), float(analysis_position[1]),
                        z_value], 'gaussian_center'
            except (TypeError, ValueError, IndexError):
                pass
        normalized_return = None
        if legacy_return is not None:
            try:
                normalized_return = [float(legacy_return[0]), float(legacy_return[1]),
                                     float(legacy_return[2])]
            except (TypeError, ValueError, IndexError):
                normalized_return = None
        if normalized_return is not None:
            return normalized_return, 'optimizer_return'
        return None, 'unchanged'

    def _advance_candidate(self):
        self._current_index += 1
        self._current_stage = 'stage1'
        QtCore.QTimer.singleShot(0, self._start_current_attempt)

    def _evaluate_after_attempt(self, candidate, attempt):
        if attempt.get('outcome') in ('timeout', 'hardware_error', 'hardware_busy'):
            self._finalize_logged_candidate(
                candidate, 'unresolved',
                rejection_reason=attempt.get('error') or attempt.get('outcome'))
            return 'unresolved'

        if attempt.get('stage') == 'stage1':
            if bool(attempt.get('worthy_candidate')):
                candidate['stage'] = 'final_state'
                candidate['status'] = 'enter_final_state'
                return 'enter_final_state'
            if int(candidate.get('stage1_attempts', 0)) >= int(self.stage1_max_attempts):
                candidate['status'] = 'rejected'
                self._finalize_logged_candidate(
                    candidate, 'rejected',
                    rejection_reason='stage1_budget_exhausted:{0}'.format(
                        ','.join(attempt.get('gate_failures', []))))
                self.sigCandidateRejected.emit(dict(candidate))
                return 'rejected'
            return 'retry'

        if attempt.get('stage') == 'final_state':
            if bool(attempt.get('final_state_fit')):
                return self._accept_candidate(candidate, attempt)
            if int(candidate.get('final_state_attempts', 0)) >= int(self.stage2_max_attempts):
                candidate['status'] = 'rejected'
                self._finalize_logged_candidate(
                    candidate, 'rejected',
                    rejection_reason='final_state_budget_exhausted:{0}'.format(
                        ','.join(attempt.get('gate_failures', []))))
                self.sigCandidateRejected.emit(dict(candidate))
                return 'rejected'
            return 'retry'

        self._finalize_logged_candidate(candidate, 'unresolved',
                                        rejection_reason='unknown verifier stage')
        return 'unresolved'

    def _accept_candidate(self, candidate, attempt):
        analysis = attempt.get('optimizer2_xy', {})
        gaussian_xy = analysis.get('position_m')
        if gaussian_xy is None:
            self._finalize_logged_candidate(
                candidate, 'unresolved',
                rejection_reason='missing Gaussian centre at acceptance')
            return 'unresolved'
        z_value = candidate.get('current_seed_position_m',
                                candidate['seed_position_m'])[2]
        accepted_position = [float(gaussian_xy[0]), float(gaussian_xy[1]),
                             float(z_value)]
        candidate['accepted_position_m'] = accepted_position
        candidate['status'] = 'optically_verified'
        poi_name = self._candidate_poi_name(candidate)

        mode = self._effective_mode()
        registration_status = 'diagnostic_not_registered'

        # In hybrid and production modes, register the POI
        if mode in ('hybrid', 'production'):
            if bool(self.auto_register_poi) and self.poimanagerlogic() is not None:
                try:
                    self.poimanagerlogic().add_poi(
                        position=np.array(accepted_position), name=poi_name)
                    registration_status = 'registered'
                except Exception as error:
                    registration_status = 'registration_failed:{0}'.format(error)
                    candidate['status'] = 'registration_failed'
            else:
                registration_status = 'poi_manager_unavailable'

        self._finalize_logged_candidate(
            candidate, 'accepted',
            accepted_position_m=accepted_position,
            poi_name=poi_name,
            registration_status=registration_status)

        # Emit acceptance signal for downstream consumers (orchestrator,
        # PulsedMeasurementExecutor).  In diagnostic mode the signal is
        # still emitted so that GUI can update, but the candidate record
        # will carry 'diagnostic_not_registered' as its registration_status.
        accepted_record = {
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'accepted_position_m': accepted_position,
            'poi_name': poi_name,
            'registration_status': registration_status,
            'operating_mode': mode,
            'region_id': candidate.get('region_id', ''),
            'overall_score': candidate.get('overall_score'),
            'stage1_attempts': candidate.get('stage1_attempts', 0),
            'final_state_attempts': candidate.get('final_state_attempts', 0),
        }
        self.sigCandidateAccepted.emit(accepted_record)
        return candidate['status']


    def _candidate_poi_name(self, candidate):
        region = _safe_slug(candidate.get('region_id') or 'R000', 'R000')
        token = _safe_slug(candidate.get('candidate_label') or
                           candidate.get('candidate_id'), 'candidate')
        return 'NV_{0}_{1}'.format(region, token)

    def _finalize_logged_candidate(self, candidate, final_status,
                                   accepted_position_m=None,
                                   rejection_reason=None, poi_name=None,
                                   registration_status=None):
        if candidate.get('_logged_final_decision'):
            return
        candidate['_logged_final_decision'] = True
        if self._poi_logger is not None:
            self._poi_logger.finalize_candidate(
                candidate['candidate_id'],
                final_status=final_status,
                accepted_position_m=accepted_position_m,
                rejection_reason=rejection_reason,
                poi_name=poi_name,
                registration_status=registration_status,
                metadata={
                    'stage1_attempts': candidate.get('stage1_attempts', 0),
                    'final_state_attempts': candidate.get('final_state_attempts', 0),
                    'diagnostic_only': bool(self.diagnostic_only),
                })

    def _finish_batch(self, status):
        if not self._active:
            return
        self._watchdog.stop()
        self._timeout_cleanup.stop()
        if status in ('stopped', 'timed_out'):
            for candidate in self._batch['candidates'][self._current_index + 1:]:
                if candidate['status'] == 'queued':
                    candidate['status'] = 'skipped'
                    self._finalize_logged_candidate(
                        candidate, 'skipped',
                        rejection_reason='batch {0}'.format(status))
        self._batch['status'] = status
        self._batch['finished_utc'] = _utc_timestamp()
        if self._poi_logger is not None:
            self._poi_logger.finish_run(status, metadata={'batch': self._batch})
        result = dict(self._batch)
        self._active = False
        self._active_tag = None
        self.sigVerificationFinished.emit(result)
