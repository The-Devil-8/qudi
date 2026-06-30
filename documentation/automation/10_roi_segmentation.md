# Region of Interest (ROI) vs. Overly Bright Clusters

This document describes the algorithms behind the `ROISegmentationLogic` module, detailing how to explicitly isolate a cell's mid-intensity Region of Interest (ROI) while rejecting overly bright clusters and dark substrate background on wide-field confocal scans (e.g. 200×200 µm).

## Background: ROI vs. Bright Clusters

In advanced confocal analysis of biological cells at wide scan areas (e.g., 200 microns and above), we often encounter extremely bright spots. These are mostly large clusters, not individual NV centers (Points of Interest, or POIs). Individual POIs will be found later when zooming in closer (e.g., 5 to 1 micron resolution). 

Therefore, at this wide-scan stage, we must distinguish between:
1. **Region of Interest (ROI)**: The continuous, macro-structural area containing the cell body. It is characterized by low-to-moderate intensity, slowly-varying auto-fluorescence. We extract this mid-intensity "middle ground" area to analyze cellular background without bias, leaving room for future high-resolution POI analysis.
2. **Bright Clusters**: The extremely bright, localized intensity spikes. Because these clusters can be orders of magnitude brighter than the cell body, including them in the ROI data heavily skews background intensity analysis.
3. **Substrate Background**: The dark diamond substrate which occupies the majority of a wide-field scan and contains scattered noise.

The `ROISegmentationLogic` module precisely isolates the ROI.

## Pipeline (Multi-Scale Adaptive Algorithm)

The current pipeline uses an adaptive approach specifically designed to handle the large fields of view and varying scales of 200×200 µm scans:

### 1. Adaptive Background Estimation
A large median filter (kernel ~25 px) is applied to the raw fluorescence image. At 1 µm/px resolution, this kernel smooths over the cells (typically 10-30 µm diameter) and captures the slowly varying diamond substrate background.

### 2. Background Subtraction
The estimated background is subtracted from the original image, isolating the auto-fluorescent cell signal and bright spikes from the dark substrate.

### 3. Iterative Spike Removal (Sigma-Clipping)
To prevent extremely bright NV clusters from biasing the cell detection, we use iterative sigma-clipping on the background-subtracted signal:
- We compute the median and MAD (Median Absolute Deviation) of the signal.
- Pixels brighter than `Median + 4*Sigma` are flagged as spikes.
- This is repeated 3 times.
- The flagged spikes are dilated slightly to cover their optical halos, and then zeroed out in the working image.

### 4. Multi-scale Smoothing
The despiked signal is smoothed using a Gaussian filter whose sigma is tuned to the expected cell scale (e.g., `sigma=6.0` pixels). This produces a clean, continuous intensity map where cells appear as smooth blobs.

### 5. Adaptive Thresholding
An Otsu threshold (or a 65th percentile fallback) is computed strictly on the non-zero (non-substrate) regions of the smoothed image to generate a raw binary mask.

### 6. Connected Component Analysis & Size Filtering (Key Step)
Unlike simple thresholding which often over-segments the background, we run connected component labeling on the raw mask to evaluate each blob individually. A blob is accepted as a true cell only if:
- **Minimum Area**: Area >= 50 µm² (rejects small noise artifacts).
- **Maximum Area**: Area <= 70% of the image (rejects massive substrate regions).
- **Compactness**: `(4 * π * Area) / Perimeter² >= 0.05` (rejects thin linear artifacts, ensures somewhat round/elliptical shapes).

### 7. Morphological Refinement
The accepted cell regions are refined using morphological closing (to fill holes) and opening (to smooth boundaries). This produces the `cell_mask`.

### 8. Bright Spot Exclusion
Within each accepted cell, we re-evaluate the raw intensities. Any pixels exceeding a robust statistical threshold (`Median_cell + 5*Sigma_cell`) are identified as bright clusters. These are dilated and subtracted from the `cell_mask` to produce the final `roi_mask`.

## Output

The filtered data is exported to `_roi_filtered.dat`, where all pixels outside the `ROI Mask` are set to `0.0`. The logic module also returns the component labels and per-cell statistics (area, centroid, mean intensity, etc.) for downstream analysis.
