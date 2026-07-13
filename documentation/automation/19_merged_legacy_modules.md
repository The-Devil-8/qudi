# 19 — Merged Modules from Docs Branch (Status & Disposition)

During the merge of the `docs` branch into the `automation` branch, several automation modules were introduced that diverge from our current multi-scale "zoom-in" architecture (which utilizes `ScanRegionQueue` and `CellRegionProcessor`). 

These pulled modules were built primarily for **single-scan NV finding** (i.e., finding NVs within an *already acquired* confocal scan), rather than fully autonomous multi-scale navigation (scanning a 200x200 area, finding cells, zooming into cells, and finding NVs). 

This document tracks these merged modules, explains what they do, and outlines our strategy for keeping, removing, or repurposing them.

## 1. Single-Scan Auto NV Finder (`logic/auto_nv_finder_logic.py`)
* **What it does:** Analyzes the current confocal image data to detect bright spots (using Color Image Processing - CIP), iteratively optimizes each spot's position, and registers successful ones to the `PoiManager`.
* **Alignment with Current Dev:** It lacks multi-scale zooming and cell region filtering.
* **Disposition:** **Use as a Sub-routine**. We will not remove it. Instead, our planned `AutomatedNVFinder` Master Task will handle the 200x200 parent scan, navigate to each cell (using `ScanRegionQueue`), apply the cytoplasm mask (using `CellRegionProcessor`), and then pass the masked image to this module to perform the final peak detection and optimization.

## 2. Auto NV Finder Task (`logic/tasks/auto_nv_find.py`)
* **What it does:** A `TaskRunner` wrapper around `auto_nv_finder_logic.py` allowing it to be scheduled and executed automatically.
* **Alignment with Current Dev:** This only automates the single-scan pipeline. 
* **Disposition:** **Replace/Extend**. We will build a new Master Task (e.g., `logic/tasks/automated_nv_finder.py`) that orchestrates the entire multi-scale process. This file can be kept as an optional "Single-Scan Finder" task, but it will not be the primary entry point.

## 3. Auto NV Finder GUI (`gui/poimanager/auto_nv_finder_widget.py`)
* **What it does:** A dock widget in the POI Manager containing controls (Start/Stop, sliders) and an overlay that draws color-coded circles on the confocal image to show candidate statuses.
* **Alignment with Current Dev:** Designed for single-scan monitoring.
* **Disposition:** **Keep & Extend**. The UI is highly valuable for visualizing the final NV detection step. We will likely extend this GUI or create a parent GUI tab to also visualize the cell queueing and multi-scale zooming process.

## 4. Cell Segmentation Logic (`logic/cell_segmentation_logic.py`)
* **What it does:** An earlier, simpler attempt at cell boundary detection from the `docs` branch.
* **Alignment with Current Dev:** **Obsolete**. Our `automation` branch already contains a much more robust pipeline (`ROISegmentationLogic` and `CellRegionProcessor`) that handles despiking, multi-scale smoothing, nucleus masking, and bright cluster exclusion.
* **Disposition:** **Remove / Ignore**. It is out of track with our current development and is superseded by `CellRegionProcessor`.

## 5. Image Analysis / CIP Utilities (`logic/image_analysis.py`)
* **What it does:** Contains the mathematical utilities for CIP detection (e.g., background subtraction, local maxima finding, shape filtering) used by `auto_nv_finder_logic.py`.
* **Alignment with Current Dev:** It is actively used by the single-scan logic.
* **Disposition:** **Keep**. It provides the core math for the final NV peak detection step.

## 6. Image Rebuild Logic (`logic/image_rebuild_logic.py`)
* **What it does:** A testing utility that converts Qudi `.dat` files into visual `.png` images using matplotlib.
* **Alignment with Current Dev:** Purely a testing tool.
* **Disposition:** **Keep**. Useful for generating diagnostics and visual test outputs.
