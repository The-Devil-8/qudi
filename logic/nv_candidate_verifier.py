# -*- coding: utf-8 -*-
"""Diagnostic, repeated optical verification for extracted NV candidates.

This module deliberately wraps the existing :class:`OptimizerLogic` without
changing it.  The legacy optimizer only publishes a final coordinate and may
fall back to its seed when a fit fails, so this module archives its raw XY scan
and re-analyses that scan with :class:`logic.optimizer2.Optimizer2D`.

Version one is diagnostic-only: it never creates or rejects POIs and it has no
ODMR dependency.  Its purpose is to collect properly correlated calibration
data before optical acceptance gates are enabled.
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

    diagnostic_only = StatusVar('diagnostic_only', True)
    minimum_attempts = StatusVar('minimum_attempts', 2)
    maximum_attempts = StatusVar('maximum_attempts', 4)
    attempt_timeout_s = StatusVar('attempt_timeout_s', 90.0)
    timeout_cleanup_s = StatusVar('timeout_cleanup_s', 10.0)
    audit_subdirectory = StatusVar('audit_subdirectory', 'NVCandidateVerifier')

    sigVerificationProgress = QtCore.Signal(str, str, int, int)
    sigCandidateVerificationUpdated = QtCore.Signal(object)
    sigVerificationFinished = QtCore.Signal(object)
    sigVerificationError = QtCore.Signal(str, str)

    def on_activate(self):
        """Connect once to the optimizer and initialise the watchdog."""
        self._optimizer = self.optimizerlogic()
        self._active = False
        self._stop_requested = False
        self._current_index = -1
        self._attempt_started_at = None
        self._attempt_started_utc = None
        self._active_tag = None
        self._batch = None
        self._audit = None
        self._timeout_requested = False
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
        """Start a non-blocking diagnostic batch and return a stable run ID.

        No POI is registered and no candidate receives an acceptance/rejection
        verdict, regardless of the captured result.
        """
        if self._active:
            raise RuntimeError('an NVCandidateVerifier batch is already active')
        if not bool(self.diagnostic_only):
            raise RuntimeError('only diagnostic_only=True is implemented; automated gates are disabled')

        records = [candidate_to_record(candidate, index)
                   for index, candidate in enumerate(candidates)]
        if not records:
            raise ValueError('verify_batch requires at least one candidate')
        labels = [record['candidate_label'] for record in records]
        if len(set(labels)) != len(labels):
            raise ValueError('candidate_id values must be unique within one verification batch')
        policy = DiagnosticRetryPolicy(self.minimum_attempts, self.maximum_attempts)
        run_id = 'nvverify_{0}_{1}'.format(_utc_timestamp(), uuid.uuid4().hex[:8])
        metadata = {
            'run_context': run_context or {},
            'policy': {
                'minimum_attempts': policy.minimum_attempts,
                'maximum_attempts': policy.maximum_attempts,
                'attempt_timeout_s': float(self.attempt_timeout_s),
                'timeout_cleanup_s': float(self.timeout_cleanup_s),
            },
            'legacy_optimizer_settings': self._optimizer_settings_snapshot(),
        }
        self._audit = VerificationAuditStore(self._audit_root(), run_id, metadata)
        self._batch = {
            'run_id': run_id,
            'audit_directory': self._audit.run_directory,
            'started_utc': _utc_timestamp(),
            'diagnostic_only': True,
            'run_context': run_context or {},
            'candidates': records,
            'status': 'running',
            'policy': metadata['policy'],
        }
        self._policy = policy
        self._active = True
        self._stop_requested = False
        self._current_index = 0
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
        attempt_number = len(candidate['attempts']) + 1
        if self._optimizer.module_state() != 'idle':
            self._record_non_scan_attempt(candidate, attempt_number, 'hardware_busy',
                                          'legacy optimizer module is not idle')
            candidate['status'] = 'unresolved'
            self._advance_candidate()
            return

        self._active_tag = '{0}:{1}:a{2:02d}'.format(
            self._batch['run_id'], candidate['candidate_label'], attempt_number)
        self._attempt_started_at = time.monotonic()
        self._attempt_started_utc = _utc_timestamp()
        self._timeout_requested = False
        candidate['status'] = 'scanning'
        self.sigVerificationProgress.emit(self._batch['run_id'], candidate['candidate_id'],
                                          attempt_number, self._policy.maximum_attempts)
        self.sigCandidateVerificationUpdated.emit(dict(candidate))
        self._watchdog.start(max(1, int(float(self.attempt_timeout_s) * 1000)))
        try:
            self._optimizer.start_refocus(
                initial_pos=list(candidate['seed_position_m']), caller_tag=self._active_tag)
        except Exception as error:
            self._watchdog.stop()
            self._active_tag = None
            self._record_non_scan_attempt(candidate, attempt_number, 'hardware_error', str(error))
            candidate['status'] = 'unresolved'
            self._advance_candidate()

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
        attempt_number = len(candidate['attempts']) + 1
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
            'seed_position_m': candidate['seed_position_m'],
            'error': 'no matching sigRefocusFinished after stop_refocus cleanup interval',
            'optimizer2_xy': {
                'success': False,
                'is_edge_fit': False,
                'error': 'no raw scan received before timeout cleanup',
            },
        }
        self._record_attempt(candidate, attempt_number, attempt, None)
        candidate['status'] = 'unresolved'
        self.sigVerificationError.emit(self._batch['run_id'], attempt['error'])
        self._active_tag = None
        self._finish_batch('stopped' if self._stop_requested else 'timed_out')

    def _on_refocus_finished(self, caller_tag, optimal_position):
        if not self._active or caller_tag != self._active_tag:
            return
        self._watchdog.stop()
        self._timeout_cleanup.stop()
        candidate = self._batch['candidates'][self._current_index]
        attempt_number = len(candidate['attempts']) + 1
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
            self._finish_batch('stopped')
            return
        action = self._policy.next_action(candidate['attempts'])
        candidate['status'] = action
        self.sigCandidateVerificationUpdated.emit(dict(candidate))
        if action == 'retry':
            QtCore.QTimer.singleShot(0, self._start_current_attempt)
        else:
            self._advance_candidate()

    def _capture_attempt(self, candidate, attempt_number, optimal_position, elapsed):
        xy_image = getattr(self._optimizer, 'xy_refocus_image', None)
        x_values = getattr(self._optimizer, '_X_values', None)
        y_values = getattr(self._optimizer, '_Y_values', None)
        opt_channel = getattr(self._optimizer, 'opt_channel', 0)
        analysis = analyse_legacy_xy_scan(xy_image, x_values, y_values,
                                          candidate['seed_position_m'], opt_channel)
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
            'seed_position_m': candidate['seed_position_m'],
            'legacy_return_position_m': optimal_position,
        }
        return {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'attempt_number': attempt_number,
            'caller_tag': self._active_tag,
            'started_utc': self._attempt_started_utc,
            'elapsed_s': elapsed,
            'outcome': 'completed',
            'seed_position_m': candidate['seed_position_m'],
            'legacy_return_position_m': _json_value(optimal_position),
            'legacy_return_offset_from_seed_xy_m': xy_offset_record(
                candidate['seed_position_m'], optimal_position),
            'legacy_sigma_m': _json_value(legacy_sigma),
            'legacy_xy_fit_evidence': bool(legacy_xy_fit_evidence),
            'legacy_xy_fit_note': ('positive legacy sigmas observed; raw bounded re-analysis remains authoritative'
                                   if legacy_xy_fit_evidence else
                                   'indeterminate: zero/absent legacy sigma can represent a fallback'),
            'optimizer2_xy': analysis,
        }, arrays

    def _record_non_scan_attempt(self, candidate, attempt_number, outcome, error):
        attempt = {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'attempt_number': attempt_number,
            'caller_tag': None,
            'started_utc': _utc_timestamp(),
            'elapsed_s': 0.0,
            'outcome': outcome,
            'seed_position_m': candidate['seed_position_m'],
            'error': error,
            'optimizer2_xy': {'success': False, 'is_edge_fit': False, 'error': error},
        }
        self._record_attempt(candidate, attempt_number, attempt, None)
        self.sigVerificationError.emit(self._batch['run_id'], str(error))

    def _record_attempt(self, candidate, attempt_number, attempt, arrays):
        self._audit.record_attempt(candidate['candidate_label'], attempt_number, attempt, arrays)
        candidate['attempts'].append(attempt)

    def _advance_candidate(self):
        self._current_index += 1
        QtCore.QTimer.singleShot(0, self._start_current_attempt)

    def _finish_batch(self, status):
        if not self._active:
            return
        self._watchdog.stop()
        self._timeout_cleanup.stop()
        if status in ('stopped', 'timed_out'):
            for candidate in self._batch['candidates'][self._current_index + 1:]:
                if candidate['status'] == 'queued':
                    candidate['status'] = 'skipped'
        self._batch['status'] = status
        self._batch['finished_utc'] = _utc_timestamp()
        self._audit.finish(self._batch)
        result = dict(self._batch)
        self._active = False
        self._active_tag = None
        self.sigVerificationFinished.emit(result)
