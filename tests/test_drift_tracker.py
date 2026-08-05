import pytest
import os
from unittest.mock import patch
from logic.drift_tracker import DriftRecord, DriftTracker

def test_drift_record_creation():
    record = DriftRecord(
        timestamp_utc="2023-01-01T12:00:00Z",
        event="pre_measurement",
        position_m=[1.0, 2.0, 3.0],
        candidate_id="cand_1",
        region_id="reg_1",
        elapsed_since_last_s=1.5
    )
    assert record.timestamp_utc == "2023-01-01T12:00:00Z"
    assert record.event == "pre_measurement"
    assert record.position_m == [1.0, 2.0, 3.0]
    assert record.candidate_id == "cand_1"
    assert record.region_id == "reg_1"
    assert record.elapsed_since_last_s == 1.5

def test_record_addition():
    tracker = DriftTracker()
    with patch("time.monotonic", side_effect=[100.0, 102.5, 106.0]):
        # The constructor calls time.monotonic() once, setting _last_time to 100.0
        # First record call: current_time = 102.5, elapsed = 2.5
        tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="cand_1")
        # Second record call: current_time = 106.0, elapsed = 3.5
        tracker.record("post_measurement", [1.0, 1.0, 1.0], candidate_id="cand_1")
    
    assert len(tracker.records) == 2
    
    rec1 = tracker.records[0]
    assert rec1.event == "pre_measurement"
    assert rec1.elapsed_since_last_s == 2.5
    assert rec1.timestamp_utc is not None
    
    rec2 = tracker.records[1]
    assert rec2.event == "post_measurement"
    assert rec2.elapsed_since_last_s == 3.5
    assert rec2.timestamp_utc is not None

def test_compute_drift():
    tracker = DriftTracker()
    tracker.record("pre_measurement", [1.0, 2.0, 3.0])
    tracker.record("post_measurement", [1.1, 1.9, 3.5])
    
    # We can override elapsed time manually for the test
    tracker.records[1].elapsed_since_last_s = 5.0
    
    drift = tracker.compute_drift(0, 1)
    
    assert drift['delta_x_m'] == pytest.approx(0.1)
    assert drift['delta_y_m'] == pytest.approx(-0.1)
    assert drift['delta_z_m'] == pytest.approx(0.5)
    assert drift['radial_xy_m'] == pytest.approx((0.1**2 + (-0.1)**2)**0.5)
    assert drift['elapsed_s'] == pytest.approx(5.0)

def test_compute_measurement_drift():
    tracker = DriftTracker()
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="cand_1")
    tracker.record("some_other_event", [0.0, 0.0, 0.0], candidate_id="cand_2")
    tracker.record("post_measurement", [1.0, 0.0, 0.0], candidate_id="cand_1")
    
    drift = tracker.compute_measurement_drift("cand_1")
    assert drift is not None
    assert drift['delta_x_m'] == pytest.approx(1.0)
    
    # No matching records
    assert tracker.compute_measurement_drift("cand_missing") is None
    
    # Only pre-measurement
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="cand_3")
    assert tracker.compute_measurement_drift("cand_3") is None

def test_get_records():
    tracker = DriftTracker()
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="c1")
    tracker.record("post_measurement", [1.0, 0.0, 0.0], candidate_id="c1")
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="c2")
    
    pre_recs = tracker.get_records("pre_measurement")
    assert len(pre_recs) == 2
    assert pre_recs[0].candidate_id == "c1"
    assert pre_recs[1].candidate_id == "c2"
    
    all_recs = tracker.get_records()
    assert len(all_recs) == 3

def test_summary():
    tracker = DriftTracker()
    # cand_1
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="c1")
    tracker.record("post_measurement", [1.0, 0.0, 0.0], candidate_id="c1") # delta_x = 1.0
    
    # cand_2
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="c2")
    tracker.record("post_measurement", [3.0, 0.0, 0.0], candidate_id="c2") # delta_x = 3.0
    
    summary = tracker.summary()
    assert 'delta_x_m' in summary
    assert summary['delta_x_m']['mean'] == pytest.approx(2.0)
    assert summary['delta_x_m']['max'] == pytest.approx(3.0)
    assert summary['delta_x_m']['std'] == pytest.approx(1.0)

def test_save_and_load_json(tmp_path):
    tracker = DriftTracker()
    tracker.record("pre_measurement", [1.1, 2.2, 3.3], candidate_id="c1", region_id="r1")
    tracker.record("post_measurement", [1.2, 2.3, 3.4], candidate_id="c1", region_id="r1")
    
    filepath = os.path.join(tmp_path, "drift.json")
    tracker.save_to_json(filepath)
    
    assert os.path.exists(filepath)
    
    new_tracker = DriftTracker.load_from_json(filepath)
    assert len(new_tracker.records) == 2
    assert new_tracker.records[0].candidate_id == "c1"
    assert new_tracker.records[0].position_m == [1.1, 2.2, 3.3]
    assert new_tracker.records[0].event == "pre_measurement"
    assert new_tracker.records[1].event == "post_measurement"

def test_reset():
    tracker = DriftTracker()
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="c1")
    assert len(tracker.records) == 1
    
    tracker.reset()
    assert len(tracker.records) == 0

def test_edge_cases():
    tracker = DriftTracker()
    # Empty tracker summary
    assert tracker.summary() == {}
    
    # Empty tracker save
    tracker.record("pre_measurement", [0.0, 0.0, 0.0], candidate_id="c1")
    assert tracker.compute_measurement_drift("c1") is None # missing post
    
    tracker.reset()
    tracker.record("post_measurement", [0.0, 0.0, 0.0], candidate_id="c1")
    assert tracker.compute_measurement_drift("c1") is None # missing pre
