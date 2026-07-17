"""Tests for POI verification logging and drift analysis helpers."""

import csv
import json
import os

import numpy as np

from logic.poi_verification_logger import (
    POIVerificationLogger,
    attempt_rows,
    candidate_summary_rows,
    load_manifest,
    summarize_manifest,
)
from tools.analyze_poi_verification_log import analyze


def _candidate(candidate_id='POI-001', score=0.83):
    return {
        'candidate_id': candidate_id,
        'region_id': 'R-001',
        'x': 1.0e-6,
        'y': 2.0e-6,
        'z_estimate': 3.0e-6,
        'pixel_row': 11,
        'pixel_col': 17,
        'classification': 'strong_candidate',
        'rank': 1,
        'overall_score': score,
        'snr': 9.5,
        'contrast': 1.7,
        'fit_quality': 0.91,
    }


def test_logger_records_candidate_attempt_decision_and_raw_archive(tmp_path):
    logger = POIVerificationLogger(
        str(tmp_path),
        run_id='poiverify_test',
        run_context={'scan_id': 'scan-001'},
        policy_snapshot={'stage1_max_attempts': 4, 'stage2_max_attempts': 5},
    )
    candidate = logger.start_candidate(_candidate())
    logger.log_attempt(
        candidate['candidate_id'],
        stage='stage1',
        attempt_number=1,
        seed_position_m=[1.0e-6, 2.0e-6, 3.0e-6],
        optimizer_return_position_m=[1.05e-6, 2.01e-6, 3.0e-6],
        gaussian_center_xy_m=[1.04e-6, 2.02e-6],
        poi_center_xy_m=[1.05e-6, 2.01e-6],
        next_seed_position_m=[1.04e-6, 2.02e-6, 3.0e-6],
        r_squared_xy=0.72,
        sigma_xy_m=[0.09e-6, 0.11e-6],
        gate_failures=[],
        raw_arrays={'xy_counts': np.ones((5, 5))},
        metadata={'next_seed_source': 'gaussian_center'},
    )
    logger.log_attempt(
        candidate['candidate_id'],
        stage='final_state',
        attempt_number=1,
        seed_position_m=[1.04e-6, 2.02e-6, 3.0e-6],
        optimizer_return_position_m=[1.045e-6, 2.018e-6, 3.0e-6],
        gaussian_center_xy_m=[1.046e-6, 2.019e-6],
        poi_center_xy_m=[1.045e-6, 2.018e-6],
        r_squared_xy=0.81,
        sigma_xy_m=[0.10e-6, 0.12e-6],
        gate_failures=[],
    )
    logger.finalize_candidate(
        candidate['candidate_id'],
        final_status='accepted',
        accepted_position_m=[1.046e-6, 2.019e-6, 3.0e-6],
        poi_name='NV_R-001_POI-001',
        registration_status='registered',
    )
    summary = logger.finish_run()

    manifest = load_manifest(str(tmp_path / 'poiverify_test'))
    state = manifest['candidates']['POI-001']
    assert manifest['terminal'] is True
    assert state['final_decision']['final_status'] == 'accepted'
    assert state['attempts'][0]['raw_archive'] == 'attempt_POI-001_stage1_a01.npz'
    raw = np.load(tmp_path / 'poiverify_test' / state['attempts'][0]['raw_archive'])
    assert raw['xy_counts'].shape == (5, 5)
    assert summary['status_counts']['accepted'] == 1
    assert summary['best_r_squared_xy'] == 0.81

    with open(tmp_path / 'poiverify_test' / 'events.jsonl', encoding='utf-8') as stream:
        events = [json.loads(line) for line in stream]
    assert [event['event_type'] for event in events] == [
        'run_started',
        'candidate_started',
        'attempt_logged',
        'seed_updated',
        'attempt_logged',
        'candidate_finalized',
        'run_finished',
    ]


def test_analysis_rows_and_cli_exports_show_drift_metrics(tmp_path):
    logger = POIVerificationLogger(str(tmp_path), run_id='poiverify_analysis')
    accepted = logger.start_candidate(_candidate('POI-A', score=0.9), index=0)
    logger.log_attempt(
        accepted['candidate_id'],
        'stage1',
        1,
        seed_position_m=[1.0e-6, 2.0e-6, 0.0],
        gaussian_center_xy_m=[1.10e-6, 2.0e-6],
        poi_center_xy_m=[1.08e-6, 2.0e-6],
        r_squared_xy=0.65,
        sigma_xy_m=[0.08e-6, 0.09e-6],
    )
    logger.finalize_candidate(
        accepted['candidate_id'], 'accepted',
        accepted_position_m=[1.10e-6, 2.0e-6, 0.0])

    rejected = logger.start_candidate(_candidate('POI-B', score=0.3), index=1)
    logger.log_attempt(
        rejected['candidate_id'],
        'stage1',
        1,
        seed_position_m=[1.0e-6, 2.0e-6, 0.0],
        gaussian_center_xy_m=[1.7e-6, 2.0e-6],
        r_squared_xy=0.2,
        sigma_xy_m=[0.5e-6, 0.6e-6],
        gate_failures=['r2_low', 'sigma_out_of_range'],
    )
    logger.finalize_candidate(
        rejected['candidate_id'], 'rejected',
        rejection_reason='stage1_budget_exhausted')
    logger.finish_run()

    manifest = load_manifest(str(tmp_path / 'poiverify_analysis'))
    rows = candidate_summary_rows(manifest)
    attempts = attempt_rows(manifest)
    summary = summarize_manifest(manifest)

    assert len(rows) == 2
    accepted_row = [row for row in rows if row['candidate_id'] == 'POI-A'][0]
    assert np.isclose(accepted_row['initial_to_final_shift_nm'], 100.0)
    rejected_row = [row for row in rows if row['candidate_id'] == 'POI-B'][0]
    assert 'r2_low' in rejected_row['gate_failure_counts']
    assert len(attempts) == 2
    assert summary['status_counts'] == {'accepted': 1, 'rejected': 1}

    output_dir = tmp_path / 'exports'
    analyze(str(tmp_path / 'poiverify_analysis'), output_directory=str(output_dir))
    summary_csv = output_dir / 'poi_verification_summary.csv'
    attempts_csv = output_dir / 'poi_verification_attempts.csv'
    assert summary_csv.exists()
    assert attempts_csv.exists()
    with open(summary_csv, newline='', encoding='utf-8') as stream:
        exported = list(csv.DictReader(stream))
    assert {row['candidate_id'] for row in exported} == {'POI-A', 'POI-B'}
