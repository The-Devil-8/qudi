"""Tests for the non-hardware pieces of NVCandidateVerifier."""

import json

import numpy as np

from logic.nv_candidate_verifier import (
    DiagnosticRetryPolicy,
    VerificationAuditStore,
    analyse_legacy_xy_scan,
    analysis_gate_failures,
    candidate_to_record,
    is_worthy_analysis,
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


# --- Helpers for fluorescence gate tests ---

def _good_analysis(amplitude=100000.0, offset=20000.0):
    """Return a minimal passing analysis dict with configurable amplitude/offset."""
    return {
        'success': True,
        'is_edge_fit': False,
        'r_squared': 0.95,
        'sigma_m': [0.15e-6, 0.15e-6],
        'position_m': [0.0, 0.0],
        'sampled_bounds_m': [-0.3e-6, 0.3e-6, -0.3e-6, 0.3e-6],
        'amplitude': amplitude,
        'offset': offset,
    }


def test_fluorescence_gate_rejects_too_low():
    """Peak = 25 kc/s, min = 50 kc/s -> fluorescence_too_low."""
    analysis = _good_analysis(amplitude=20000.0, offset=5000.0)
    failures = analysis_gate_failures(
        analysis, min_fluorescence_cps=50e3, max_fluorescence_cps=8e6)
    assert 'fluorescence_too_low' in failures
    assert 'fluorescence_too_high' not in failures


def test_fluorescence_gate_rejects_too_high():
    """Peak = 11 Mc/s, max = 8 Mc/s -> fluorescence_too_high."""
    analysis = _good_analysis(amplitude=10e6, offset=1e6)
    failures = analysis_gate_failures(
        analysis, min_fluorescence_cps=50e3, max_fluorescence_cps=8e6)
    assert 'fluorescence_too_high' in failures
    assert 'fluorescence_too_low' not in failures


def test_fluorescence_gate_passes_normal():
    """Peak = 120 kc/s, within [50 kc/s, 8 Mc/s] -> no fluorescence failures."""
    analysis = _good_analysis(amplitude=100000.0, offset=20000.0)
    failures = analysis_gate_failures(
        analysis, min_fluorescence_cps=50e3, max_fluorescence_cps=8e6)
    assert 'fluorescence_too_low' not in failures
    assert 'fluorescence_too_high' not in failures


def test_fluorescence_gate_not_applied_when_none():
    """Default None parameters -> no fluorescence gating (backward compatible)."""
    analysis = _good_analysis(amplitude=1.0, offset=1.0)  # Very low, but no gate
    failures = analysis_gate_failures(analysis)
    assert 'fluorescence_too_low' not in failures
    assert 'fluorescence_too_high' not in failures


def test_is_worthy_analysis_with_fluorescence_gates():
    """is_worthy_analysis passes through fluorescence params correctly."""
    good = _good_analysis(amplitude=100000.0, offset=20000.0)
    assert is_worthy_analysis(good, min_fluorescence_cps=50e3,
                              max_fluorescence_cps=8e6)

    too_dim = _good_analysis(amplitude=10000.0, offset=5000.0)
    assert not is_worthy_analysis(too_dim, min_fluorescence_cps=50e3,
                                  max_fluorescence_cps=8e6)


def test_analysis_gate_details_all_passed():
    """All gates pass on a valid diffraction-limited spot."""
    from logic.nv_candidate_verifier import analysis_gate_details

    analysis = _good_analysis(amplitude=120000.0, offset=25000.0)
    result = analysis_gate_details(
        analysis, min_r_squared=0.6,
        sigma_range_m=(0.05e-6, 0.4e-6),
        min_fluorescence_cps=50e3,
        max_fluorescence_cps=8e6)

    assert result['passed'] is True
    assert len(result['gate_failures']) == 0
    assert all(d['passed'] for d in result['details'])


def test_analysis_gate_details_r2_low_contains_values_and_criteria():
    """When R² is low, details must contain the exact R² and passing criteria."""
    from logic.nv_candidate_verifier import analysis_gate_details

    analysis = _good_analysis()
    analysis['r_squared'] = 0.4210
    result = analysis_gate_details(analysis, min_r_squared=0.6)

    assert result['passed'] is False
    assert 'r2_low' in result['gate_failures']
    r2_entry = [d for d in result['details'] if d['gate_name'] == 'r_squared'][0]
    assert r2_entry['passed'] is False
    assert '0.4210' in r2_entry['measured_value']
    assert '0.6000' in r2_entry['passing_criteria']
    assert '0.4210' in r2_entry['reason']
    assert '0.6000' in r2_entry['reason']


def test_analysis_gate_details_sigma_out_of_range_contains_values_and_criteria():
    """When sigma is outside the allowed range, details must show measured nm and range."""
    from logic.nv_candidate_verifier import analysis_gate_details

    analysis = _good_analysis()
    analysis['sigma_m'] = [0.4852e-6, 0.5120e-6]  # 485.2 nm, 512.0 nm (> 400 nm)
    result = analysis_gate_details(
        analysis, sigma_range_m=(0.05e-6, 0.4e-6))

    assert result['passed'] is False
    assert 'sigma_out_of_range' in result['gate_failures']
    sig_entry = [d for d in result['details'] if d['gate_name'] == 'sigma'][0]
    assert sig_entry['passed'] is False
    assert '485.2' in sig_entry['measured_value']
    assert '512.0' in sig_entry['measured_value']
    assert '50.0' in sig_entry['passing_criteria']
    assert '400.0' in sig_entry['passing_criteria']
    assert 'too broad' in sig_entry['reason']


def test_analysis_gate_details_stage2_distance_check():
    """In stage2, POI-to-Gaussian distance > tolerance must fail with measured distance and tolerance."""
    from logic.nv_candidate_verifier import analysis_gate_details

    analysis = _good_analysis()
    result = analysis_gate_details(
        analysis, stage='final_state',
        poi_gaussian_distance_m=78.4e-9,  # 78.4 nm
        poi_gaussian_center_tolerance_m=50e-9)  # 50.0 nm max

    assert result['passed'] is False
    assert 'poi_gaussian_distance_large' in result['gate_failures']
    dist_entry = [d for d in result['details'] if d['gate_name'] == 'poi_gaussian_distance'][0]
    assert dist_entry['passed'] is False
    assert '78.4' in dist_entry['measured_value']
    assert '50.0' in dist_entry['passing_criteria']
    assert '78.4' in dist_entry['reason']
    assert '50.0' in dist_entry['reason']


def test_rejection_banner_formats_failed_gates_values_and_history():
    """Rejection banner must contain candidate ID, failed gates, values, criteria, and reasons."""
    from logic.nv_candidate_verifier import (
        analysis_gate_details,
        format_candidate_rejection_banner,
    )

    analysis = _good_analysis(amplitude=20000.0, offset=5000.0)  # 25 kc/s (< 50 kc/s)
    analysis['r_squared'] = 0.35
    gate_details = analysis_gate_details(
        analysis, min_r_squared=0.6,
        min_fluorescence_cps=50e3, max_fluorescence_cps=8e6)

    candidate = {
        'candidate_id': 'POI-test-01',
        'candidate_label': 'POI-test-01',
        'region_id': 'R001',
        'overall_score': 0.85,
    }
    history = [
        {
            'attempt_number': 1,
            'stage': 'stage1',
            'outcome': 'completed',
            'gate_failures': ['r2_low'],
            'optimizer2_xy': {'r_squared': 0.35, 'sigma_m': [0.15e-6, 0.15e-6]},
        }
    ]
    banner = format_candidate_rejection_banner(
        candidate, 'stage1', attempts_used=4, max_attempts=4,
        final_gate_details=gate_details, attempt_history=history)

    assert 'POI CANDIDATE REJECTED' in banner
    assert 'POI-test-01' in banner
    assert 'R2 Goodness of Fit' in banner
    assert '0.3500' in banner
    assert '0.6000' in banner
    assert 'Peak Fluorescence Count Rate' in banner
    assert '25.0 kc/s' in banner
    assert '50.0 kc/s' in banner
    assert 'ATTEMPT PROGRESSION HISTORY' in banner

