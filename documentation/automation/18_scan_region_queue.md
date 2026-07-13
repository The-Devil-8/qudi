# 18 — ScanRegionQueue: ROI Queue Management

> **Document 18 of the Automation Series**  
> Module for managing a priority queue of cell regions extracted from ROI
> segmentation, bridging wide-field scanning to close-scan acquisition.

**Related documents:**
- [10 — ROI Segmentation](10_roi_segmentation.md) — upstream pipeline
- [14 — Roadmap & Status](14_automation_roadmap_and_status.md) — project overview
- [15 — Phased Plan](15_phased_implementation_plan.md) — Phase 4: multi-scale zoom loop

---

## Purpose

The `ScanRegionQueue` sits between ROI segmentation (which identifies
cell-like regions in 200×200 µm scans) and the scanner (which executes
close scans). It:

1. Extracts bounding boxes from segmentation masks.
2. Separates coupled / touching cells via watershed.
3. Filters false positives using asymmetric dimension rules.
4. Manages a priority queue of regions for close scanning.
5. Computes scanner FOV parameters for each region.
6. Stores cropped ROI thumbnail images for GUI display.
7. Tracks each region's state through the scanning pipeline.

---

## Location

`logic/scan_region_queue.py`

---

## Key Classes

### `ScanRegion`

A single region extracted from segmentation, with:

| Attribute | Type | Description |
|-----------|------|-------------|
| `region_id` | str | Unique ID (e.g. `R-a1b2c3`) |
| `bbox_physical` | tuple | `(x_min, x_max, y_min, y_max)` in metres |
| `bbox_pixels` | tuple | `(row_min, row_max, col_min, col_max)` |
| `width_um`, `height_um` | float | Physical dimensions |
| `centroid_physical` | tuple | `(x, y)` in metres |
| `peak_intensity` | float | Max fluorescence in region |
| `mean_intensity` | float | Average fluorescence |
| `status` | str | `queued` / `scanning` / `processed` / `skipped` / `failed` |
| `priority` | float | Higher = scan first |
| `cropped_image` | ndarray | Thumbnail from parent scan |

### `ScanRegionQueue`

Priority queue manager. Key methods:

| Method | Description |
|--------|-------------|
| `extract_regions_from_segmentation()` | Parse ROI mask → bounding boxes |
| `filter_false_positives()` | Asymmetric dimension filter |
| `prioritize_queue()` | Sort by intensity×√area or other metric |
| `get_next_region()` | Pop highest-priority queued region |
| `compute_scan_parameters()` | FOV, resolution, pixel size for scanner |
| `mark_region_status()` | Update state after scanning/processing |
| `to_json()` / `from_json()` | Serialization |

---

## Filtering Rules

**Asymmetric dimension rule:**
- Longer axis ≥ 20 µm
- Shorter axis ≥ 10 µm  
- Area ≥ 200 µm²
- Area ≤ 5000 µm²

This allows elongated cells (e.g. 29×14 µm) while rejecting
small noise fragments (3×5 µm).

---

## Touching Cell Separation

When cells are coupled or boundaries touch, they may form a
single connected component in the ROI mask. The watershed-based
separation detects multiple peaks in the distance transform
and splits them into independent regions.

**Parameters:**
- `split_threshold_um = 50.0` — components wider than this are checked
- `min_cell_diameter_um = 15.0` — minimum separation distance

---

## Data Flow

```
ROISegmentationLogic.segment_roi()
        │
        ▼  segmentation_result['roi_mask']
ScanRegionQueue.extract_regions_from_segmentation()
        │
        ├── _separate_touching_cells()  (watershed)
        │
        ▼
ScanRegionQueue.filter_false_positives()
        │
        ▼
ScanRegionQueue.prioritize_queue()
        │
        ▼
for each region:
    params = queue.compute_scan_parameters(region)
    → ConfocalLogic: set image_x_range, image_y_range
    → ConfocalLogic: start_scanning()
    → queue.mark_region_status(region_id, 'processed')
```

---

## Test Results (Confocal2 Data)

| Parent Scan | Raw Regions | Accepted | 
|-------------|-------------|----------|
| 20260705-1517-07 | 16 | 8 |
| 20260706-1037-35 | 32 | 4 |
| 20260706-1218-34 | 32 | 4 |
| 20260706-1733-10 | 18 | 7 |
| 20260706-2212-44 | 17 | 9 |

All tests pass. See `tests/test_scan_region_queue.py`.

---

## Next Step: CellRegionProcessor

The `CellRegionProcessor` (not yet implemented) will process close-scan
images by:
1. Detecting and masking the dark nucleus region
2. Masking overly bright NV clusters
3. Extracting the processable cytoplasm zone
4. Handing that zone to the existing CIP + Optimizer pipeline for NV detection

**It does NOT do NV detection** — that stays in the existing
`ConfocalImageAnalysis` + `OptimizerLogic` + `FitLogic` pipeline.
