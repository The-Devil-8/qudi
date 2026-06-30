# 17 — Algorithm Optimization Guide

> **Document 17 of the Automation Series**  
> Detailed optimization plan for cell boundary detection, ROI / bright-cluster rejection, bounding-box extraction, and CIP NV detection. Works offline first (Phase 2 of [doc 15](15_phased_implementation_plan.md)).

**Related:** [16 — Testing Data](16_testing_data_requirements.md) for annotated datasets needed to measure improvements.

---

## Overview

Four algorithm layers must be tuned for the multi-scale pipeline:

```
Layer 1: Cell boundary     → CellSegmentationLogic
Layer 2: Bright clusters   → ROISegmentationLogic (spike MAD threshold)
Layer 3: ROI mask          → cell_mask AND NOT bright_cluster_mask
Layer 4: NV CIP detection  → ConfocalImageAnalysis + AutoNVFinderLogic
Layer 5: Bbox extraction   → NEW (connected components on bright_cluster_mask)
```

Layers 1–3 operate at **coarse FOV** (~200 µm). Layer 4 operates at **fine FOV** (≤30 µm). Layer 5 bridges coarse → zoom loop.

---

## Layer 1 — Cell Boundary (`CellSegmentationLogic`)

### Current pipeline

```python
despiked = median_filter(fluor, size=7)
smoothed = gaussian_filter(despiked, sigma=5)
thresh = threshold_otsu(smoothed)  # or percentile 70
mask = smoothed > thresh
mask = binary_closing(mask, iterations=3)
mask = binary_fill_holes(mask)
mask = binary_opening(mask, iterations=2)
```

### Known limitations

| Issue | Cause | Symptom |
|-------|-------|---------|
| Cell larger than truth | NV spikes smear despite median filter | Boundary expands toward bright clusters |
| Cell smaller than truth | Otsu threshold too high | Interior holes, shrunken mask |
| Multiple blobs | Separate auto-fluorescence regions | Wrong region queue count |
| Edge cell clipped | Cell extends beyond FOV | Partial mask — may be OK if handled |
| Weak cell invisible | Auto-fluorescence near background | Empty mask — needs fallback |

### Parameters to expose (StatusVar)

| Parameter | Current | Search range | Notes |
|-----------|---------|--------------|-------|
| `median_kernel_size` | 7 | 3, 5, 7, 9, 11 | Must be odd; larger removes wider spikes |
| `gaussian_sigma` | 5 | 2, 3, 5, 7, 10 | Larger = smoother cell blob |
| `threshold_method` | `otsu` | `otsu`, `percentile`, `yen` | Fallback if Otsu fails on bimodal data |
| `threshold_percentile` | 70 | 50–85 | Used when Otsu unavailable or fails |
| `closing_iterations` | 3 | 1–5 | Fills gaps in cell outline |
| `opening_iterations` | 2 | 1–4 | Removes salt noise outside cell |
| `min_cell_area_px` | *(none)* | 500–5000 | Reject tiny false blobs at 200×200 |

### Optimization steps

1. **Visual audit** — Run batch script on all coarse fixtures; save overlay PNGs.
2. **Manual masks** — Create ground-truth cell masks (doc 16 Category C).
3. **Grid search** — Sweep median × sigma × threshold method; score IoU vs manual mask.
4. **Select Pareto optimum** — Max IoU with minimal boundary overshoot (penalize mask area > 120% of manual).
5. **Edge cases** — Tune separate preset `weak_cell` with lower percentile threshold.
6. **Lock defaults** — Update `CellSegmentationLogic` and config docs.

### Proposed improvements (code changes)

| Improvement | Priority | Description |
|-------------|----------|-------------|
| Adaptive sigma from FOV | High | `sigma ≈ cell_expected_diameter_px / 10` |
| Keep largest connected component | High | If multiple blobs, keep largest only (configurable) |
| Rolling-ball background | Medium | Alternative to median for uneven diamond background |
| Convex hull smoothing | Low | Regularize jagged boundaries |

### Success metrics

| Metric | Target (set after first annotations) |
|--------|----------------------------------------|
| Mean IoU vs manual mask | ≥ 0.85 |
| False background inclusion | < 5% of image area outside manual cell |
| Failure rate (empty mask on visible cell) | 0 on annotated set |

---

## Layer 2 — Bright Cluster Detection (`ROISegmentationLogic`)

### Current pipeline

```python
spikes = fluor - despiked   # same despiked as cell pipeline
spikes_in_cell = spikes[cell_mask]
cluster_thresh = median(spikes_in_cell) + 10 * MAD_sigma
bright_cluster_mask = spikes > cluster_thresh
```

### Known limitations

| Issue | Cause | Symptom |
|-------|-------|---------|
| Missed clusters | 10σ too aggressive | Bright NV groups not flagged for zoom |
| Over-segmentation | Threshold too low | Whole cell interior flagged as cluster |
| Single-pixel spikes | No area filter | Noise pixels become cluster seeds |
| Cluster merged with cell edge | Spike extends to boundary | Bbox covers entire cell |

### Parameters to expose

| Parameter | Current | Search range |
|-----------|---------|--------------|
| `cluster_mad_multiplier` | 10.0 | 5, 7, 10, 12, 15 |
| `min_cluster_area_px` | *(none)* | 4, 9, 16, 25 |
| `max_cluster_area_px` | *(none)* | optional cap to reject whole-cell false positive |
| `use_spikes_in_cell_only` | True | Restrict stats to cell_mask |
| `merge_cluster_distance_px` | *(none)* | Dilate + merge within N pixels |

### Optimization steps

1. Plot spike histogram inside `cell_mask` for each fixture — verify bimodality (background vs clusters).
2. Sweep MAD multiplier; count connected components vs manual cluster count.
3. Apply `min_cluster_area_px` to drop isolated pixels.
4. Compare bbox IoU to manually drawn cluster boxes (doc 16).
5. Test on weak-cell fixtures — may need lower multiplier (7σ).

### Proposed improvements

| Improvement | Priority | Description |
|-------------|----------|-------------|
| Connected components on `bright_cluster_mask` | **Required** | Foundation for bbox extraction |
| Per-cluster peak intensity ranking | High | Sort zoom queue by brightness |
| Morphological dilate before CC | Medium | Merge fragmented cluster pixels |
| Alternative: top-N peaks in cell | Medium | Fallback if MAD fails — sliding window max |

### Success metrics

| Metric | Target |
|--------|--------|
| Cluster count vs manual | ±1 per scan |
| Bbox IoU vs manual box | ≥ 0.7 per cluster |
| False cluster rate on empty diamond | 0 |

---

## Layer 3 — ROI Mask (Mid-Intensity Region)

### Definition

```
roi_mask = cell_mask AND (NOT bright_cluster_mask)
```

### Purpose

- Analysis of cell auto-fluorescence without cluster bias
- **Not** used directly for NV POI detection at coarse scale
- Optional: restrict fine-scan placement to ROI interior (exclude cluster cores from background stats)

### Optimization steps

1. Verify ROI visually — mid-intensity ring around clusters, not empty.
2. If ROI too thin (clusters eat cell): reduce MAD multiplier or shrink cluster mask via erosion.
3. Export `_roi_filtered.dat` and compare intensity histogram to raw cell interior.

### Parameters

| Parameter | Description |
|-----------|-------------|
| `cluster_mask_erosion_px` | Shrink bright mask before subtracting — widens ROI band |
| `require_roi_for_fine_scan_center` | Bool — centroid must fall in ROI not cluster |

---

## Layer 4 — Bounding Box Extraction (NEW)

Not yet in codebase — required for zoom loop.

### Recommended algorithm

```python
from scipy.ndimage import label

labeled, n_components = label(bright_cluster_mask)
for each component i:
    if area >= min_cluster_area_px:
        rows, cols = np.where(labeled == i)
        bbox_px = (row_min, row_max, col_min, col_max)
        bbox_m = pixel_indices_to_meters(bbox_px, x_coords, y_coords)
        add margin_fraction (e.g. 10%)
```

### Optional: DBSCAN fallback

Use only if connected components merge distinct clusters:

```python
points = argwhere(spikes > cluster_thresh AND cell_mask)
labels = DBSCAN(eps=eps_um/pixel_size, min_samples=5).fit(points)
```

| Parameter | Search range |
|-----------|--------------|
| `bbox_margin_fraction` | 0.05 – 0.20 |
| `dbscan_eps_px` | 3 – 15 |
| `dbscan_min_samples` | 3 – 10 |

### Optimization steps

1. Implement both CC and DBSCAN paths behind config flag.
2. Compare on annotated fixtures; pick simpler method that wins on recall.
3. Sort bboxes by peak intensity inside each box (zoom brightest first).

---

## Layer 5 — CIP NV Detection (`ConfocalImageAnalysis`)

### Current pipeline (9 stages)

1. Background estimation (median filter, kernel 15)
2. Background subtraction
3. Intensity normalization (percentile)
4. MAD noise estimate
5. Threshold: `max(sigma * detection_threshold_sigma, min_spot_intensity)`
6. Local maxima (neighborhood = spot diameter in px)
7. Shape validation (circularity)
8. Distance clustering (greedy, not DBSCAN)
9. Sub-pixel refinement (center-of-mass — **should upgrade to FitLogic 2D Gaussian**)

### FOV-dependent presets (proposed)

| Scan type | `spot_diameter` | `detection_threshold_sigma` | `background_filter_size` | `min_spot_intensity` |
|-----------|-----------------|----------------------------|--------------------------|----------------------|
| Coarse 200 µm / 200 px | N/A — **do not detect POIs** | — | — | — |
| Fine 30 µm / 200 px | 1.5 µm | 5.0 | 11 | 1000 c/s |
| Fine 10 µm / 200 px | 1.2 µm | 4.0 | 9 | 800 c/s |
| Fine 5 µm / 200 px | 0.8 µm | 4.0 | 7 | 500 c/s |

### Known gaps in current implementation

| Gap | Fix |
|-----|-----|
| `min_optimization_quality` ignored | Read R² from optimizer; reject if below threshold |
| `fitlogic` connected but unused | Call `make_twoDgaussian_fit` in `refine_position_gaussian_2d` |
| Coarse scan POI registration | Block when `enable_multi_scale` or pixel size > 0.5 µm |
| Center-of-mass refinement | Upgrade to proper 2D Gaussian fit |

### Optimization steps (fine-scale data required)

1. **Synthetic baseline** — Extend `test_auto_nv_finder.py` with fine-pixel images (0.05 µm/px).
2. **Recall/precision sweep** — Grid search sigma × spot_diameter on labeled NV positions.
3. **Fit upgrade** — Compare COM vs Gaussian fit position error vs manual optimizer.
4. **False positive audit** — Run on fine scans with labeled negatives (dust).
5. **Lock presets** — Store as config profiles `cip_fine_30um`, `cip_fine_5um`.

### Cluster vs single-NV discrimination at fine scale

Even at fine FOV, unresolved clusters may appear as one broad peak:

| Check | Action |
|-------|------|
| Fitted sigma > `spot_diameter` | Flag as `cluster`; optionally sub-zoom |
| Multiple maxima within spot neighborhood | Split candidates |
| Low circularity | Reject or defer to ODMR |

---

## Cross-Layer Optimization Workflow

Run this sequence when new data arrives:

```
1. Load coarse .dat
2. Tune Layer 1 (cell) → IoU vs manual mask
3. Tune Layer 2 (clusters) → bbox count/recall
4. Verify Layer 3 (ROI) visually
5. Extract Layer 4 bboxes → compare to manual
6. Load paired fine .dat
7. Tune Layer 5 CIP → NV recall/precision
8. Commit golden outputs + default StatusVars
```

### Batch evaluation script (to implement)

```
tools/evaluate_segmentation.py \
  --fixtures tests/fixtures/confocal/coarse/ \
  --annotations tests/fixtures/confocal/annotations/ \
  --output reports/segmentation_eval/
```

Outputs:
- CSV: per-scan IoU, cluster count error, parameter set used
- PNG: overlay gallery
- JSON: recommended defaults

---

## Parameter Dependency Map

```
median_kernel_size ──┬──► despiked image ──► cell_mask
                     │
                     └──► spikes image ──► bright_cluster_mask
                                              │
gaussian_sigma ──────► smoothed ──► cell_mask │
                                              │
cluster_mad_multiplier ───────────────────────┘
                                              │
                                              ▼
                                         bbox list ──► zoom FOV
                                              │
fine FOV + resolution ────────────────────────┼──► CIP parameters
                                              ▼
                                         NV candidates ──► optimizer
```

Changing `median_kernel_size` affects **both** cell and cluster layers — tune jointly, not independently.

---

## Duplicate Code to Eliminate (Phase 1)

| Duplication | Location | Resolution |
|-------------|----------|------------|
| `parse_dat_file()` | `cell_segmentation_logic.py`, `roi_segmentation_logic.py` | Shared `confocal_image_utils` |
| Despike + smooth | Cell and ROI both recompute | ROI should accept precomputed `despiked` from cell stage |
| Threshold/morphology constants | Hardcoded magic numbers | Single config object |

**Refactored call chain:**

```python
despiked, smoothed = preprocess(fluor, params)
cell_mask = segment_cell(smoothed, params)
bright_cluster_mask = detect_clusters(fluor, despiked, cell_mask, params)
roi_mask = cell_mask & ~bright_cluster_mask
bboxes = extract_bboxes(bright_cluster_mask, coords, params)
```

---

## Review Checklist Before Live Deployment

### Cell boundary
- [ ] IoU ≥ target on all annotated coarse scans
- [ ] Largest-component rule tested on multi-blob failure cases
- [ ] Empty mask triggers operator warning, not silent zoom

### Bright clusters
- [ ] Cluster count within ±1 of manual on annotated set
- [ ] `min_cluster_area_px` removes salt noise
- [ ] Bbox margins do not exceed scanner range

### CIP (fine scale only)
- [ ] NV recall ≥ target on labeled fine scans
- [ ] Gaussian fit refinement improves position vs COM
- [ ] R² gate wired and tested
- [ ] Coarse-scan POI registration disabled in multi-scale mode

### Integration
- [ ] Same results from `.dat` offline and live `xy_image`
- [ ] Parameter changes persist via StatusVar
- [ ] Golden regression tests pass in CI

---

## Summary

| Layer | Module | Optimize with | Key output |
|-------|--------|---------------|------------|
| Cell boundary | `CellSegmentationLogic` | Annotated cell masks | `cell_mask` |
| Bright clusters | `ROISegmentationLogic` | Annotated cluster bboxes | `bright_cluster_mask` |
| ROI | Derived | Visual + histogram | `roi_mask` |
| Bboxes | **New** | Cluster bbox JSON | Zoom queue |
| CIP | `ConfocalImageAnalysis` | Fine scans + NV labels | POI candidates |

**Current 200 µm data:** sufficient to start Layers 1–4 tuning **after manual annotation**. Layer 5 requires **fine-scale paired data** (doc 16 Category B).
