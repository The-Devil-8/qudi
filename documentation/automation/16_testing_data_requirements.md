# 16 — Testing Data Requirements & Catalog

> **Document 16 of the Automation Series**  
> Defines what test data exists today, what is missing, how to organize new acquisitions, and what each implementation phase needs to validate.

**Related:** [15 — Phased Implementation Plan](15_phased_implementation_plan.md) | [17 — Algorithm Optimization](17_algorithm_optimization.md)

---

## Current Data Inventory (`Confocal/`)

| File (timestamp) | Type | FOV | Resolution | Notes |
|------------------|------|-----|------------|-------|
| `20260615-1140-42_*` | Coarse XY | ~200 µm | 200×200 (~1 µm/px) | Used in `run_segmentation_test.py` |
| `20260615-1425-59_*` | Coarse XY | ~200 µm | 200×200 | Same geometry, different Z |
| `20260615-1816-21_*` | Coarse XY | ~200 µm | 200×200 | Same geometry |
| `20260615-1911-38_*` | Coarse XY | ~200 µm | 200×200 | Same geometry |

**Formats present per scan:**
- `*_confocal_xy_data.dat` — tab-separated with full header (preferred for algorithms)
- `*_confocal_xy_image_Dev1Ctr3.dat` — raw 200×200 count matrix (no x/y columns)
- `*_fig.png` / `*_fig.pdf` — Qudi-rendered figures (visual reference only)
- `*_scan_raw_pixel_image_raw.png` — GUI screenshot

### What Current Data Is Good For

| Use case | Supported? |
|----------|------------|
| Parser / grid reconstruction tests | ✅ Yes |
| Offline cell boundary tuning (initial) | ✅ Partial — 4 coarse scans, same FOV |
| Offline ROI / bright-cluster tuning | ✅ Partial |
| CIP unit tests (synthetic) | ✅ Yes (existing `test_auto_nv_finder.py`) |
| CIP on real fine-scale NV spots | ❌ No — pixel size too coarse |
| Multi-scale zoom loop validation | ❌ No — no paired fine scans |
| Ground-truth bbox / mask evaluation | ❌ No — no annotations |
| ODMR confirmation labels | ❌ No |
| Multi-cell / empty field scenarios | ❌ Unknown — need review of all 4 images |

### What Current Data Cannot Validate

1. **Individual NV detection** — at 1 µm/px, PSF is sub-pixel; CIP finds clusters not single NVs
2. **Zoom correctness** — no recordings at 30 µm, 10 µm, 5 µm, 1 µm FOV of the same cluster
3. **Algorithm generalization** — 4 scans, one geometry, one sample session
4. **Edge cases** — empty diamond, dust-only, multiple cells, weak auto-fluorescence cells
5. **Temporal drift** — laser power change between coarse and fine scan

---

## Proposed Test Data Repository Structure

Create a dedicated dataset root (recommended path):

```
tests/fixtures/confocal/
├── README.md                          # Index of all fixtures
├── coarse/                            # Wide FOV scans
│   ├── cell_single/
│   ├── cell_multi/
│   ├── empty_diamond/
│   └── weak_autofluorescence/
├── fine/                              # High-res scans (paired to coarse)
│   └── {coarse_id}/
│       ├── zoom_30um/
│       ├── zoom_10um/
│       └── zoom_5um/
├── synthetic/                         # Generated numpy / .dat fixtures
│   ├── single_nv/
│   ├── multi_nv/
│   └── cluster_only/
├── annotations/                       # Ground truth
│   ├── masks/                         # PNG or NPY boolean masks
│   ├── bboxes/                        # JSON per scan
│   └── pois/                          # Known NV positions (meters)
└── golden/                            # Committed expected outputs for regression
    ├── segmentation/
    └── cip/
```

**Naming convention:**

```
{YYYYMMDD-HHMMSS}_{type}_{fov}um_{resolution}px_{label}.dat

Examples:
20260701-120000_coarse_200um_200px_cell_A.dat
20260701-120530_fine_30um_200px_cell_A_cluster_1.dat
20260701-121000_fine_5um_200px_cell_A_cluster_1_nv_field.dat
```

---

## Dataset Categories Required

### Category A — Coarse scans (200 µm class)

**Purpose:** Cell boundary, ROI, bright-cluster bbox extraction

| Subcategory | Min count | Description |
|-------------|-----------|-------------|
| A1 Single cell, strong auto-fluorescence | 5 | Clear cell blob, ≥1 bright cluster inside |
| A2 Single cell, weak auto-fluorescence | 3 | Hard segmentation case |
| A3 Multiple cells in FOV | 3 | Tests separate bboxes / region queue |
| A4 Empty diamond (no cell) | 2 | Should return empty region queue |
| A5 Cell at FOV edge (partial) | 2 | Boundary clipping tests |
| A6 High cluster density | 2 | Many bright spots — queue ordering |

**Required metadata per file (sidecar JSON):**

```json
{
  "scan_id": "20260701-120000_coarse_200um_200px_cell_A",
  "fov_um": [200, 200],
  "resolution": [200, 200],
  "z_m": 2.31e-5,
  "laser_power_percent": 100,
  "notes": "Single cell, center of FOV"
}
```

### Category B — Fine scans (paired to coarse)

**Purpose:** CIP detection, optimize→POI, multi-scale loop

For each annotated coarse scan with ≥1 cluster bbox, acquire:

| FOV (µm) | Resolution | Pixel size | Min per cluster |
|----------|------------|------------|-----------------|
| 30 | 200×200 | ~0.15 µm | 1 scan centered on cluster centroid |
| 10 | 200×200 | ~0.05 µm | 1 scan (optional, higher precision) |
| 5 | 200×200 | ~0.025 µm | 1 scan for NV-dense regions |
| 1–2 | 100×100 or 200×200 | sub-µm | 1 scan for optimizer validation |

**Critical:** Record **physical center** (x, y) used for each fine scan so replay can verify zoom logic.

```json
{
  "parent_coarse_id": "20260701-120000_coarse_200um_200px_cell_A",
  "cluster_index": 0,
  "bbox_m": [1.2e-4, 1.45e-4, 8.0e-5, 1.05e-4],
  "scan_center_m": [1.325e-4, 9.25e-5],
  "fov_um": [30, 30]
}
```

### Category C — Ground-truth annotations

**Purpose:** Objective metrics for algorithm optimization (Phase 2)

| Annotation type | Format | Tool suggestions |
|-----------------|--------|------------------|
| Cell boundary mask | PNG (binary) or `.npy` | ImageJ, napari, manual polygon → raster |
| Bright cluster bboxes | JSON list of `[x_min, x_max, y_min, y_max]` in meters | Draw on confocal figure |
| ROI mask (mid-intensity) | PNG / NPY | Derived or manual |
| Known NV positions | JSON list of `{x_m, y_m, z_m, verified_odmr: bool}` | From optimizer + ODMR |
| Negative controls | JSON — positions that are **not** NVs | Dust, scratches |

**Minimum annotations before Phase 2 sign-off:**

| Asset | Minimum |
|-------|---------|
| Coarse scans with cell mask | 5 |
| Coarse scans with cluster bboxes | 5 |
| Fine scans with NV position labels | 3 regions × ≥5 NVs each |

### Category D — ODMR-labeled POIs (Phase 5)

| Subcategory | Min count | Fields |
|-------------|-----------|--------|
| Confirmed NV⁻ (dip >5% at 2.87 GHz) | 10 | position, contrast, frequency |
| Rejected (no dip) | 10 | position, reason (dust, cluster, etc.) |

### Category E — Synthetic fixtures (always available)

Generate without hardware — maintain in repo:

| Fixture | Purpose | Location |
|---------|---------|----------|
| Single NV Gaussian spot | CIP recall | `tests/test_auto_nv_finder.py` (exists) |
| Multi-NV grid | Clustering / max_candidates | Extend existing tests |
| Synthetic cell blob + clusters | Segmentation IoU | New `tests/fixtures/synthetic/` |
| Noise-only field | False positive rate | New |

**Synthetic cell generator spec:**

- Background: 3k–8k c/s + Gaussian noise
- Cell: elliptical blob, 40–80 µm effective diameter, moderate auto-fluorescence
- Clusters: 2–5 Gaussian peaks, 10×–100× brighter than cell body
- Isolated NVs: only in fine-scale synthetic images (≤0.2 µm FWHM at fine pixel size)

---

## Phase-by-Phase Data Requirements

### Phase 1 — Foundation

| Need | Source | Blocking? |
|------|--------|-----------|
| 4 existing coarse `.dat` files | `Confocal/` | No |
| Consistent parse round-trip | Same files | No |

**Action:** Move or symlink `Confocal/*.dat` → `tests/fixtures/confocal/coarse/` with README index.

---

### Phase 2 — Algorithm Optimization

| Need | Source | Blocking? |
|------|--------|-----------|
| Annotated cell masks (Category C) | **Must acquire + label** | **Yes** for meaningful tuning |
| Annotated cluster bboxes | **Must acquire + label** | **Yes** for bbox metrics |
| Parameter sweep outputs | Generated from above | No |
| Synthetic cell images | Generate in repo | No — unblocks partial work |

**Action items:**
1. Label at least 3 of existing 4 coarse scans (cell outline + cluster boxes)
2. Acquire 2 additional coarse scans (multi-cell, empty)
3. Commit golden mask outputs once defaults locked

---

### Phase 3 — Live Integration

| Need | Source | Blocking? |
|------|--------|-----------|
| Any coarse `.dat` replayable as fake `xy_image` | Fixtures | No |
| 1 live coarse scan on hardware | Lab | Soft — replay sufficient for dev |
| Visual reference PNGs | Existing `*_fig.png` | No |

---

### Phase 4 — Multi-Scale Loop

| Need | Source | Blocking? |
|------|--------|-----------|
| Paired coarse + fine scans (Category B) | **Must acquire** | **Yes** |
| Parent/child metadata JSON | Record during acquisition | Yes |
| ≥1 cluster with known NVs at fine scale | Category C POI labels | Yes for POI validation |

**Minimum acquisition session (one lab day):**

1. Coarse 200 µm scan of cell with visible clusters
2. Annotate cluster bboxes manually (or from Phase 2 algo)
3. For each cluster: fine 30 µm scan centered on bbox
4. Optionally: 5 µm scan on brightest sub-region
5. Manually identify ≥3 NVs in fine scan (optimizer + visual)
6. Save all `.dat` + sidecar JSON

---

### Phase 5 — Validation Gates

| Need | Source | Blocking? |
|------|--------|-----------|
| ODMR-positive POIs | Category D | Yes for ODMR gate tuning |
| ODMR-negative bright spots | Category D | Yes for specificity |

---

### Phase 6 — Production Benchmark

| Need | Source |
|------|--------|
| Full workflow on 1 cell end-to-end | Categories A + B + C |
| Logged timing + POI count | Automated run log |

---

## Acquisition Checklist (Lab Session)

Use this when collecting new data:

### Before scanning

- [ ] Record sample ID, laser power, MW off for confocal
- [ ] Note Z focus height; keep fixed for coarse→fine pairs
- [ ] Set clock frequency / return slowness (match existing: 500 Hz, 50 steps)

### Coarse scan

- [ ] FOV: 200 µm × 200 µm (or document if different)
- [ ] Resolution: 200 × 200
- [ ] Save `*_confocal_xy_data.dat` (not image matrix only)
- [ ] Export Qudi figure PNG for annotation reference
- [ ] Write sidecar JSON metadata

### Annotation (same day)

- [ ] Draw cell boundary → save mask PNG/NPY
- [ ] Draw cluster bboxes → save JSON (meters)
- [ ] Note cluster count expected for zoom queue

### Fine scans (per cluster)

- [ ] Set FOV from bbox + 10% margin (target ~30 µm first)
- [ ] Resolution: 200 × 200
- [ ] Record scan center coordinates from Qudi
- [ ] Link to parent coarse scan ID in JSON
- [ ] Save `.dat` + PNG

### NV ground truth (fine scan)

- [ ] Mark ≥3 candidate spots visually
- [ ] Run manual optimizer on each; record accepted positions
- [ ] Optional: ODMR confirm; tag in JSON

---

## Regression Testing with Golden Files

Once Phase 2 defaults are locked:

1. Run segmentation on fixture corpus → save masks/bboxes to `tests/fixtures/golden/segmentation/{scan_id}/`
2. Commit golden outputs
3. CI test: `assert np.array_equal(new_mask, golden_mask)` or IoU > 0.99

Similarly for CIP on synthetic fine images:

```
tests/fixtures/golden/cip/fine_5um_synthetic_001_candidates.json
```

---

## Metrics to Record Per Dataset

| Metric | Formula / method | Used in |
|--------|------------------|---------|
| Cell mask IoU | `intersection / union` vs manual mask | Phase 2 |
| Bbox precision/recall | Compare to annotated boxes (IoU > 0.5 = match) | Phase 2 |
| Cluster count error | `\|detected - manual\|` | Phase 2 |
| CIP recall | `found NVs / labeled NVs` | Phase 2, 4 |
| CIP false positives | `spurious detections / scan` | Phase 2, 4 |
| POI registration rate | `accepted / candidates` | Phase 4 |
| Zoom center error | `\|scan_center - bbox_centroid\|` | Phase 4 |
| ODMR true positive rate | confirmed / tested | Phase 5 |

---

## Immediate Actions (No New Hardware Required)

These can start now with existing + synthetic data:

| # | Action |
|---|--------|
| 1 | Copy `Confocal/` → `tests/fixtures/confocal/coarse/legacy_20260615/` |
| 2 | Manually annotate 4 existing coarse scans (cell mask + cluster bboxes) |
| 3 | Add synthetic cell+cluster generator for segmentation unit tests |
| 4 | Extend `run_segmentation_test.py` to batch all coarse fixtures |
| 5 | Document in `tests/fixtures/confocal/README.md` what each file is |

---

## Summary: Data Gap vs Phase

| Phase | Can proceed with current 200 µm data only? | Additional data required |
|-------|-------------------------------------------|--------------------------|
| 1 Foundation | ✅ Yes | Organize fixtures folder |
| 2 Algo optimization | ⚠️ Partial | Annotations + synthetic cells |
| 3 Live integration | ✅ Yes (replay mode) | Live scan optional |
| 4 Multi-scale loop | ❌ No | Paired fine scans + POI labels |
| 5 Validation | ❌ No | ODMR-labeled POIs |
| 6 Production | ❌ No | Full benchmark session |

**Bottom line:** Current data is **incomplete** for end-to-end automation validation. It is sufficient to unify code (Phase 1) and begin segmentation tuning (Phase 2) once manual annotations exist. **Fine-scale paired scans are mandatory before Phase 4.**
