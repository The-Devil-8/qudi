# Region of Interest (ROI) vs. Overly Bright Clusters

This document describes the algorithms behind the `ROISegmentationLogic` module, detailing how to explicitly isolate a cell's mid-intensity Region of Interest (ROI) while rejecting overly bright clusters.

## Background: ROI vs. Bright Clusters

In advanced confocal analysis of biological cells at wide scan areas (e.g., 25 microns and above), we often encounter extremely bright spots. These are mostly large clusters, not individual NV centers (Points of Interest, or POIs). Individual POIs will be found later when zooming in closer (e.g., 5 to 1 micron resolution). 

Therefore, at this wide-scan stage, we must distinguish between two conceptual areas:
1. **Region of Interest (ROI)**: The continuous, macro-structural area containing the cell body. It is characterized by low-to-moderate intensity, slowly-varying auto-fluorescence. We extract this mid-intensity "middle ground" area to analyze cellular background without bias, leaving room for future high-resolution POI analysis.
2. **Bright Clusters**: The extremely bright, localized intensity spikes. Because these clusters can be orders of magnitude brighter than the cell body, including them in the ROI data heavily skews background intensity analysis.

The `ROISegmentationLogic` module physically separates the two.

## Pipeline

The module employs a statistical thresholding subtraction to "punch holes" in the cell body wherever an overly bright cluster exists.

### 1. Cell Boundary Detection
The algorithm first identifies the macro-structure of the entire cell using the standard `CellSegmentationLogic` pipeline:
- **Despiking**: A Median Filter erases the high-intensity spikes.
- **Smoothing**: A Gaussian Filter blurs the cell body.
- **Thresholding**: Otsu's method (or percentile threshold) isolates the cell boundary from the dark exterior.

This gives us the `cell_mask`.

### 2. High-Frequency Spike Isolation
We isolate the bright cluster candidates by subtracting the despiked image from the original, raw fluorescence image:
`spikes = raw_image - despiked_image`

The resulting `spikes` image contains the high-frequency intensity data, removing the slowly-varying cell auto-fluorescence.

### 3. Statistical Cluster Thresholding
We calculate the Median Absolute Deviation (MAD) of the `spikes` *strictly within the `cell_mask`* to determine the inherent noise variance.

We define a robust, outlier-resistant dynamic threshold for a bright cluster:
`Cluster Threshold = Median(Cell Spikes) + 10 * Sigma(Cell Spikes)`

Any pixel in the `spikes` image that exceeds this threshold is flagged in the `bright_cluster_mask`.

### 4. ROI Extraction
The final Region of Interest is defined mathematically as:
`ROI Mask = cell_mask AND (NOT bright_cluster_mask)`

This ensures the ROI strictly contains the mid-intensity cell body background, explicitly excluding both the dark exterior and the overly bright clusters inside the cell. 

The filtered data is then exported to `_roi_filtered.dat`, where all pixels outside the `ROI Mask` are set to `0.0`.
