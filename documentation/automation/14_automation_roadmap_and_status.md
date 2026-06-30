# 14 — Automation Roadmap, Status & Approach Decision

> **Document 14 of the Automation Series**  
> Executive summary: current status, approach decision, and pointers to detailed next-step plans (no deadlines).

**Detailed plans (read these for implementation):**
- [15 — Phased Implementation Plan](15_phased_implementation_plan.md) — module wiring, 7 phases, step-by-step tasks
- [16 — Testing Data Requirements](16_testing_data_requirements.md) — what data exists, what's missing, acquisition checklist
- [17 — Algorithm Optimization](17_algorithm_optimization.md) — cell boundary, ROI, cluster, CIP tuning

---

## Executive Summary

The goal is an **automated, iterative feedback loop** that:

1. Detects fluorescent cell clusters (NV center groups) in wide-field confocal scans
2. Dynamically adjusts the scanner field of view (zoom in / zoom out)
3. Finds individual NV centers at fine resolution inside each region
4. Optimizes positions and registers POIs

**Current state:** Single-scale NV detection (CIP pipeline) is **implemented and wired into Qudi**. Cell/ROI segmentation runs **offline on `.dat` files** but is **not connected** to live scanning. Coarse-to-fine zoom, validation (ODMR/HBT), and the legacy `AutomationLogic` task tree are **planned or stubbed only**.

**Recommended path:** **Hybrid Traditional CV + Coarse-to-Fine multi-scale loop**, reusing code already in this repository. Do **not** pursue deep learning now. Use DBSCAN only as an optional refinement for coarse-scale cluster counting—not as the primary architecture.

---

## Confocal Data (Attached `Confocal/` Folder)

All sample scans share the same geometry:

| Parameter | Value |
|-----------|-------|
| Scan range | ~200 µm × 200 µm (`X/Y image range ≈ 1.999×10⁻⁴ m`) |
| Resolution | 200 × 200 pixels (~1 µm/pixel) |
| Format | Tab-separated `.dat` (header + `x, y, z, count rate`) |
| Files | 4 timestamps on 2026-06-15 (11:40, 14:25, 18:16, 19:11) |

These wide scans match the use case described in `ROISegmentationLogic`: at 25 µm and above, bright spots are **clusters**, not resolvable single NVs. Individual POIs require zooming to **5–1 µm** FOV.

---

## Current Automation Status

### Implemented and Working

| Component | File(s) | Status | Notes |
|-----------|---------|--------|-------|
| **CIP NV detection** | `logic/image_analysis.py`, `logic/auto_nv_finder_logic.py` | ✅ Complete | Background subtraction, MAD noise, threshold, local maxima, shape filter, distance clustering, sub-pixel refinement |
| **Optimize → POI pipeline** | `logic/auto_nv_finder_logic.py` | ✅ Complete | Sequential refocus via `OptimizerLogic`, auto POI registration |
| **Auto NV Finder GUI** | `gui/poimanager/auto_nv_finder_widget.py` | ✅ Complete | Dock widget, candidate table, color markers |
| **TaskRunner integration** | `logic/tasks/auto_nv_find.py` | ✅ Complete | Configurable task; pauses conflicting scans |
| **Unit tests (CIP)** | `tests/test_auto_nv_finder.py` | ✅ Complete | Synthetic images; pipeline tested without full Qudi stack |
| **Cell boundary segmentation** | `logic/cell_segmentation_logic.py` | ✅ Offline | Median despike → Gaussian → Otsu → morphology; saves `_filtered.dat` |
| **ROI segmentation (cell − bright clusters)** | `logic/roi_segmentation_logic.py` | ✅ Offline | Adds MAD-based bright-cluster rejection; saves `_roi_filtered.dat` |
| **Image rebuild / visualization** | `logic/image_rebuild_logic.py` | ✅ Offline | Reconstructs 2D grid from `.dat`, exports PNG/PDF |
| **Segmentation validation test** | `tests/test_cell_segmentation/run_segmentation_test.py` | ✅ Passes | Verified on `Confocal/20260615-1140-42_confocal_xy_data.dat` |

### Documented but Not Implemented in Code

| Feature | Documented In | Code Reality |
|---------|---------------|--------------|
| `enable_multi_scale` (coarse→fine scans) | `10_configuration_guide.md`, `07_auto_nv_finder_architecture.md` | ❌ No `StatusVar`, no zoom logic in `auto_nv_finder_logic.py` |
| Auto-ODMR validation | `13_validation_steps.md` | ❌ `ODMRLogic` exists elsewhere; not connected to auto-finder |
| Auto-HBT validation | `13_validation_steps.md` | ❌ No correlator module |
| `fitlogic` connector usage | `auto_nv_finder_logic.py` | ⚠️ Connected but unused; refinement uses center-of-mass, not `FitLogic` |
| `min_optimization_quality` (R² gate) | Config docs | ⚠️ StatusVar declared; acceptance uses displacement only, not R² |

### Stub / Legacy (Not Part of Active Pipeline)

| Component | File(s) | Status |
|-----------|---------|--------|
| **AutomationLogic task tree** | `logic/automation.py` | 🔶 Skeleton — loads `auto.cfg` into `TreeModel`; no scan orchestration |
| **Automation GUI** | `gui/automation/automationgui.py` | 🔶 Skeleton — tree view only; run/pause/stop commented out |

---

## End-to-End Pipeline: Today vs Target

```
TODAY (single-scale, single FOV):
─────────────────────────────────
[User runs 200×200 µm scan manually]
        │
        ▼
[Auto NV Finder: CIP on current xy_image]
        │
        ▼
[Optimizer → POI for each bright spot]
        │
        ▼
STOP — may register clusters as false "NV" POIs at wide FOV


TARGET (multi-scale feedback loop):
───────────────────────────────────
[Phase A] Coarse scan (200×200 µm, 200 px)     ← reuse existing scan API
        │
        ▼
[Phase B] ROI segmentation → cell mask + bright cluster bboxes
        │                    (logic/roi_segmentation_logic.py)
        ▼
[Phase C] For each cluster bbox:
        │   set image_x/y_range to bbox + margin
        │   fine scan (e.g. 30×30 µm, 200 px → 0.15 µm/px)
        ▼
[Phase D] CIP detection on fine scan              ← existing AutoNVFinderLogic
        │
        ▼
[Phase E] Optimize → POI (optional ODMR gate)
        │
        ▼
[Phase F] Mark region explored; zoom out or next cluster
```

---

## Approach Comparison (Your Analysis vs This Codebase)

### 1. Traditional Computer Vision & Morphological Filtering

| | |
|---|---|
| **Your summary** | Median/Gaussian blur, adaptive threshold, connected components, bounding boxes → scanner zoom |
| **Already in repo** | **Yes — heavily** |
| **Where** | `ConfocalImageAnalysis` (NV spots); `CellSegmentationLogic` + `ROISegmentationLogic` (cell/cluster ROIs) |
| **Fit for zoom loop** | **Excellent** — `ROISegmentationLogic` already produces masks; bounding boxes are a ~10-line extension |
| **Verdict** | ✅ **Primary building block** |

Existing CIP clustering in `image_analysis.cluster_detections()` is **greedy distance-based** (brightest-first, merge within `min_distance`). It is **not DBSCAN**, but serves the same purpose for NV spot deduplication at fine scale.

### 2. DBSCAN Clustering

| | |
|---|---|
| **Your summary** | Point cloud of bright pixels; ignores isolated NVs; finds dense cluster aggregations |
| **Already in repo** | **Partially** — spatial clustering exists, but not DBSCAN |
| **Fit for zoom loop** | Good for **coarse-scale cluster discovery** when connected-component bboxes fail (irregular cluster shapes) |
| **Verdict** | ⚠️ **Optional Phase 2 enhancement** — add only if morphological bbox extraction proves insufficient on real cell data |

For 200×200 frames, DBSCAN is fast enough, but ROI segmentation already handles the macro cell body; DBSCAN adds most value for **sub-cluster splitting** inside large bright regions.

### 3. Coarse-to-Fine Multi-Scale Strategy (Adaptive Zoom Loop)

| | |
|---|---|
| **Your summary** | Wide scan → locate peaks → shrink FOV → fine scan → iterate |
| **Already in repo** | **Designed, not built** — `enable_multi_scale` in docs only |
| **Hardware support** | `ConfocalLogic.image_x_range`, `image_y_range`, `xy_resolution`, `start_scanning()` |
| **Verdict** | ✅ **Required next implementation** — this is the missing orchestration layer |

This is the only approach that directly solves **"zoom close in and out"** with existing scanner APIs.

### 4. Deep Learning (U-Net / YOLO)

| | |
|---|---|
| **Your summary** | Robust to drift and artifacts; high labeling/training cost |
| **Already in repo** | **No** |
| **Verdict** | ❌ **Defer indefinitely** — insufficient labeled data, high setup cost, hard to debug on hardware loop |

Revisit only if classical methods fail after multi-scale + ODMR validation on ≥50 real sessions.

---

## Decision: Recommended Hybrid Strategy

### Primary: Traditional CV + Coarse-to-Fine Loop

Combine what already works:

| Layer | Module | Role |
|-------|--------|------|
| **Macro navigation** | `ROISegmentationLogic` | Find cell body; reject ultra-bright clusters at wide FOV; export cluster bboxes |
| **Scanner control** | `ConfocalLogic` | Adjust `image_x_range` / `image_y_range`; trigger rescans |
| **Micro detection** | `AutoNVFinderLogic` + `ConfocalImageAnalysis` | CIP on fine scans only (inside ROI, not whole diamond) |
| **Position lock** | `OptimizerLogic` | Refocus each candidate |
| **Persistence** | `PoiManagerLogic` | Register confirmed NVs |
| **Quality gate (later)** | `ODMRLogic` | Reject non-NV⁻ emitters |

### Secondary (optional, not blocking):

- **DBSCAN** on coarse bright-pixel point cloud if bbox extraction misses merged clusters
- **Connected components** (`scipy.ndimage.label`) as simpler alternative to DBSCAN for bbox extraction from `bright_cluster_mask`

### Explicitly not chosen now:

- **Deep learning** — cost/benefit poor given existing CV coverage
- **Replacing CIP with DBSCAN at fine scale** — CIP local-max + shape filter is tuned for single NV PSFs

---

## Implementation Plan (Summary)

Full step-by-step plans live in **[doc 15](15_phased_implementation_plan.md)**. Phase overview:

| Phase | Focus | Doc |
|-------|-------|-----|
| **0** (done) | CIP + offline segmentation + Auto NV Finder GUI | — |
| **1** | Shared parsers, bbox extraction, unify duplicate code | [15 § Phase 1](15_phased_implementation_plan.md#phase-1--shared-foundation--code-consolidation) |
| **2** | Optimize cell / ROI / CIP algorithms offline | [17](17_algorithm_optimization.md) |
| **3** | Wire segmentation to live `ConfocalLogic` + GUI overlays | [15 § Phase 3](15_phased_implementation_plan.md#phase-3--live-integration-segmentation--confocal) |
| **4** | Coarse-to-fine zoom state machine | [15 § Phase 4](15_phased_implementation_plan.md#phase-4--multi-scale-zoom-loop) |
| **5** | ODMR / R² quality gates | [15 § Phase 5](15_phased_implementation_plan.md#phase-5--validation--quality-gates) |
| **6** | TaskRunner orchestration, docs, AutomationLogic decision | [15 § Phase 6](15_phased_implementation_plan.md#phase-6--orchestration--production-hardening) |
| **7** | DBSCAN, PID zoom, ML — only if needed | [15 § Phase 7](15_phased_implementation_plan.md#phase-7--optional-enhancements-after-core-loop-works) |

**Testing data:** Current `Confocal/` 200 µm scans are **incomplete** for Phases 4–6. See **[doc 16](16_testing_data_requirements.md)**.

---

## Mapping: Your Comparison Matrix → This Decision

| Approach | Setup | Speed | Robustness | Best Use Here | Decision |
|----------|-------|-------|------------|---------------|----------|
| Traditional CV | Very Low | Instant | Moderate | ROI masks, NV CIP, bbox extraction | **Use now** |
| DBSCAN | Low | Fast | High | Optional coarse cluster splitting | **Later if needed** |
| Coarse-to-Fine | Moderate | Efficient | High | Scanner zoom orchestration | **Build next (Phase 2)** |
| Deep Learning | High | Slow | Exceptional | Drift/artifact-heavy edge cases | **Do not pursue now** |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Scanner lock conflicts (confocal vs optimizer vs ODMR) | Extend TaskRunner `pausetasks`; serialize via existing `module_state()` checks |
| Cluster detected as single NV at wide FOV | **Never run CIP on coarse scan for POI registration** when multi-scale enabled |
| Parameter drift (laser power, background) | MAD-based thresholds already robust; auto color range; per-session threshold UI |
| `enable_multi_scale` doc/code mismatch | Phase 2 explicitly implements documented config keys |
| Segmentation fails on atypical cells | Manual bbox override in GUI; fallback to peak-based zoom (sliding window) |

---

## Key Code References

| Purpose | Location |
|---------|----------|
| NV CIP pipeline | `logic/auto_nv_finder_logic.py` → `_detect_candidates()` |
| CIP utilities | `logic/image_analysis.py` → `ConfocalImageAnalysis` |
| Cell / cluster ROI | `logic/roi_segmentation_logic.py` → `segment_roi()` |
| Scanner range control | `logic/confocal_logic.py` → `image_x_range`, `image_y_range`, `start_scanning()` |
| Offline validation | `tests/test_cell_segmentation/run_segmentation_test.py` |
| Validation roadmap | `documentation/automation/13_validation_steps.md` |

---

## Summary

1. **~60% built:** Traditional CV (cell, ROI, CIP) + optimize→POI pipeline; modules mostly **standalone**.
2. **Next:** Phase 1 consolidation → Phase 2 algo tuning (needs annotations) → Phase 3 live wiring → Phase 4 zoom loop (needs fine-scale paired data).
3. **DBSCAN / ML deferred** until classical bbox + CIP tuning fails on real data.
4. **Read [15](15_phased_implementation_plan.md), [16](16_testing_data_requirements.md), [17](17_algorithm_optimization.md)** for comprehensive next steps.

For day-to-day operation today, use the [User Guide](12_user_guide.md). Auto NV Finder operates on **whatever FOV is scanned** — at 200 µm it detects clusters, not individual NVs.
