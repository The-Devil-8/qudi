# 25 — Algorithmic Intuitions & Future Upgrades

> **Living Document**
> This document captures brainstormed intuitions, identified gaps, and proposed algorithmic upgrades for the Qudi automation pipeline. It specifically addresses issues observed in dense cell populations and the bridging gap between macro-level segmentation and micro-level POI extraction.

---

## 1. The Macro-Micro Gap: Cell Truncation

### 1.1 The Problem
Observations in the current automation loop reveal a "gap" between the `ROISegmentationLogic` and the resulting micro-scans. The bounding boxes generated from the macro scan often truncate cells or group multiple cells improperly. 
- **Cause**: The current pipeline relies on global Otsu thresholding combined with basic morphological operations (opening/closing). In densely populated cell samples, cells touch or overlap. A simple threshold cannot distinguish between one massive blob of touching cells and individual cells.
- **Consequence**: When the `ScanRegionQueue` draws a bounding box around what it thinks is one cell (but is actually a piece of a cell or merged cells), the subsequent high-resolution micro-scan focuses on a structurally compromised area. This leads to inefficient scanning, truncated processable zones, and missed NV candidates.

### 1.2 The Solution: Watershed Segmentation/ Related algos
To properly separate touching cells in dense environments, we need to transition from simple morphological thresholding to **Watershed Segmentation**.

**How it works (The Intuition):**
1. **Binary Mask Generation**: Use a global threshold (like Otsu) to separate the foreground (all cells) from the background.
2. **Distance Transform**: Compute the Euclidean Distance Transform (EDT) on the binary mask. This assigns every foreground pixel a value equal to its distance from the nearest background pixel. The "centers" of the cells will have the highest values (peaks).
3. **Marker Generation**: Find the local maxima in the EDT. These peaks act as definite "seeds" or markers for individual cells.
4. **Watershed Flooding**: Invert the EDT (or use the gradient magnitude of the original image) and apply the Watershed algorithm using the identified markers. The algorithm "floods" from the markers, and where the flooded basins meet, it builds a dam (boundary).

**Why it fixes the gap**:
The Watershed algorithm explicitly separates touching objects based on their geometric centers. This ensures that the generated bounding boxes encapsulate whole, individual cells perfectly, even when they are tightly packed. The micro-scan will then receive an accurate, un-truncated cell body to process.

---

## 2. Revamping `ScanRegionQueue` Priority

### 2.1 The Problem
Currently, the `ScanRegionQueue` prioritizes regions based on simple metrics like total intensity or bounding box area. This is highly susceptible to false positives:
- A massive, irregularly shaped cluster of debris might have high intensity and area, pushing it to the top of the queue.
- Scanning these poorly structured regions wastes valuable confocal time and yields no valid NVs.

### 2.2 The Solution: Structure-Based Prioritization
We must revamp the queue prioritization to evaluate the **morphological structure** and **suitability** of the cell, rather than just its brightness. This should be implemented at two distinct levels: Macro and Micro.

#### Level A: Macro-Scan Prioritization (Queueing)
After `ROISegmentationLogic` identifies the bounding boxes, we score them using shape descriptors:
1. **Solidity**: $Area / Convex\_Hull\_Area$. A healthy cell is generally convex. Deeply indented or highly irregular shapes (solidity < 0.7) are likely debris or merged clusters and should be penalized.
2. **Circularity**: $4\pi \times (Area / Perimeter^2)$. Filters out long, string-like artifacts.
3. **Processable Area Ratio**: Evaluate how much of the cell's mask is composed of bright macro-clusters. If a bounding box is 80% saturated cluster, it is functionally useless.
*Implementation*: Update `prioritize_queue(method='structure')` to rank regions using a weighted sum of these geometric properties.

#### Level B: Micro-Scan Rejection (Execution)
Even with good macro-prioritization, the high-res micro-scan reveals the true structure.
- **The Intuition**: After the `CellRegionProcessor` evaluates the high-res image, we dynamically check the true `processable_mask` (Cytoplasm minus Nucleus minus Clusters).
- **Action**: If the processable area is drastically small relative to the cell's bounding box (e.g., < 15%), or if the structure is completely fragmented into tiny islands, we should immediately abort the POI extraction step for that cell.
- **Result**: Discarding bad cells early saves the time otherwise spent running the expensive CIP algorithm and verification attempts on garbage data.

---

## 3. Next Implementation Steps

1. **Implement Watershed**: Replace or augment the `compute_component_properties` and labeling steps in `ROISegmentationLogic` with `scipy.ndimage.distance_transform_edt` and `skimage.segmentation.watershed`.
2. **Add Shape Descriptors**: Update the `ScanRegionQueue` region data structure to store `solidity` and `circularity`.
3. **Micro-Level Bailout**: Add a conditional check in `multi_scale_auto_nv_finder_logic.py` directly after `_cell_processor.process(image)`. If `cell_result.processable_area_ratio < threshold`, log a skip message and proceed to `_process_next_region()`.
