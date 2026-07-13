# Cell Boundary Detection & Segmentation

This document details the techniques and implementation of the `CellSegmentationLogic` module, responsible for isolating regions of interest (e.g., biological cells) within a confocal fluorescence image.

## Background
In NV-diamond center experiments involving biological cells, the target area often exhibits a low-intensity, slowly varying auto-fluorescence against a completely dark background. However, the true NV centers appear as extremely bright, localized "spikes" within or outside the cells. 

To properly segment the cell boundaries from the background, we must distinguish between:
1. **Background Noise**: Very low counts outside the cell.
2. **Cell Body**: Moderate count area (auto-fluorescence).
3. **NV Centers**: High-intensity spikes.

## Pipeline

The `CellSegmentationLogic` module implements the following pipeline to generate a binary mask of the cell regions:

### 1. Despiking (Median Filter)
Because NV centers are incredibly bright (often orders of magnitude brighter than the cell auto-fluorescence), a simple Gaussian blur will spread their intensity and artificially enlarge the apparent size of the cell. 

We apply a **Median Filter** (e.g., `scipy.ndimage.median_filter` with a size of 7 pixels) to the raw fluorescence data. A median filter is robust against outliers, effectively completely erasing the localized NV spikes while preserving the underlying macro-structures like the cell body.

### 2. Macro-Structure Smoothing (Gaussian Filter)
After the spikes are removed, the image is heavily smoothed using a **Gaussian Filter** (`scipy.ndimage.gaussian_filter` with a high sigma, e.g., 5). This blurs the natural noise and slight irregularities in the cell's auto-fluorescence, creating a smooth, continuous blob that represents the cell.

### 3. Thresholding
We apply an **Otsu Threshold** (`skimage.filters.threshold_otsu`) to the smoothed image. Otsu's method automatically calculates the optimal threshold value to separate the foreground (cell) from the background. 
*(If `skimage` is not available, a static percentile threshold such as the 70th percentile is used as a fallback).*

### 4. Morphological Cleanup
The resulting binary mask might contain small holes or jagged edges. We clean the mask using morphological operations:
- **Closing**: Fills small holes and connects disjointed pieces of a single cell.
- **Fill Holes**: Ensures the interior of the cell is completely solid.
- **Opening**: Removes tiny isolated noise artifacts outside the main cell body.

## Filtering and Data Regeneration
Once the cell boundary mask is defined, the module zeroes out (makes completely dark) any fluorescence counts in the original image that fall *outside* the mask. 

The resulting spatial array is flattened and exported to a new `_filtered.dat` file, preserving the original Qudi confocal header formatting so that the data can be seamlessly reused by other modules.
