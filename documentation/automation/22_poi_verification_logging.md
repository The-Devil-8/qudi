# 22 - POI Verification Logging and Drift Analysis

## Purpose

`POIVerificationLogger` is the audit layer for automated NV candidate
verification.  It records what happened to every POI candidate before, during,
and after optimizer attempts so the lab can tune thresholds against real
hardware behaviour instead of guessing from final POI positions.

The logger is intentionally separate from `NVCandidateVerifier`.  The verifier
owns hardware state and decisions; the logger owns durable evidence.  This
separation lets calibration scripts, offline replays, and future verifier
versions all write the same schema.

## What Must Be Logged

Each run has a stable `run_id` and records:

- run context: scan ID, ROI/region, operator notes, optimizer settings, policy
  thresholds, and software version metadata when available;
- each candidate's original POIExtractor data: ID, region, pixel location,
  physical seed, classification, rank, score, SNR, contrast, and fit-quality
  score;
- each optimizer attempt: stage, attempt number, seed, optimizer return,
  Gaussian centre, POI/scanner centre, next seed, R2, sigma/standard
  deviation, edge/support flags, gate failures, raw archive path, elapsed
  time, and error state;
- each seed update: previous seed, chosen next seed, and the source used
  (`gaussian_center`, `optimizer_return`, `poi_center`, or `unchanged`);
- final candidate decision: accepted, rejected, unresolved, skipped, or
  registration failed, with the accepted position or rejection reason;
- run summary: counts by final status and aggregate drift/change statistics.

All distances are stored in metres.  Analysis tools may display micrometres or
nanometres, but the persisted schema stays SI-unit clean.

## Files

```text
.../POIVerificationLogger/<run_id>/
    manifest.json
    events.jsonl
    attempt_<candidate-id>_<stage>_aNN.npz
```

`events.jsonl` is append-only chronological evidence.  `manifest.json` is a
crash-resilient summary that is atomically rewritten after each event.  Raw
arrays are optional and stored in compressed NPZ files when available.

## Position Semantics

- `initial_seed_position_m`: POIExtractor's original strong-candidate
  position.
- `seed_position_m`: the seed used for this optimizer attempt.
- `optimizer_return_position_m`: the coordinate returned by legacy
  `OptimizerLogic`.
- `gaussian_center_xy_m`: fitted centre from the raw XY optimizer image.
- `poi_center_xy_m`: current POI/scanner centre after the attempt.
- `next_seed_position_m`: seed chosen for the next attempt.
- `accepted_position_m`: final registered POI position.

The analysis script compares these values to estimate hardware drift,
optimizer walk, and final convergence:

- `seed_to_gaussian_shift_xy_nm`
- `previous_seed_to_next_seed_shift_xy_nm`
- `initial_seed_to_final_shift_xy_nm`
- `poi_to_gaussian_distance_xy_nm`
- `final_state_last_distance_xy_nm`

These are diagnostic quantities for tuning.  They are not proof that a spot is
an NV centre and must not replace ODMR/HBT validation.

## Analysis Script

`tools/analyze_poi_verification_log.py` reads one run directory or a parent
directory containing multiple runs.  It writes:

- a human-readable summary to stdout;
- `poi_verification_summary.csv`, one row per candidate;
- `poi_verification_attempts.csv`, one row per optimizer attempt.

The per-candidate table is the main tuning artifact.  It includes original
POI score, final status, final position, initial-to-final shift, best and last
R2, sigma range, final POI/Gaussian distance, number of attempts, and gate
failure counts.

## Integration Rule

`NVCandidateVerifier` must log before acting on a decision:

1. candidate start;
2. attempt start;
3. optimizer/raw-fit result;
4. gate evaluation and seed update;
5. final accept/reject/unresolved decision;
6. POIManager registration result.

If Qudi stops mid-run, the manifest should still show the last completed
event and all completed attempt archives.
