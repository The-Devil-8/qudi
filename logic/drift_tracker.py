"""
Lightweight drift tracking module for the NV automation pipeline.

This module provides tools to record position snapshots before and after
measurements to build a calibration dataset for drift compensation.
"""
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
import numpy as np


@dataclass
class DriftRecord:
    """
    Dataclass representing a single drift tracking record.

    Attributes
    ----------
    timestamp_utc : str
        UTC timestamp of the record in ISO 8601 format.
    event : str
        Event type, one of 'pre_measurement', 'post_measurement',
        'pre_rescan', 'post_verification'.
    position_m : list of float
        [x, y, z] position coordinates in meters.
    candidate_id : str
        Identifier for the NV candidate.
    region_id : str
        Identifier for the measurement region.
    elapsed_since_last_s : float
        Elapsed time since the last record in seconds.
    """
    timestamp_utc: str
    event: str
    position_m: List[float]
    candidate_id: str
    region_id: str
    elapsed_since_last_s: float


class DriftTracker:
    """
    Tracks and analyzes position drift during automation tasks.
    """

    def __init__(self):
        """
        Initialize an empty DriftTracker.
        """
        self.records: List[DriftRecord] = []
        self._last_time = time.monotonic()

    def record(self, event: str, position_m: List[float],
               candidate_id: str = '', region_id: str = '') -> None:
        """
        Record a position snapshot.

        Parameters
        ----------
        event : str
            The event type (e.g., 'pre_measurement', 'post_measurement').
        position_m : list of float
            [x, y, z] position coordinates in meters.
        candidate_id : str, optional
            Candidate identifier, by default ''.
        region_id : str, optional
            Region identifier, by default ''.
        """
        current_time = time.monotonic()
        elapsed = current_time - self._last_time
        self._last_time = current_time

        timestamp = datetime.now(timezone.utc).isoformat()
        
        record = DriftRecord(
            timestamp_utc=timestamp,
            event=event,
            position_m=list(position_m),
            candidate_id=candidate_id,
            region_id=region_id,
            elapsed_since_last_s=elapsed
        )
        self.records.append(record)

    def compute_drift(self, from_index: int, to_index: int) -> Dict[str, float]:
        """
        Compute drift between two specific records by their indices.

        Parameters
        ----------
        from_index : int
            Index of the starting record.
        to_index : int
            Index of the ending record.

        Returns
        -------
        dict
            Dictionary containing 'delta_x_m', 'delta_y_m', 'delta_z_m',
            'radial_xy_m', and 'elapsed_s'.
            
        Raises
        ------
        IndexError
            If indices are out of bounds.
        """
        r1 = self.records[from_index]
        r2 = self.records[to_index]

        p1 = np.array(r1.position_m)
        p2 = np.array(r2.position_m)
        delta = p2 - p1

        radial_xy = np.sqrt(delta[0]**2 + delta[1]**2)
        
        # Calculate elapsed time between these two records
        step = 1 if from_index <= to_index else -1
        start = min(from_index, to_index)
        end = max(from_index, to_index)
        
        elapsed = sum(r.elapsed_since_last_s for r in self.records[start+1:end+1])
        if step == -1:
            elapsed = -elapsed

        return {
            'delta_x_m': float(delta[0]),
            'delta_y_m': float(delta[1]),
            'delta_z_m': float(delta[2]),
            'radial_xy_m': float(radial_xy),
            'elapsed_s': float(elapsed)
        }

    def compute_measurement_drift(self, candidate_id: str) -> Optional[Dict[str, float]]:
        """
        Find pre/post measurement records for a candidate and compute drift.

        Parameters
        ----------
        candidate_id : str
            The candidate identifier to search for.

        Returns
        -------
        dict or None
            Drift dictionary if both 'pre_measurement' and 'post_measurement'
            are found in that order for the candidate, else None.
        """
        pre_idx = -1
        post_idx = -1
        
        for i, r in enumerate(self.records):
            if r.candidate_id == candidate_id:
                if r.event == 'pre_measurement':
                    pre_idx = i
                elif r.event == 'post_measurement':
                    post_idx = i
                    break

        if pre_idx != -1 and post_idx != -1 and pre_idx < post_idx:
            return self.compute_drift(pre_idx, post_idx)
            
        return None

    def get_records(self, event_filter: Optional[str] = None) -> List[DriftRecord]:
        """
        Get all records, optionally filtered by event type.

        Parameters
        ----------
        event_filter : str, optional
            Event type to filter by, by default None.

        Returns
        -------
        list of DriftRecord
            List of matching records.
        """
        if event_filter is None:
            return self.records.copy()
        return [r for r in self.records if r.event == event_filter]

    def summary(self) -> Dict[str, Dict[str, float]]:
        """
        Compute mean, max, and std of drift across all measurement pairs.

        Returns
        -------
        dict
            Nested dictionary with summary statistics for each axis.
        """
        drifts = []
        pre_records = {}
        for i, r in enumerate(self.records):
            if r.event == 'pre_measurement':
                pre_records[r.candidate_id] = i
            elif r.event == 'post_measurement' and r.candidate_id in pre_records:
                pre_idx = pre_records.pop(r.candidate_id)
                drifts.append(self.compute_drift(pre_idx, i))

        if not drifts:
            return {}

        axes = ['delta_x_m', 'delta_y_m', 'delta_z_m', 'radial_xy_m']
        results = {}
        for axis in axes:
            vals = [d[axis] for d in drifts]
            results[axis] = {
                'mean': float(np.mean(vals)),
                'max': float(np.max(np.abs(vals))),
                'std': float(np.std(vals))
            }
            
        return results

    def save_to_json(self, filepath: str) -> None:
        """
        Save all records to a JSON file.

        Parameters
        ----------
        filepath : str
            Path to the output JSON file.
        """
        data = [asdict(r) for r in self.records]
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)

    @classmethod
    def load_from_json(cls, filepath: str) -> 'DriftTracker':
        """
        Load records from a JSON file.

        Parameters
        ----------
        filepath : str
            Path to the input JSON file.

        Returns
        -------
        DriftTracker
            A new DriftTracker instance with the loaded records.
        """
        tracker = cls()
        with open(filepath, 'r') as f:
            data = json.load(f)
            
        tracker.records = [DriftRecord(**item) for item in data]
        return tracker

    def reset(self) -> None:
        """
        Clear all records and reset the timer.
        """
        self.records.clear()
        self._last_time = time.monotonic()
