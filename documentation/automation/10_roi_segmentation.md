# Region of Interest (ROI) Segmentation: Bright Cell Extraction

This document describes the `ROISegmentationLogic` pipeline for extracting cell
ROIs from wide-field confocal scans.

## Corrected Interpretation

The ROI target is the bright localized fluorescence inside valid diffuse
regions. Earlier versions treated those bright areas as NV-center artifacts and
subtracted them from the final ROI. That was wrong for the current dataset: the
bright highlighted regions are the cells we want to keep.

The dim diffuse fluorescence is now used only as a coarse bounding region. It
helps reject substrate background and limits where bright candidates may be
accepted, but it is not the final ROI.

## Pipeline

### 1. Adaptive Background Estimation

A large median filter estimates the slowly varying substrate background from
the raw fluorescence image.

### 2. Background Subtraction

The estimated background is subtracted from the raw signal. Negative values are
clipped to zero so downstream stages operate on positive contrast.

### 3. Despiking Before Smoothing

A median filter suppresses isolated one-pixel intensity spikes before Gaussian
smoothing. This prevents tiny speckles from expanding into large false diffuse
regions.

### 4. Diffuse Region Localization

The despiked image is smoothed at a broad scale and adaptively thresholded.
Connected components are filtered by:

- minimum diffuse-region area (`min_cell_area_um2`)
- maximum allowed image fraction (`max_cell_fraction`)
- compactness (`min_compactness`)

The result is `diffuse_region_mask`. This is a bounding mask, not the final
cell ROI.

### 5. Bright Cell Candidate Detection

Within `diffuse_region_mask`, the algorithm thresholds the original
fluorescence using a robust median/MAD threshold:

`median + bright_spot_sigma * sigma`

The resulting bright candidate mask is dilated by `bright_spot_dilate` pixels
to recover the local bright cell area. This intermediate output is
`raw_bright_spots`.

### 6. Bright Candidate Size and Shape Filtering

Bright candidates are then connected-component filtered independently from the
diffuse bounding regions. This is important because a valid diffuse region may
be large while the true bright cellular signal is smaller.

The final bright-cell minimum area is controlled by
`min_bright_cell_area_um2`. Small bright speckles can therefore be rejected
without forcing the same size threshold used for diffuse regions.

### 7. Final ROI Export

The final `roi_mask` is the filtered bright cell mask. Exported
`_roi_filtered.dat` files preserve the original fluorescence values inside this
mask and set all pixels outside it to `0.0`.

## Diagnostic Plot Meaning

The confocal verification plot uses four panels:

1. Original fluorescence image.
2. Diffuse Region Mask (Bounding areas).
3. Cell Mask (Bright NV Clusters).
4. Final ROI (Original Cell Fluorescence).

Panel 4 should visually match the accepted bright regions from Panel 3, but
rendered with the original input fluorescence colors/counts.

## Tuning Notes

`min_cell_area_um2` controls the broad diffuse bounding regions. Increase it if
large background haze is being accepted, or decrease it if valid cells are not
localized at all.

`min_bright_cell_area_um2` controls the final bright cell components. Increase
it to reject more small bright speckles; decrease it if valid bright cells are
being removed after they appear correctly in the third diagnostic panel.

`bright_spot_sigma` controls how bright a candidate must be relative to its
diffuse region. Lower values include more of the bright cellular signal; higher
values keep only the strongest cores.
