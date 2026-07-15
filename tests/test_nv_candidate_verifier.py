"""Tests for the non-hardware pieces of NVCandidateVerifier."""

import json

import numpy as np

from logic.nv_candidate_verifier import (
    DiagnosticRetryPolicy,
    VerificationAuditStore,
    analyse_legacy_xy_scan,
    candidate_to_record,
)


def _legacy_image(center_x=0.08e-6, center_y=-0.04e-6):
    x_values = np.linspace(-0.3e-6, 0.3e-6, 15)
    y_values = np.linspace(-0.3e-6, 0.3e-6, 15)
    x_grid, y_grid = np.meshgrid(x_values, y_values)
    counts = 9000.0 + 60000.0 * np.exp(
        -0.5 * (((x_grid - center_x) / 0.07e-6) ** 2 +
                ((y_grid - center_y) / 0.08e-6) ** 2))
    image = np.zeros((len(y_values), len(x_values), 4))
    image[:, :, 0] = x_grid
    image[:, :, 1] = y_grid
    image[:, :, 3] = counts
    return image, x_values, y_values


def test_legacy_xy_reanalysis_is_bounded_to_actual_samples():
    image, x_values, y_values = _legacy_image()
    record = analyse_legacy_xy_scan(
        image, x_values, y_values, seed_position_m=(0.0, 0.0, 0.0))

    assert record['success']
    assert record['r_squared'] > 0.999
    x_min, x_max, y_min, y_max = record['sampled_bounds_m']
    center_x, center_y = record['position_m']
    assert x_min <= center_x <= x_max
    assert y_min <= center_y <= y_max
    offset = record['fitted_offset_from_seed_xy_m']
    assert offset['delta_x_m'] > 0
    assert offset['delta_y_m'] < 0
    assert np.isclose(offset['radial_m'], np.hypot(offset['delta_x_m'],
                                                    offset['delta_y_m']))


def test_retry_policy_requires_two_normal_attempts_and_never_accepts():
    policy = DiagnosticRetryPolicy(minimum_attempts=2, maximum_attempts=4)
    normal = {'outcome': 'completed', 'optimizer2_xy': {
        'success': True, 'is_edge_fit': False}}
    edge = {'outcome': 'completed', 'optimizer2_xy': {
        'success': True, 'is_edge_fit': True}}

    assert policy.next_action([normal]) == 'retry'
    assert policy.next_action([normal, normal]) == 'diagnostic_complete'
    assert policy.next_action([normal, edge]) == 'retry'
    assert policy.next_action([normal, edge, edge, edge]) == 'unresolved'


def test_audit_store_writes_manifest_and_raw_npz(tmp_path):
    store = VerificationAuditStore(str(tmp_path), 'nvverify_test', {'operator': 'test'})
    attempt = {
        'candidate_id': 'POI-123',
        'attempt_number': 1,
        'outcome': 'completed',
        'optimizer2_xy': {'success': True, 'is_edge_fit': False},
    }
    store.record_attempt('POI-123', 1, attempt, {
        'xy_refocus_image': np.ones((3, 4, 4)),
        'seed_position_m': [1.0, 2.0, 3.0],
    })
    store.finish({'status': 'completed'})

    with open(store.manifest_path, encoding='utf-8') as stream:
        manifest = json.load(stream)
    assert manifest['terminal'] is True
    assert manifest['attempts'][0]['raw_archive'] == 'attempt_POI-123_a01.npz'
    raw = np.load(tmp_path / 'nvverify_test' / manifest['attempts'][0]['raw_archive'])
    assert raw['xy_refocus_image'].shape == (3, 4, 4)


def test_candidate_record_uses_candidate_id_not_queue_index_for_identity():
    candidate = candidate_to_record({'candidate_id': 'POI-a1b2c3', 'x': 1e-6,
                                     'y': 2e-6, 'z_estimate': 3e-6}, 9)
    assert candidate['candidate_label'] == 'POI-a1b2c3'
    assert candidate['seed_position_m'] == [1e-06, 2e-06, 3e-06]
