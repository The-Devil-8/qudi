# 00 — Current Implementation & Intuition State

> **Living Document**  
> This document tracks the *current* state of the automation pipeline, why decisions were made, and the physical intuition behind algorithms.  
> **AGENTS MUST READ THIS** before writing code or plans.

## 1. Pipeline Status

1. **Wide-Field Segmentation** (`logic/roi_segmentation_logic.py`) — **DONE**
   - Uses Otsu thresholding to separate cell masks from substrate.
   - Detects bright macro-clusters to avoid wasting time on dense areas.
2. **Region Queueing** (`logic/scan_region_queue.py`) — **DONE**
   - Ranks identified bounding boxes for priority processing.
3. **Close-Scan Cell Processing** (`logic/cell_region_processor.py`) — **DONE**
   - Analyzes high-res 30-60µm FOV close-scans.
   - Extracts the "Processable Zone": cell interior minus the nucleus and minus bright macro-clusters.
   - Computes zone statistics (median, std, max intensity) used for adaptive thresholding later.
4. **POI Extraction** (`logic/poi_extractor.py`) — **DONE**
   - Runs Confocal Image Processing (CIP) constrained to the Processable Zone.
   - Extracts individual diffraction-limited NV candidates, scores them, and filters them adaptively.
5. **Candidate Verification** (`logic/nv_candidate_verifier.py`) — **DONE (HYBRID MODE)**
   - Wraps unmodified legacy `OptimizerLogic` for two-to-four correlated refocus attempts.
   - Archives raw legacy optimizer scans and independently re-fits XY data through bounded `Optimizer2D`.
   - Now supports three operating modes: `diagnostic` (data collection only), `hybrid` (gates + registration + full audit), `production` (gates + registration, reduced logging).
   - In `hybrid` mode, accepted candidates are registered to `PoiManagerLogic` and a `sigCandidateAccepted` signal is emitted for downstream consumers (PulsedMeasurementExecutor).
   - Backward-compatible: configs using `diagnostic_only: True` are automatically mapped to `operating_mode: 'diagnostic'`.
6. **Pulsed Measurement Execution** (`logic/pulsed_measurement_executor.py`) — **DONE**
   - Qt signal-driven state machine that automates T1/ODMR experiment sequences through `PulsedMasterLogic`.
   - Sequence: pulser off → stop prev measurement → sample+load measurement ensemble → run → wait for completion → save data → pulser off → sample+load laser pulse → pulser on.
   - 15-minute configurable safety timeout. All transitions use signal correlation (no blocking waits).
   - **State machine uses set-state-before-call pattern**: For states that invoke an async PML method, the wait state is set *before* the async call is made.  This prevents a race condition where the callback arrives before a deferred state transition.  Fire-and-forget operations (pulser off, stop measurement) use 100 ms settle timers instead.
7. **Full Experiment Loop** (`logic/multi_scale_auto_nv_finder_logic.py`) — **DONE**
   - Complete orchestration: macro scan → ROI segmentation → for each cell: micro scan → cell processing → POI extraction → POI filtering (non-repetition radius) → **serial one-at-a-time** verification (hybrid mode) → pulsed measurement → drift snapshot → repeat until NVs/cell met (with re-scanning) → next cell.
   - **Strictly serial one-at-a-time candidate flow**: candidates are sent to `NVCandidateVerifier` one at a time.  Each candidate's full lifecycle (optical verification → pulsed measurement → completion) must finish before the next candidate is dispatched.  See §2.6 for the hardware constraint that requires this.
   - Signal connections to `NVCandidateVerifier` and `PulsedMeasurementExecutor` are established once in `on_activate()` and torn down in `on_deactivate()`.  Per-batch connect/disconnect is not used (it caused duplicate handlers and leaked connections on error paths).
   - Tracks per-cell NV targets (default 2-3), total cell targets, drift records, and measurement results.
   - POI non-repetition filtering removes candidates within 1 µm of previously measured NVs.
8. **Z-Scan Surface Finding** (`logic/z_surface_finder.py`) — **STUB**
   - Documented interface for next iteration: full Z scan → find bright surface layer (top 2% = "cream") → compute target depth Z = Z_SL - Z_depth.
   - `compute_target_depth()` is functional; `find_surface()` raises `NotImplementedError`.

## 2. Core Intuition & Physics Considerations

### 2.1 The Need for Adaptive Zone Thresholding
Standard full-image CIP algorithms fail in biological confocal scans because the image has three distinct regions: dark substrate, bright uniform cell body, and extremely bright NV/diamond clusters. 
- Using full-image noise estimations causes thresholds to be either too high (missing real NVs because clusters dominated the noise profile) or too low (detecting false positives in the substrate). 
- **Solution**: We strictly extract noise and median intensity *only* from the Processable Zone. This creates a flat background baseline specific to the biological material.

### 2.2 Boundary Artifacts & Erosion
When subtracting background via a large median filter (e.g., kernel=15), the sharp cell-to-substrate edge creates a transition zone. Subtracting this background leaves artificial high-intensity rims at the cell boundary.
- **Solution**: The `processable_mask` must be eroded deeply enough (e.g., 8-10 pixels) so that we do not run CIP detection on these boundary artifacts.

### 2.3 Physical Reality of Diffraction-Limited Spots (NV Centers)
Real NV centers are point spread functions (PSF) approximated by 2D Gaussians. At our optical setup and pixel size (e.g., 0.33 µm/px), the spot has a FWHM of ~4.5px, which means a standard deviation (sigma) of ~2.0 pixels.
- **Contrast**: At a radius of 2 pixels from the center, the Gaussian flank is still at ~40-60% of the peak intensity. Because contrast is measured as `peak / border_intensity`, a true NV will natively have a contrast ratio of roughly **1.5 to 2.5**. It will *never* be 10.0 (which would imply a single-pixel hot noise spike).
- **Fit Quality**: Simple peak-to-background ratio fit qualities within a 5x5 window evaluate to surprisingly low numbers (e.g., 0.15 - 0.3) for broad NVs because the patch edges are still bright.
- **Solution**: In `POIExtractor._score_candidates`, the scoring curves for `contrast_score` and `fit_score` are deliberately scaled so that physical values (e.g., contrast=1.5, fit=0.2) map to near-perfect scores (1.0).

### 2.4 Zone Consistency Scoring
Because the Processable Zone has its macro-clusters explicitly removed by the `CellRegionProcessor`, any extremely bright spot left in the zone is highly likely to be a superb single NV.
- **Intuition**: An NV center can be 200,000 counts/sec, while the cell background has a median of 30,000 and a standard deviation of 2,000. This yields a z-score of `(200k - 30k) / 2k = 85`. 
- **Solution**: `POIExtractor._compute_zone_consistency` compares the **raw** candidate intensity to the **raw** zone stats and is very lenient. It gives top scores to candidates up to z-score=30, and high scores up to z-score=100.

### 2.5 Scikit-Image Otsu Fallbacks for Extreme Brightness Outliers
When `scikit-image` is available, the pipeline uses `threshold_otsu` for cell segmentation and candidate narrowing. However, NV centers can reach 13-20 million counts/sec, creating extreme outliers that heavily skew the Otsu algorithm (which is designed for standard images).
- **Cell Segmenter Impact**: Otsu gets skewed by extreme NVs, setting a cell-body threshold in the millions and dropping the actual faint cell body (resulting in 0 px processable zones).
  - **Solution**: In `CellRegionProcessor`, if the calculated Otsu threshold exceeds the 90th percentile of image intensities, it is rejected as skewed and falls back to a safe `np.percentile(nonzero, 60)`.
- **POI Extractor Impact**: When narrowing candidates, running Otsu on a tiny array of 5-10 similarly-scored candidates can calculate a threshold *higher than the maximum score in the array*, wrongly classifying 100% of candidates as "marginal".
  - **Solution**: In `POIExtractor`, if `score_threshold > np.max(scores)`, it falls back to `np.median(scores)` to keep the top 50% of candidates safely.

### 2.6 Hardware Exclusivity: Verification vs. Pulsed Measurement
The `NVCandidateVerifier` uses the **optimizer/confocal scanning hardware** to perform optical refocus scans on candidates.  The `PulsedMeasurementExecutor` uses the **pulse generator and fast counter** to run T1/ODMR experiments.  These two subsystems share electrical and timing resources that prevent concurrent operation:

- The fast counter (used for pulsed measurement) drives acquisition timing.  Running a confocal (optimizer) scan while the fast counter is active will produce corrupted data or hardware contention errors.
- The pulse generator state must be well-defined for both systems — the optimizer expects laser-only CW operation, while pulsed experiments require specific pulse sequences.

**Critical Design Rule**: The orchestrator (`MultiScaleAutoNVFinderLogic`) must enforce **strictly serial** processing:

```
Verify Candidate N (optimizer scans) → Accept/Reject
  If Accepted → Run Pulsed Measurement (T1/ODMR) → Wait for completion → Verify Candidate N+1
  If Rejected → Verify Candidate N+1 immediately
```

Candidates are dispatched to the verifier **one at a time** as single-element batches.  The orchestrator's `_verify_next_candidate()` method enforces that no new candidate is sent until the current candidate's entire lifecycle (optical verification + pulsed measurement) is complete.  This constraint is documented in the method's docstring and in the pipeline comment block.

This design also eliminates three classes of synchronization bugs:
1. **Race between deferred transitions and async callbacks** in `PulsedMeasurementExecutor` — fixed by setting wait states *before* async calls.
2. **Candidate acceptance flooding** — eliminated because only one candidate is verified at a time, so at most one `sigCandidateAccepted` can fire before the orchestrator explicitly dispatches the next.
3. **Signal connection leaks** — eliminated by connecting verifier/executor signals once in `on_activate()` instead of per-batch.

## 3. Immediate Next Steps

1. Run the full pipeline in `hybrid` mode on a known sample with `enable_pulsed_measurement: True` to validate the serial verify→measure flow end-to-end.  Confirm no "Cannot start measurement while one is already active" errors.
2. Analyze the `DriftTracker` snapshots from step 1 to quantify typical hardware drift during T1/ODMR measurements (expected 10-30 minutes per NV). This calibration data feeds the future drift compensation module.
3. Review the `POIVerificationLogger` audit logs alongside pulsed measurement results to correlate optical fit quality with actual NV behavior under T1/ODMR.
4. Implement `ZSurfaceFinder.find_surface()` using the calibration Z-scan profiles collected in step 1 — identify the bright layer peak (top 2%) and validate depth targeting.
5. Add ODMR quality gates as a Stage 3 in `NVCandidateVerifier` based on measurement results (dip contrast, linewidth).
6. Build drift compensation module using the calibration dataset: apply start/end drift subtraction to improve next-NV candidate verification accuracy.
