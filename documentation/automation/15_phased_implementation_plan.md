# 15 — Phased Implementation Plan (Next Steps)

> **Document 15 of the Automation Series**  
> Comprehensive step-by-step plan for connecting standalone modules, optimizing algorithms, building the multi-scale loop, and validating end-to-end automation. **No deadlines** — phases are ordered by dependency.

**Related documents:**
- [14 — Roadmap & Status](14_automation_roadmap_and_status.md) — executive summary and approach decision
- [16 — Testing Data Requirements](16_testing_data_requirements.md) — datasets needed per phase
- [17 — Algorithm Optimization](17_algorithm_optimization.md) — ROI, cell boundary, CIP tuning details

---

## How to Read This Plan

Each phase lists:

1. **Prerequisites** — what must exist before starting
2. **Goals** — what “done” means
3. **Steps** — ordered, actionable tasks
4. **Module connections** — which standalone pieces get wired together
5. **Tests** — what to run (see doc 16 for data)
6. **Exit criteria** — objective checks before moving on

Phases are **sequential**. Do not skip Phase 1 (shared foundation) even if eager to build the zoom loop.

---

## Standalone Module Inventory

These modules exist but are **not connected** in a live Qudi session today:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CONNECTED (live pipeline)                         │
│  ConfocalLogic ──► AutoNVFinderLogic ──► OptimizerLogic ──► PoiManager  │
│       ▲                    │                                             │
│       │              ConfocalImageAnalysis (imported, not a Logic module)  │
└───────┼────────────────────┼─────────────────────────────────────────────┘
        │                    │
        │              NOT CONNECTED
        ▼                    ▼
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│ CellSegmentation  │  │ ROISegmentation   │  │ ImageRebuildLogic │
│ Logic             │  │ Logic             │  │                   │
│ (.dat only)       │  │ (.dat only)       │  │ (.dat only)       │
└───────────────────┘  └───────────────────┘  └───────────────────┘
        │                      │
        └──────────┬───────────┘
                   │  duplicate parse_dat_file() in both modules
                   ▼
┌───────────────────┐  ┌───────────────────┐
│ AutomationLogic   │  │ AutomationGui       │
│ (TreeModel stub)  │  │ (UI stub)           │
└───────────────────┘  └───────────────────┘
```

**Target architecture after all phases:**

```
ConfocalLogic
    │
    ├──► SegmentationLogic (unified: cell + ROI + bboxes)  ◄── NEW wrapper
    │         │
    │         └── uses optimized algorithms (doc 17)
    │
    └──► MultiScaleNVFinderLogic (or extended AutoNVFinderLogic)
              │
              ├── coarse scan → segment → queue bboxes
              ├── fine scan per bbox → CIP → optimize → POI
              └── optional ODMR gate
```

---

## Phase 1 — Shared Foundation & Code Consolidation

**Prerequisites:** None (baseline code exists)

**Goals:** Eliminate duplication; one code path for `.dat` files and live `xy_image`; make segmentation callable from Qudi logic modules.

### Steps

#### 1.1 Create shared confocal image utilities

| Step | Action | Output |
|------|--------|--------|
| 1.1.1 | Add `logic/confocal_image_utils.py` (or extend `image_rebuild_logic.py`) | Single module for parsing and grid handling |
| 1.1.2 | Implement `xy_image_from_dat(filepath)` | Returns `(image, x_coords, y_coords, header)` |
| 1.1.3 | Implement `xy_image_from_scan_array(xy_image)` | Same tuple shape from `ConfocalLogic.xy_image` |
| 1.1.4 | Implement `physical_extent(image)` | Returns `x_min, x_max, y_min, y_max, pixel_size_x, pixel_size_y` |
| 1.1.5 | Refactor `CellSegmentationLogic`, `ROISegmentationLogic`, `ImageRebuildLogic` to import shared utils | Remove duplicated `parse_dat_file()` |

#### 1.2 Promote segmentation to a Qudi Logic module (optional but recommended)

| Step | Action | Output |
|------|--------|--------|
| 1.2.1 | Create `logic/segmentation_logic.py` extending `GenericLogic` | Qudi-connectable module |
| 1.2.2 | Wrap `CellSegmentationLogic` + `ROISegmentationLogic` methods | `segment_cell()`, `segment_roi()`, `get_bboxes()` |
| 1.2.3 | Add `StatusVar` parameters for tunable algo knobs (see doc 17) | Persisted config |
| 1.2.4 | Register in `.cfg` with connector to `confocallogic` | Loadable in Qudi |

#### 1.3 Standardize result objects

| Step | Action | Output |
|------|--------|--------|
| 1.3.1 | Define `SegmentationResult` dataclass/named tuple | Fields: `cell_mask`, `roi_mask`, `bright_cluster_mask`, `contours`, `bboxes`, `metadata` |
| 1.3.2 | Define `BoundingBox` with physical coords + pixel indices | Used by zoom loop |
| 1.3.3 | Add `bbox_from_mask(mask, x_coords, y_coords, min_area_px)` | Connected-components → list of bboxes |

#### 1.4 Baseline regression tests

| Step | Action | Output |
|------|--------|--------|
| 1.4.1 | Add `tests/test_confocal_image_utils.py` | Parse all 4 `Confocal/*.dat`; assert shape 200×200 |
| 1.4.2 | Assert `xy_image_from_dat` ≡ `xy_image_from_scan_array` when fed same data | Round-trip test |
| 1.4.3 | Run existing `tests/test_auto_nv_finder.py` — must still pass | No CIP regression |

**Tests / data:** Phase 1 dataset — see [16 § Phase 1](16_testing_data_requirements.md#phase-1--foundation)

**Exit criteria:**
- [ ] One parser used everywhere; no duplicate `parse_dat_file` in cell/ROI modules
- [ ] `BoundingBox` extraction works on `bright_cluster_mask` from offline `.dat`
- [ ] All unit tests green

---

## Phase 2 — Algorithm Optimization (Offline First)

**Prerequisites:** Phase 1 complete

**Goals:** Tune cell boundary, ROI, bright-cluster, and CIP parameters against real and synthetic data **before** wiring live hardware. Full detail in [doc 17](17_algorithm_optimization.md).

### Steps (summary — see doc 17 for parameter matrix)

#### 2.1 Cell boundary optimization

| Step | Action |
|------|--------|
| 2.1.1 | Parameterize median kernel, Gaussian sigma, Otsu vs percentile fallback, morphology iterations |
| 2.1.2 | Build sweep script: grid search over parameter ranges on labeled coarse scans |
| 2.1.3 | Metrics: IoU vs manual cell mask, boundary smoothness, false positive area outside cell |
| 2.1.4 | Lock default parameters; document sensitivity in doc 17 |

#### 2.2 ROI & bright-cluster optimization

| Step | Action |
|------|--------|
| 2.2.1 | Tune MAD multiplier (currently hardcoded `10σ`) — make configurable |
| 2.2.2 | Add minimum cluster area filter (reject single-pixel spikes) |
| 2.2.3 | Optional: merge nearby bright clusters before bbox extraction |
| 2.2.4 | Compare connected-components bboxes vs DBSCAN on coarse bright-pixel point cloud |
| 2.2.5 | Validate ROI mask excludes clusters but retains mid-intensity cell interior |

#### 2.3 CIP pipeline optimization (fine-scale)

| Step | Action |
|------|--------|
| 2.3.1 | Tune `detection_threshold_sigma`, `spot_diameter`, `background_filter_size` per FOV |
| 2.3.2 | Add FOV-aware defaults (coarse vs fine scan presets in config) |
| 2.3.3 | Wire `FitLogic` 2D Gaussian for sub-pixel refinement (replace center-of-mass) |
| 2.3.4 | Enforce `min_optimization_quality` (R²) in acceptance logic |

#### 2.4 Visualization & comparison tooling

| Step | Action |
|------|--------|
| 2.4.1 | Extend `run_segmentation_test.py` → batch mode over dataset folder |
| 2.4.2 | Overlay script: cell contour + cluster bboxes + ROI on original image |
| 2.4.3 | Export side-by-side PNGs for manual review (`tests/test_cell_segmentation/output/`) |

**Tests / data:** Phase 2 dataset — coarse scans with **manual ground-truth masks** (see doc 16)

**Exit criteria:**
- [ ] Cell mask IoU ≥ target on ≥3 annotated coarse scans (set target in doc 16)
- [ ] Cluster bbox count matches manual count ±1 on annotated scans
- [ ] CIP detects synthetic NVs at fine FOV with ≥95% recall in unit tests
- [ ] Default parameters documented and committed to `StatusVar` defaults

---

## Phase 3 — Live Integration (Segmentation ↔ Confocal)

**Prerequisites:** Phases 1–2 complete

**Goals:** Run segmentation on `confocallogic().xy_image` inside Qudi; display overlays; no zoom loop yet.

### Steps

#### 3.1 Connect segmentation to ConfocalLogic

| Step | Action | Output |
|------|--------|--------|
| 3.1.1 | In `SegmentationLogic`, add `analyze_current_scan()` | Reads `confocallogic().xy_image` |
| 3.1.2 | Emit signals: `sigSegmentationComplete`, `sigBboxesFound` | GUI can subscribe |
| 3.1.3 | Guard: refuse if `xy_image` empty or scan in progress | Safe state checks |
| 3.1.4 | Hook `signal_xy_image_updated` for optional auto-segment after scan | Config flag `auto_segment_after_scan` |

#### 3.2 GUI overlay integration

| Step | Action | Output |
|------|--------|--------|
| 3.2.1 | Add overlay layer to confocal scan plot OR POI Manager | Cell contour (cyan), ROI (green fill), cluster bboxes (yellow rects) |
| 3.2.2 | Toggle buttons: show/hide cell / ROI / clusters | Operator control |
| 3.2.3 | Parameter spinboxes linked to `SegmentationLogic` StatusVars | Live retune |
| 3.2.4 | "Export segmentation" → save masks + bboxes JSON alongside `.dat` | Reproducibility |

#### 3.3 Connect ImageRebuildLogic to live pipeline

| Step | Action |
|------|--------|
| 3.3.1 | Add `save_xy_image_as_dat(confocallogic, path)` using shared utils |
| 3.3.2 | After segmentation, optionally save `_roi_filtered.dat` from live scan |

#### 3.4 Offline ↔ live parity test

| Step | Action |
|------|--------|
| 3.4.1 | Load `.dat` into numpy array mimicking `xy_image` |
| 3.4.2 | Run offline `segment_roi()` and live `analyze_current_scan()` on same array |
| 3.4.3 | Assert masks identical (bitwise) |

**Tests / data:** Phase 3 — replay `.dat` through dummy confocal if hardware unavailable (see doc 16)

**Exit criteria:**
- [ ] Segmentation runs on live scan completion in Qudi GUI
- [ ] Overlays match offline PNGs from Phase 2 for same input
- [ ] Operator can adjust parameters and re-run without rescanning

---

## Phase 4 — Multi-Scale Zoom Loop

**Prerequisites:** Phase 3 complete; fine-scale test data available (doc 16)

**Goals:** Automated coarse → segment → zoom → fine scan → CIP → optimize → POI.

### Steps

#### 4.1 Extend AutoNVFinderLogic (or add MultiScaleNVFinderLogic)

| Step | Action |
|------|--------|
| 4.1.1 | Add StatusVars: `enable_multi_scale`, `coarse_fov_um`, `fine_fov_um`, `bbox_margin_fraction`, `max_regions_per_run`, `max_zoom_iterations` |
| 4.1.2 | Implement region queue from `SegmentationLogic.get_bboxes()` |
| 4.1.3 | Save/restore scan settings: `image_x_range`, `image_y_range`, `xy_resolution`, Z |
| 4.1.4 | State machine: `COARSE_SCAN` → `SEGMENTING` → `FINE_SCAN` → `DETECTING` → `OPTIMIZING` → `NEXT_REGION` → `COMPLETE` |

#### 4.2 Scanner control helpers

| Step | Action |
|------|--------|
| 4.2.1 | `set_scan_window(center_x, center_y, fov_x, fov_y, resolution)` wrapper on ConfocalLogic |
| 4.2.2 | Validate window inside scanner physical limits |
| 4.2.3 | Center fine scan on cluster bbox centroid + margin |
| 4.2.4 | After fine pass, optionally zoom further if CIP finds extended bright structure at edge |

#### 4.3 Detection policy by scale

| Step | Action |
|------|--------|
| 4.3.1 | **Coarse scan:** segmentation only — **do not register POIs from coarse CIP** |
| 4.3.2 | **Fine scan:** run full CIP + optimize + POI |
| 4.3.3 | Deduplicate POIs: reject if new POI within `spot_diameter` of existing |
| 4.3.4 | Mark bbox as `explored` / `skipped` / `failed` in region queue |

#### 4.4 GUI & operator controls

| Step | Action |
|------|--------|
| 4.4.1 | "Start Multi-Scale Find" button (separate from single-scale) |
| 4.4.2 | Progress: current region index, FOV size, state name |
| 4.4.3 | Pause/stop preserves queue state |
| 4.4.4 | Manual "Skip region" / "Force zoom here" for failed segmentation |

#### 4.5 TaskRunner task

| Step | Action |
|------|--------|
| 4.5.1 | New task `logic/tasks/coarse_fine_nv_find.py` |
| 4.5.2 | Config: FOV presets, max regions, validation flags |
| 4.5.3 | `pausetasks: ['scan']` and mutex with optimizer |

**Tests / data:** Phase 4 — **paired coarse + fine scans** of same physical region (doc 16)

**Exit criteria:**
- [ ] One-button run: coarse 200 µm → ≥1 fine scan → ≥1 POI on known NV cluster
- [ ] No POIs registered from coarse scan alone
- [ ] Queue processes all bboxes without scanner lock deadlock
- [ ] Stop/resume leaves consistent state

---

## Phase 5 — Validation & Quality Gates

**Prerequisites:** Phase 4 producing POIs on fine scans

**Goals:** Reduce false POIs; confirm NV⁻ charge state where hardware allows.

### Steps

#### 5.1 Optimizer quality gate

| Step | Action |
|------|--------|
| 5.1.1 | Read optimizer fit R² from `OptimizerLogic` / `FitLogic` |
| 5.1.2 | Reject candidate if R² < `min_optimization_quality` |
| 5.1.3 | Log rejection reason to candidate table |

#### 5.2 Optional Auto-ODMR (ODMRLogic exists)

| Step | Action |
|------|--------|
| 5.2.1 | Add optional connector `odmrlogic` to AutoNVFinderLogic |
| 5.2.2 | After optimize, run rapid frequency sweep (configurable range/step) |
| 5.2.3 | Accept if dip contrast > threshold at ~2.87 GHz |
| 5.2.4 | Tag POI: `NV_###` → `cNV_###` (confirmed) or delete on fail |
| 5.2.5 | Serialize scanner access with ODMR (see doc 13) |

#### 5.3 Auto-HBT (future — hardware dependent)

| Step | Action |
|------|--------|
| 5.3.1 | Define `AutocorrelationInterface` |
| 5.3.2 | Measure g²(0) at POI; accept if < 0.5 |
| 5.3.3 | Tag `sNV_###` for single-emitter confirmed |

#### 5.4 Cluster-at-coarse guard (even without ODMR)

| Step | Action |
|------|--------|
| 5.4.1 | If `enable_multi_scale=False`, warn when pixel size > 0.5 µm |
| 5.4.2 | Optional: reject CIP candidates whose fitted sigma > `spot_diameter` |

**Tests / data:** Phase 5 — POIs with known ODMR positive/negative examples (doc 16)

**Exit criteria:**
- [ ] Documented false-positive rate on benchmark dataset
- [ ] ODMR gate optional and off by default
- [ ] R² gate active and tested

---

## Phase 6 — Orchestration & Production Hardening

**Prerequisites:** Phases 4–5 stable on hardware

**Goals:** One coherent operator workflow; resolve legacy stubs; full documentation.

### Steps

#### 6.1 TaskRunner as primary orchestrator

| Step | Action |
|------|--------|
| 6.1.1 | Document task recipes: `auto_nv_find` (single-scale), `coarse_fine_nv_find` (multi-scale) |
| 6.1.2 | Add preflight checks: scanner free, POI manager loaded, Z in range |
| 6.1.3 | Structured log file per run (timestamp, regions, POIs, failures) |

#### 6.2 AutomationLogic / AutomationGui decision

| Option | Action |
|--------|--------|
| **A — Deprecate** | Mark `logic/automation.py` legacy; document TaskRunner-only workflow |
| **B — Revive** | Map tree nodes to TaskRunner tasks; wire GUI start/pause/stop |

Pick one; do not maintain two parallel orchestration systems.

#### 6.3 Documentation & operator guide

| Step | Action |
|------|--------|
| 6.3.1 | Update `12_user_guide.md` with multi-scale workflow |
| 6.3.2 | Update `10_configuration_guide.md` — implement all documented StatusVars |
| 6.3.3 | Update `11_troubleshooting.md` — segmentation + zoom failures |
| 6.3.4 | Add `documentation/automation/examples/` with sample configs |

#### 6.4 Performance & safety

| Step | Action |
|------|--------|
| 6.4.1 | Benchmark: time per cell, time per NV, total scan line count |
| 6.4.2 | Max candidates / max regions hard limits |
| 6.4.3 | Emergency stop always completes current line, not current region |

**Exit criteria:**
- [ ] Operator can run full workflow from GUI or TaskRunner without editing code
- [ ] All docs match implemented StatusVars
- [ ] Benchmark recorded on reference dataset

---

## Phase 7 — Optional Enhancements (After Core Loop Works)

Only pursue if Phase 4 exit criteria fail on real data:

| Enhancement | Trigger condition |
|-------------|-------------------|
| DBSCAN cluster splitting | Connected-components merges distinct clusters |
| Sliding-window peak finder | Segmentation misses cells with weak auto-fluorescence |
| Adaptive PID zoom tracker | Fine scan hits cluster edge — need dynamic FOV expansion |
| Deep learning segmenter | Classical pipeline fails on >30% of cells after tuning |

---

## Module Connection Checklist

Use this checklist to track wiring progress:

| From | To | Phase | Done |
|------|-----|-------|------|
| `ConfocalLogic.xy_image` | `SegmentationLogic` | 3 | ☐ |
| `SegmentationLogic.bboxes` | `AutoNVFinderLogic` region queue | 4 | ☐ |
| `ConfocalLogic` scan params | Zoom window setter | 4 | ☐ |
| `AutoNVFinderLogic` | `OptimizerLogic` | 0 (exists) | ☑ |
| `AutoNVFinderLogic` | `PoiManagerLogic` | 0 (exists) | ☑ |
| `AutoNVFinderLogic` | `ODMRLogic` | 5 | ☐ |
| `ImageRebuildLogic` | Live scan save | 3 | ☐ |
| `CellSegmentationLogic` | Shared utils (no duplicate parser) | 1 | ☐ |
| `ROISegmentationLogic` | Shared utils | 1 | ☐ |
| `TaskRunner` | `coarse_fine_nv_find` | 4 | ☐ |
| `AutomationLogic` | TaskRunner or deprecated | 6 | ☐ |

---

## Testing Strategy (All Phases)

| Level | What | Where |
|-------|------|-------|
| **Unit** | CIP stages, mask ops, bbox extraction, parsers | `tests/test_*` |
| **Offline integration** | Full segmentation on `.dat` corpus | `tests/test_cell_segmentation/` |
| **Replay integration** | Inject `.dat` as fake `xy_image` | New `tests/test_segmentation_replay/` |
| **Hardware integration** | Live coarse→fine on sample | Manual + logged benchmark |
| **Regression** | Compare mask/bbox outputs to committed golden files | `tests/fixtures/` (create in Phase 2) |

Full dataset specifications: [doc 16](16_testing_data_requirements.md).

---

## Recommended Order of Work

```
Phase 1  Shared foundation
    ↓
Phase 2  Algorithm optimization (offline, needs annotated data)
    ↓
Phase 3  Live segmentation + overlays
    ↓
Phase 4  Multi-scale zoom loop  ← core automation goal
    ↓
Phase 5  ODMR / quality gates
    ↓
Phase 6  Orchestration + docs
    ↓
Phase 7  Optional enhancements (only if needed)
```

**Parallel track:** While waiting for fine-scale hardware data (doc 16), continue Phase 1–2 and synthetic CIP tests.

---

## Summary

| Phase | Focus |
|-------|-------|
| **1** | Unify parsers, bboxes, Qudi module wrapper |
| **2** | Optimize cell / ROI / CIP algorithms offline |
| **3** | Wire segmentation to live confocal + GUI overlays |
| **4** | Coarse-to-fine zoom state machine |
| **5** | ODMR, R² gates, false-positive reduction |
| **6** | TaskRunner, docs, deprecate or revive AutomationLogic |
| **7** | DBSCAN, PID zoom, ML — only if needed |

Current `Confocal/` data supports **Phase 1–2 partial work only**. Phases 3–6 require additional datasets described in doc 16.
