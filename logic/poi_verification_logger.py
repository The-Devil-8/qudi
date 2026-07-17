# -*- coding: utf-8 -*-
"""Durable POI verification logging for optimizer-driven NV candidate tuning.

The logger is deliberately independent of Qudi connectors.  Hardware modules
can choose the root directory through SaveLogic, while tests and offline
replays can use a temporary directory directly.
"""

from __future__ import division

import csv
import datetime
import json
import os
import re
import uuid

import numpy as np


SCHEMA_VERSION = 1


def utc_timestamp():
    """Return a sortable UTC timestamp with microsecond resolution."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def json_value(value):
    """Convert NumPy and tuple values into JSON-compatible structures."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value


def safe_slug(value, fallback='item'):
    """Return a filesystem-safe non-empty slug."""
    slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('-._')
    return slug or fallback


def normalize_position(position, dimensions=3):
    """Return a finite list of floats or ``None`` when unavailable."""
    if position is None:
        return None
    try:
        values = [float(position[index]) for index in range(dimensions)]
    except (TypeError, ValueError, IndexError):
        return None
    if not all(np.isfinite(values)):
        return None
    return values


def xy_delta_m(start, end):
    """Return signed XY shift plus radial distance in metres."""
    if start is None or end is None:
        return None
    try:
        delta_x = float(end[0]) - float(start[0])
        delta_y = float(end[1]) - float(start[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not np.isfinite(delta_x) or not np.isfinite(delta_y):
        return None
    return {
        'delta_x_m': delta_x,
        'delta_y_m': delta_y,
        'radial_m': float(np.hypot(delta_x, delta_y)),
    }


def candidate_to_log_record(candidate, index=0):
    """Normalize a POIExtractor candidate or mapping for audit storage."""
    if isinstance(candidate, dict):
        getter = candidate.get
    else:
        getter = lambda name, default=None: getattr(candidate, name, default)

    candidate_id = str(getter('candidate_id', '') or
                       'candidate-{0:03d}'.format(index + 1))
    seed = normalize_position((
        getter('x', None),
        getter('y', None),
        getter('z_estimate', 0.0),
    ))
    if seed is None:
        raise ValueError('candidate {0} must provide finite x/y/z_estimate'.format(
            candidate_id))

    record = {
        'candidate_id': candidate_id,
        'candidate_label': safe_slug(candidate_id, 'candidate-{0:03d}'.format(index + 1)),
        'region_id': str(getter('region_id', '') or ''),
        'initial_seed_position_m': seed,
        'pixel_row': getter('pixel_row', None),
        'pixel_col': getter('pixel_col', None),
        'classification': str(getter('classification', '') or ''),
        'rank': getter('rank', None),
        'overall_score': getter('overall_score', None),
        'intensity': getter('intensity', None),
        'snr': getter('snr', None),
        'contrast': getter('contrast', None),
        'fit_quality': getter('fit_quality', None),
        'detection_confidence': getter('detection_confidence', None),
        'isolation_score': getter('isolation_score', None),
        'zone_consistency': getter('zone_consistency', None),
        'edge_candidate': bool(getter('edge_candidate', False)),
        'extraction_method': str(getter('extraction_method', '') or ''),
    }
    return json_value(record)


class POIVerificationLogger:
    """Write chronological and summarized POI verification evidence."""

    def __init__(self, root_directory, run_id=None, run_context=None,
                 policy_snapshot=None):
        self.run_id = run_id or 'poiverify_{0}_{1}'.format(
            utc_timestamp(), uuid.uuid4().hex[:8])
        self.run_directory = os.path.abspath(os.path.join(root_directory, self.run_id))
        os.makedirs(self.run_directory, exist_ok=True)
        self.manifest_path = os.path.join(self.run_directory, 'manifest.json')
        self.events_path = os.path.join(self.run_directory, 'events.jsonl')
        self._event_index = 0
        self.manifest = {
            'schema_version': SCHEMA_VERSION,
            'run_id': self.run_id,
            'created_utc': utc_timestamp(),
            'run_context': json_value(run_context or {}),
            'policy_snapshot': json_value(policy_snapshot or {}),
            'terminal': False,
            'events_path': 'events.jsonl',
            'candidates': {},
            'summary': {},
        }
        self._write_manifest()
        self._append_event('run_started', {
            'run_context': self.manifest['run_context'],
            'policy_snapshot': self.manifest['policy_snapshot'],
        })

    def _write_manifest(self):
        temporary_path = self.manifest_path + '.tmp'
        with open(temporary_path, 'w', encoding='utf-8') as stream:
            json.dump(json_value(self.manifest), stream, indent=2, sort_keys=True)
        os.replace(temporary_path, self.manifest_path)

    def _append_event(self, event_type, payload):
        self._event_index += 1
        event = {
            'event_index': self._event_index,
            'event_utc': utc_timestamp(),
            'event_type': event_type,
        }
        event.update(json_value(payload or {}))
        with open(self.events_path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, sort_keys=True) + '\n')
        return event

    def _candidate_state(self, candidate_id):
        try:
            return self.manifest['candidates'][str(candidate_id)]
        except KeyError:
            raise KeyError('candidate {0} has not been started'.format(candidate_id))

    def start_candidate(self, candidate, index=0, extra=None):
        """Register a candidate and append a ``candidate_started`` event."""
        record = candidate_to_log_record(candidate, index)
        candidate_id = record['candidate_id']
        if candidate_id in self.manifest['candidates']:
            raise ValueError('candidate {0} is already present in this run'.format(
                candidate_id))
        state = {
            'candidate': record,
            'attempts': [],
            'seed_updates': [],
            'final_decision': None,
            'started_utc': utc_timestamp(),
        }
        if extra:
            state['extra'] = json_value(extra)
        self.manifest['candidates'][candidate_id] = state
        self._append_event('candidate_started', {
            'candidate_id': candidate_id,
            'candidate': record,
            'extra': extra or {},
        })
        self._write_manifest()
        return record

    def log_attempt(self, candidate_id, stage, attempt_number,
                    seed_position_m=None, optimizer_return_position_m=None,
                    gaussian_center_xy_m=None, poi_center_xy_m=None,
                    next_seed_position_m=None, outcome='completed',
                    r_squared_xy=None, sigma_xy_m=None, candidate_score=None,
                    gate_failures=None, elapsed_s=None, raw_arrays=None,
                    error=None, metadata=None):
        """Persist one optimizer attempt and optional raw arrays."""
        state = self._candidate_state(candidate_id)
        candidate = state['candidate']
        seed = normalize_position(seed_position_m)
        if seed is None:
            seed = candidate.get('initial_seed_position_m')
        optimizer_return = normalize_position(optimizer_return_position_m)
        gaussian_center = normalize_position(gaussian_center_xy_m, dimensions=2)
        poi_center = normalize_position(poi_center_xy_m, dimensions=2)
        next_seed = normalize_position(next_seed_position_m)

        stage_slug = safe_slug(stage, 'stage')
        raw_archive = self._write_raw_arrays(
            candidate['candidate_label'], stage_slug, attempt_number, raw_arrays)

        attempt = {
            'candidate_id': str(candidate_id),
            'candidate_label': candidate['candidate_label'],
            'stage': str(stage),
            'attempt_number': int(attempt_number),
            'outcome': str(outcome),
            'seed_position_m': seed,
            'optimizer_return_position_m': optimizer_return,
            'gaussian_center_xy_m': gaussian_center,
            'poi_center_xy_m': poi_center,
            'next_seed_position_m': next_seed,
            'r_squared_xy': None if r_squared_xy is None else float(r_squared_xy),
            'sigma_xy_m': normalize_position(sigma_xy_m, dimensions=2),
            'candidate_score': (
                candidate.get('overall_score') if candidate_score is None
                else float(candidate_score)),
            'gate_failures': list(gate_failures or []),
            'elapsed_s': None if elapsed_s is None else float(elapsed_s),
            'raw_archive': raw_archive,
            'error': error,
            'metadata': json_value(metadata or {}),
        }
        initial_seed = candidate.get('initial_seed_position_m')
        attempt['seed_to_gaussian_shift_xy_m'] = xy_delta_m(seed, gaussian_center)
        attempt['seed_to_optimizer_return_shift_xy_m'] = xy_delta_m(seed, optimizer_return)
        attempt['initial_seed_to_gaussian_shift_xy_m'] = xy_delta_m(
            initial_seed, gaussian_center)
        attempt['initial_seed_to_optimizer_return_shift_xy_m'] = xy_delta_m(
            initial_seed, optimizer_return)
        attempt['previous_seed_to_next_seed_shift_xy_m'] = xy_delta_m(seed, next_seed)
        attempt['poi_gaussian_distance_xy_m'] = xy_delta_m(poi_center, gaussian_center)

        state['attempts'].append(json_value(attempt))
        self._append_event('attempt_logged', attempt)
        if next_seed is not None:
            self.log_seed_update(
                candidate_id, stage, attempt_number, seed, next_seed,
                source=(metadata or {}).get('next_seed_source', 'unspecified'))
        else:
            self._write_manifest()
        return attempt

    def log_seed_update(self, candidate_id, stage, attempt_number,
                        previous_seed_position_m, next_seed_position_m,
                        source='unspecified'):
        """Record the seed chosen for the next optimizer attempt."""
        state = self._candidate_state(candidate_id)
        update = {
            'candidate_id': str(candidate_id),
            'stage': str(stage),
            'attempt_number': int(attempt_number),
            'previous_seed_position_m': normalize_position(previous_seed_position_m),
            'next_seed_position_m': normalize_position(next_seed_position_m),
            'source': str(source),
        }
        update['shift_xy_m'] = xy_delta_m(
            update['previous_seed_position_m'], update['next_seed_position_m'])
        state['seed_updates'].append(json_value(update))
        self._append_event('seed_updated', update)
        self._write_manifest()
        return update

    def finalize_candidate(self, candidate_id, final_status,
                           accepted_position_m=None, rejection_reason=None,
                           poi_name=None, registration_status=None,
                           metadata=None):
        """Store the final candidate disposition."""
        state = self._candidate_state(candidate_id)
        candidate = state['candidate']
        accepted = normalize_position(accepted_position_m)
        decision = {
            'candidate_id': str(candidate_id),
            'final_status': str(final_status),
            'accepted_position_m': accepted,
            'rejection_reason': rejection_reason,
            'poi_name': poi_name,
            'registration_status': registration_status,
            'metadata': json_value(metadata or {}),
        }
        decision['initial_seed_to_accepted_shift_xy_m'] = xy_delta_m(
            candidate.get('initial_seed_position_m'), accepted)
        state['final_decision'] = json_value(decision)
        self._append_event('candidate_finalized', decision)
        self._write_manifest()
        return decision

    def finish_run(self, status='completed', metadata=None):
        """Mark the run terminal and write final summary statistics."""
        self.manifest['terminal'] = True
        self.manifest['finished_utc'] = utc_timestamp()
        self.manifest['status'] = str(status)
        self.manifest['finish_metadata'] = json_value(metadata or {})
        self.manifest['summary'] = summarize_manifest(self.manifest)
        self._append_event('run_finished', {
            'status': status,
            'summary': self.manifest['summary'],
            'metadata': metadata or {},
        })
        self._write_manifest()
        return self.manifest['summary']

    def _write_raw_arrays(self, candidate_label, stage, attempt_number, raw_arrays):
        arrays = {}
        for name, value in (raw_arrays or {}).items():
            if value is not None:
                arrays[str(name)] = np.asarray(value)
        if not arrays:
            return None
        filename = 'attempt_{0}_{1}_a{2:02d}.npz'.format(
            safe_slug(candidate_label, 'candidate'), safe_slug(stage, 'stage'),
            int(attempt_number))
        np.savez_compressed(os.path.join(self.run_directory, filename), **arrays)
        return filename


def load_manifest(run_directory):
    """Load a POI verification manifest from a run directory."""
    manifest_path = os.path.join(run_directory, 'manifest.json')
    with open(manifest_path, 'r', encoding='utf-8') as stream:
        return json.load(stream)


def _radial_nm(delta):
    if not delta:
        return None
    value = delta.get('radial_m')
    return None if value is None else float(value) * 1e9


def _position_component(position, index, scale=1.0):
    if position is None:
        return None
    try:
        return float(position[index]) * scale
    except (TypeError, ValueError, IndexError):
        return None


def candidate_summary_rows(manifest):
    """Return one summary row per candidate."""
    rows = []
    for candidate_id, state in sorted(manifest.get('candidates', {}).items()):
        candidate = state.get('candidate', {})
        attempts = list(state.get('attempts', []))
        decision = state.get('final_decision') or {}
        r2_values = [
            attempt.get('r_squared_xy') for attempt in attempts
            if attempt.get('r_squared_xy') is not None
        ]
        sigma_values = [
            sigma for attempt in attempts
            for sigma in (attempt.get('sigma_xy_m') or [])
            if sigma is not None
        ]
        gate_failures = {}
        for attempt in attempts:
            for failure in attempt.get('gate_failures', []):
                gate_failures[failure] = gate_failures.get(failure, 0) + 1
        last_attempt = attempts[-1] if attempts else {}
        final_position = decision.get('accepted_position_m')
        if final_position is None:
            final_position = last_attempt.get('gaussian_center_xy_m')
        row = {
            'run_id': manifest.get('run_id'),
            'candidate_id': candidate_id,
            'region_id': candidate.get('region_id'),
            'classification': candidate.get('classification'),
            'rank': candidate.get('rank'),
            'overall_score': candidate.get('overall_score'),
            'snr': candidate.get('snr'),
            'contrast': candidate.get('contrast'),
            'fit_quality': candidate.get('fit_quality'),
            'attempt_count': len(attempts),
            'stage1_attempt_count': sum(
                1 for attempt in attempts if attempt.get('stage') == 'stage1'),
            'final_state_attempt_count': sum(
                1 for attempt in attempts
                if attempt.get('stage') in ('stage2', 'final_state')),
            'final_status': decision.get('final_status', 'unresolved'),
            'poi_name': decision.get('poi_name'),
            'rejection_reason': decision.get('rejection_reason'),
            'initial_x_um': _position_component(
                candidate.get('initial_seed_position_m'), 0, 1e6),
            'initial_y_um': _position_component(
                candidate.get('initial_seed_position_m'), 1, 1e6),
            'final_x_um': _position_component(final_position, 0, 1e6),
            'final_y_um': _position_component(final_position, 1, 1e6),
            'initial_to_final_shift_nm': _radial_nm(
                decision.get('initial_seed_to_accepted_shift_xy_m')) or _radial_nm(
                    last_attempt.get('initial_seed_to_gaussian_shift_xy_m')),
            'last_seed_to_gaussian_shift_nm': _radial_nm(
                last_attempt.get('seed_to_gaussian_shift_xy_m')),
            'last_poi_gaussian_distance_nm': _radial_nm(
                last_attempt.get('poi_gaussian_distance_xy_m')),
            'best_r_squared_xy': max(r2_values) if r2_values else None,
            'last_r_squared_xy': last_attempt.get('r_squared_xy'),
            'min_sigma_xy_nm': min(sigma_values) * 1e9 if sigma_values else None,
            'max_sigma_xy_nm': max(sigma_values) * 1e9 if sigma_values else None,
            'gate_failure_counts': json.dumps(gate_failures, sort_keys=True),
        }
        rows.append(row)
    return rows


def attempt_rows(manifest):
    """Return one summary row per optimizer attempt."""
    rows = []
    for candidate_id, state in sorted(manifest.get('candidates', {}).items()):
        candidate = state.get('candidate', {})
        for attempt in state.get('attempts', []):
            sigma = attempt.get('sigma_xy_m') or [None, None]
            row = {
                'run_id': manifest.get('run_id'),
                'candidate_id': candidate_id,
                'stage': attempt.get('stage'),
                'attempt_number': attempt.get('attempt_number'),
                'outcome': attempt.get('outcome'),
                'overall_score': candidate.get('overall_score'),
                'seed_x_um': _position_component(attempt.get('seed_position_m'), 0, 1e6),
                'seed_y_um': _position_component(attempt.get('seed_position_m'), 1, 1e6),
                'gaussian_x_um': _position_component(
                    attempt.get('gaussian_center_xy_m'), 0, 1e6),
                'gaussian_y_um': _position_component(
                    attempt.get('gaussian_center_xy_m'), 1, 1e6),
                'optimizer_return_x_um': _position_component(
                    attempt.get('optimizer_return_position_m'), 0, 1e6),
                'optimizer_return_y_um': _position_component(
                    attempt.get('optimizer_return_position_m'), 1, 1e6),
                'poi_x_um': _position_component(attempt.get('poi_center_xy_m'), 0, 1e6),
                'poi_y_um': _position_component(attempt.get('poi_center_xy_m'), 1, 1e6),
                'next_seed_x_um': _position_component(
                    attempt.get('next_seed_position_m'), 0, 1e6),
                'next_seed_y_um': _position_component(
                    attempt.get('next_seed_position_m'), 1, 1e6),
                'seed_to_gaussian_shift_nm': _radial_nm(
                    attempt.get('seed_to_gaussian_shift_xy_m')),
                'previous_seed_to_next_seed_shift_nm': _radial_nm(
                    attempt.get('previous_seed_to_next_seed_shift_xy_m')),
                'poi_gaussian_distance_nm': _radial_nm(
                    attempt.get('poi_gaussian_distance_xy_m')),
                'r_squared_xy': attempt.get('r_squared_xy'),
                'sigma_x_nm': None if sigma[0] is None else float(sigma[0]) * 1e9,
                'sigma_y_nm': None if sigma[1] is None else float(sigma[1]) * 1e9,
                'gate_failures': ';'.join(attempt.get('gate_failures', [])),
                'raw_archive': attempt.get('raw_archive'),
                'elapsed_s': attempt.get('elapsed_s'),
                'error': attempt.get('error'),
            }
            rows.append(row)
    return rows


def summarize_manifest(manifest):
    """Return compact aggregate counts and drift statistics for a manifest."""
    candidate_rows = candidate_summary_rows(manifest)
    attempt_data = attempt_rows(manifest)
    status_counts = {}
    for row in candidate_rows:
        status = row.get('final_status') or 'unresolved'
        status_counts[status] = status_counts.get(status, 0) + 1
    shifts = [
        row.get('initial_to_final_shift_nm') for row in candidate_rows
        if row.get('initial_to_final_shift_nm') is not None
    ]
    final_distances = [
        row.get('last_poi_gaussian_distance_nm') for row in candidate_rows
        if row.get('last_poi_gaussian_distance_nm') is not None
    ]
    r2_values = [
        row.get('r_squared_xy') for row in attempt_data
        if row.get('r_squared_xy') is not None
    ]
    return {
        'candidate_count': len(candidate_rows),
        'attempt_count': len(attempt_data),
        'status_counts': status_counts,
        'median_initial_to_final_shift_nm': (
            float(np.median(shifts)) if shifts else None),
        'max_initial_to_final_shift_nm': max(shifts) if shifts else None,
        'median_final_poi_gaussian_distance_nm': (
            float(np.median(final_distances)) if final_distances else None),
        'best_r_squared_xy': max(r2_values) if r2_values else None,
        'median_r_squared_xy': float(np.median(r2_values)) if r2_values else None,
    }


def write_csv(path, rows):
    """Write dictionaries to CSV, preserving first-seen field order."""
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['empty']
        rows = [{'empty': ''}]
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
