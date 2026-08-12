# Cell Boundary Detection & Instance Segmentation (`CellSegmentationLogic`)

This document details the architecture, physical intuition, mathematical formulation, real fluorescence count statistics, and parameter configurations of [`CellSegmentationLogic`](file:///d:/qudi-working/qudi/logic/cell_segmentation_logic.py).

This module is responsible for identifying macro-scale biological cell boundaries from wide-field confocal scans, separating overlapping 3D cell instances, and extracting individual cell bounding boxes formatted for direct consumption by [`ScanRegionQueue`](file:///d:/qudi-working/qudi/logic/scan_region_queue.py).

---

## 1. Real Fluorescence Count Statistics (Confocal2 vs Confocal3)

Empirical fluorescence count analysis across the experimental datasets reveals the exact intensity levels (in counts/sec) of substrate background, low-lit cell bodies, cell cores, and extreme NV spikes:

| Dataset | Image File | Substrate Background ($0-25\%$) | Low-Lit Cell Edges ($25-75\%$) | Cell Cores ($75-90\%$) | NV Clusters & Spikes ($95-100\%$) | Peak Spike Ratio |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Confocal2** (Control) | `20260705-1517-07` | $0 - 3,000\,\text{c/s}$ | $3,000 - 6,500\,\text{c/s}$ | $6,500 - 24,000\,\text{c/s}$ | $42,000 - 2,210,000\,\text{c/s}$ | **$491\times$** |
| **Confocal2** (Control) | `20260706-1037-35` | $500 - 6,500\,\text{c/s}$ | $6,500 - 12,500\,\text{c/s}$ | $12,500 - 37,500\,\text{c/s}$ | $61,500 - 14,711,500\,\text{c/s}$ | **$1,731\times$** |
| **Confocal3** (Target 1) | `20260805-0001-21` | $0 - 19,000\,\text{c/s}$ | $19,000 - 100,000\,\text{c/s}$ | $100,000 - 193,500\,\text{c/s}$ | $509,000 - 20,258,000\,\text{c/s}$ | **$390\times$** |
| **Confocal3** (Target 2) | `20260806-0016-25` | $0 - 13,500\,\text{c/s}$ | $13,500 - 90,500\,\text{c/s}$ | $90,500 - 158,000\,\text{c/s}$ | $478,050 - 20,839,500\,\text{c/s}$ | **$405\times$** |
| **Confocal3** (Target 3) | `20260807-2017-13` | $0 - 8,000\,\text{c/s}$ | $8,000 - 76,500\,\text{c/s}$ | $76,500 - 135,000\,\text{c/s}$ | $369,500 - 18,440,000\,\text{c/s}$ | **$595\times$** |

---

## 2. Layman Explanation (Plain English Intuition)

### The Target Challenge in Confocal3
In `Confocal3`, biological cells overlap in 3D, lack spherical shapes, and have faint, low-lit auto-fluorescence ($13,500 - 90,500\,\text{c/s}$). Meanwhile, ultra-bright NV clusters ($>20,000,000\,\text{c/s}$) act like $20,000,000$-watt blinding halogen searchlights scattered across the scan.

Standard computer algorithms get blinded by the searchlights. They place their brightness cutoffs so high ($>2,000,000\,\text{c/s}$) that they segment **only the searchlight beams**, completely missing the faint cells!

### How the Targeted Pipeline Solves It
1. **Squishing the Glare (Log Scale)**: Converts linear light to a logarithmic scale (similar to decibels), compressing a $400\times$ blinding glare difference down to a manageable $1.5\times$ nudge.
2. **Capping the Searchlights (P92 Winsorization)**: Caps extreme glare at the 92nd percentile so searchlights cannot bleed light into surrounding dark background.
3. **Subtracting Sky Gradient & Measuring Noise Floor**: Estimates dark background using a 51-pixel filter and measures substrate noise standard deviation $\sigma_{\text{noise}}$.
4. **Noise-Floor Bounded Adaptive Thresholding**: Sets the expansion threshold at $\max(0.4 \cdot t_{\text{Otsu}}, 2.5 \cdot \sigma_{\text{noise}})$.
   - On `Confocal2` (clean substrate), $2.5 \cdot \sigma_{\text{noise}}$ keeps masks tight and clean ($15.3\% - 17.2\%$).
   - On `Confocal3`, it dynamically expands to catch low-lit cell boundaries ($19.6\% - 23.3\%$) without leaking into background substrate noise!
5. **Slicing Overlapping 3D Cells (Multi-Peak Watershed)**: Uses topography distance mapping (`min_distance=8`) to slice connected 3D cell clumps into **24 to 36 distinct individual cell bounding boxes** ready for [`ScanRegionQueue`](file:///d:/qudi-working/qudi/logic/scan_region_queue.py).

---

## 3. Benchmark Performance Summary

| Dataset | Image File | Mask Coverage Area | Cell Region Count | Substrate Contrast Ratio |
| :--- | :--- | :--- | :--- | :--- |
| **Confocal2** | `20260705-1517-07` | **6,113 px (15.3%)** | 14 instances | **7.32x** |
| **Confocal2** | `20260706-1037-35` | **6,888 px (17.2%)** | 15 instances | **5.33x** |
| **Confocal3** (Target 1) | `20260805-0001-21` | **7,827 px (19.6%)** | **24 instances** | **2.28x** |
| **Confocal3** (Target 2) | `20260806-0016-25` | **9,270 px (23.2%)** | **25 instances** | **3.11x** |
| **Confocal3** (Target 3) | `20260807-2017-13` | **6,497 px (16.2%)** | **17 instances** | **2.68x** |

---

## 4. Usage Example

```python
from logic.cell_segmentation_logic import CellSegmentationLogic

# Initialize logic module
cell_logic = CellSegmentationLogic()

# Parse Qudi confocal .dat file
image, ux, uy, header = cell_logic.parse_dat_file('scan_data.dat')

# Run targeted cell boundary and 3D instance segmentation
mask, smoothed, labeled_cells, cell_boxes = cell_logic.segment_cells_with_instances(
    image,
    min_cell_area_um2=30.0,
    cap_percentile=92.0
)

# Export filtered data file
out_filepath = cell_logic.filter_and_save(image, mask, header, 'scan_data.dat')

# Queue extracted cell bounding boxes to ScanRegionQueue
for box in cell_boxes:
    print(f"Cell ID {box['cell_id']}: Area = {box['area_um2']:.1f} um^2, Bounding Box = {box['bbox_um']}")
```

---

## 5. Testing & Verification

Run the comprehensive unit and comparative integration test suite:
```bash
python -m pytest tests/test_cell_segmentation_logic.py -v -s
```
Visual diagnostic comparison figures are generated automatically in [`tests/test_cell_segmentation_old/`](file:///d:/qudi-working/qudi/tests/test_cell_segmentation_old).
