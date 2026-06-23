# 08 — CIP Detection Algorithm

## Overview

This document describes the CIP (Color Image Processing) algorithm that automatically detects NV center candidates from a confocal fluorescence image. The algorithm replaces the human visual inspection of the color image with a systematic computational pipeline.

## Pipeline Overview

```
Raw Fluorescence Image (counts/s array)
        │
        ▼
┌─────────────────────────┐
│ 1. Background Estimation│  Large-kernel median filter
│    & Subtraction        │  Removes slowly-varying background
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 2. Intensity            │  Percentile-based normalization
│    Normalization        │  Auto-adjusts "color range"
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 3. Noise Estimation     │  MAD-based robust noise level
│                         │  Defines minimum detectable signal
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 4. Intensity            │  Keep only pixels significantly
│    Thresholding         │  above background + noise
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 5. Local Maxima         │  Find brightest pixel in each
│    Detection            │  local neighborhood
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 6. Spot Shape           │  Check for circular intensity
│    Validation           │  profile (Gaussian PSF)
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 7. Spatial Clustering   │  Merge detections within
│                         │  spot_diameter of each other
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 8. Sub-Pixel Gaussian   │  2D Gaussian fit for precise
│    Refinement           │  position beyond pixel grid
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 9. Intensity Ranking    │  Sort brightest-first
│    & Confidence Score   │  Assign detection confidence
└───────────┬─────────────┘
            ▼
    List of CandidateNV
```

## Stage 1: Background Estimation & Subtraction

### Problem
The fluorescence image has a slowly-varying background from:
- Diamond autofluorescence
- Scattered laser light
- Sample tilt (one side brighter than the other)
- Detector bias

### Algorithm
Apply a **median filter** with a large kernel (larger than the expected NV spot size):

```python
from scipy.ndimage import median_filter

def estimate_background(image, kernel_size=15):
    """
    Estimate background using large-kernel median filter.
    
    The kernel must be significantly larger than the NV spot size
    (~3-5 pixels FWHM) so that NV spots don't affect the background estimate.
    
    kernel_size=15 means 15×15 pixel window → good for spots up to ~7 px wide.
    """
    return median_filter(image, size=kernel_size)

background = estimate_background(fluorescence_image, kernel_size=15)
corrected = fluorescence_image - background
corrected = np.maximum(corrected, 0)  # Clip negative values
```

### Visual Effect on Color Image
```
Before subtraction:               After subtraction:
(background gradient visible)     (flat background, spots stand out)

████████████████████████         ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
██████████████████░█████         ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░█▓▓▓▓▓▓▓▓▓
████████████████████████         ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
██████░█████████████████         ▓▓▓▓░█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
████████████████████████         ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓

Gradient makes right side          Both NVs equally visible
NV harder to see                   on uniform background
```

### Parameters
- `background_filter_size` (default: 15) — kernel size in pixels

## Stage 2: Intensity Normalization

### Problem
Different samples and scan settings produce different absolute count ranges. The detection threshold needs to be relative, not absolute.

### Algorithm
Percentile-based normalization (equivalent to auto-adjusting the color bar):

```python
def normalize_intensity(image, low_percentile=2, high_percentile=98):
    """
    Normalize intensity to [0, 1] range using percentiles.
    
    This is the algorithmic equivalent of the user adjusting the
    color bar min/max percentile sliders for optimal contrast.
    """
    vmin = np.percentile(image, low_percentile)
    vmax = np.percentile(image, high_percentile)
    if vmax <= vmin:
        return np.zeros_like(image)
    return np.clip((image - vmin) / (vmax - vmin), 0, 1)
```

## Stage 3: Noise Estimation

### Problem
Need to know the noise floor to set a meaningful detection threshold.

### Algorithm
Use **Median Absolute Deviation (MAD)**, which is robust against outliers (NV spots are outliers in the intensity distribution):

```python
def estimate_noise(image):
    """
    Estimate noise level using MAD (Median Absolute Deviation).
    
    Unlike standard deviation, MAD is not biased by bright NV spots.
    The factor 1.4826 converts MAD to equivalent Gaussian sigma.
    """
    median_val = np.median(image)
    mad = np.median(np.abs(image - median_val))
    sigma = 1.4826 * mad
    return sigma
```

## Stage 4: Intensity Thresholding

### Algorithm
Keep only pixels that are significantly brighter than the noise:

```python
threshold = detection_threshold_sigma * noise_sigma
# Also apply absolute minimum
threshold = max(threshold, min_spot_intensity_absolute)
candidate_mask = corrected_image > threshold
```

### Parameter Tuning Guide

| `detection_threshold_sigma` | Behavior |
|----|-----|
| 3.0 | Sensitive — catches dim NVs but also more false positives |
| 5.0 | **Default** — good balance for most samples |
| 8.0 | Conservative — only very bright, clear NV centers |
| 10.0+ | Very conservative — only the brightest spots |

## Stage 5: Local Maxima Detection

### Problem
The thresholded mask may contain extended bright regions. We need the peak position of each NV spot.

### Algorithm
A pixel is a local maximum if it's the highest value in its neighborhood:

```python
from scipy.ndimage import maximum_filter

def detect_local_maxima(image, mask, neighborhood_size):
    """
    Find local maxima — the brightest pixel in each neighborhood.
    
    In color image terms: find the single "hottest" pixel in each
    bright region.
    
    neighborhood_size should be ~2× the expected spot FWHM in pixels.
    """
    local_max = maximum_filter(image, size=neighborhood_size)
    # A pixel is a local maximum if it equals the local maximum value
    # AND is above the detection threshold
    maxima = (image == local_max) & mask
    return np.argwhere(maxima)  # Returns array of (row, col) positions
```

### Neighborhood Size Calculation

```python
# Convert physical spot diameter to pixels
pixel_size_x = (x_range[1] - x_range[0]) / num_pixels_x
neighborhood_pixels = int(spot_diameter / pixel_size_x)
# Ensure odd number (symmetric window)
neighborhood_pixels = max(3, neighborhood_pixels | 1)
```

## Stage 6: Spot Shape Validation

### Problem
Not all bright spots are NV centers. Dust, scratches, and other artifacts can also be bright.

### Algorithm
Check that the intensity profile around each candidate is approximately circular (consistent with a Gaussian PSF):

```python
def validate_spot_shape(image, row, col, radius):
    """
    Check if the intensity pattern is circular (Gaussian PSF-like).
    
    A real NV center shows:
    - Symmetric intensity drop-off in all directions
    - Similar horizontal and vertical intensity profiles
    
    Artifacts show:
    - Elongated or asymmetric patterns
    - Sharp edges (dust particle)
    - Linear features (scratches)
    """
    # Extract profiles through the candidate center
    row_profile = image[row, max(0,col-radius):col+radius+1]
    col_profile = image[max(0,row-radius):row+radius+1, col]
    
    # Compare profile integrals (should be similar for circular spots)
    if min(row_profile.sum(), col_profile.sum()) == 0:
        return False, 0.0
        
    ratio = max(row_profile.sum(), col_profile.sum()) / \
            min(row_profile.sum(), col_profile.sum())
    
    is_circular = ratio < 1.5  # Allow 50% asymmetry
    circularity = 1.0 / ratio  # 1.0 = perfect circle
    
    return is_circular, circularity
```

## Stage 7: Spatial Clustering

### Problem
Multiple pixels near a single NV center may all pass the local maxima + shape tests, especially for bright NV centers.

### Algorithm
Merge detections that are within `spot_diameter` of each other, keeping the brightest:

```python
def cluster_detections(positions, intensities, min_distance):
    """
    Merge nearby detections into single candidates.
    
    For each group of detections within min_distance of each other,
    keep only the brightest one.
    """
    from scipy.spatial.distance import cdist
    
    # Sort by intensity (brightest first)
    order = np.argsort(-intensities)
    positions = positions[order]
    intensities = intensities[order]
    
    clustered = []
    used = set()
    
    for i in range(len(positions)):
        if i in used:
            continue
        clustered.append((positions[i], intensities[i]))
        # Mark all nearby detections as used
        distances = cdist([positions[i]], positions)[0]
        nearby = np.where(distances < min_distance)[0]
        used.update(nearby)
    
    return clustered
```

## Stage 8: Sub-Pixel Gaussian Refinement

### Problem
Local maxima detection gives positions on the pixel grid. Real NV positions are between pixels.

### Algorithm
Fit a 2D Gaussian to the intensity patch around each candidate:

```python
def refine_position_gaussian(image, center_row, center_col, radius, 
                              x_coords, y_coords, fit_logic):
    """
    Refine position by fitting 2D Gaussian to intensity data.
    
    Uses FitLogic.make_twoDgaussian_fit() which fits:
    f(x,y) = offset + amp × exp(-((x-x0)²/2σx² + (y-y0)²/2σy²))
    
    Returns sub-pixel (x, y) position and fit quality.
    """
    # Extract local patch
    r1, r2 = max(0, center_row-radius), center_row+radius+1
    c1, c2 = max(0, center_col-radius), center_col+radius+1
    patch = image[r1:r2, c1:c2]
    
    # Get physical coordinates for the patch
    x_patch = x_coords[c1:c2]
    y_patch = y_coords[r1:r2]
    
    # Fit 2D Gaussian
    result = fit_logic.make_twoDgaussian_fit(
        x_axis=x_patch, y_axis=y_patch, data=patch
    )
    
    x_refined = result.params['center_x'].value
    y_refined = result.params['center_y'].value
    amplitude = result.params['amplitude'].value
    
    return x_refined, y_refined, amplitude, result
```

## Stage 9: Ranking & Confidence

### Algorithm
Assign a confidence score to each candidate based on multiple factors:

```python
def compute_confidence(candidate):
    """
    Compute detection confidence in [0, 1] range.
    
    Factors:
    - SNR: intensity above background / noise (higher = more confident)
    - Circularity: how circular the spot shape is (1.0 = perfect)
    - Fit quality: R² of the 2D Gaussian fit
    - Intensity: absolute count rate (single NV: ~100k c/s typical)
    """
    snr_score = min(1.0, candidate.snr / 20)         # Saturates at SNR=20
    shape_score = candidate.circularity               # Already [0, 1]
    fit_score = max(0, candidate.fit_r_squared)       # [0, 1]
    
    confidence = 0.4 * snr_score + 0.3 * fit_score + 0.3 * shape_score
    return confidence
```

## Full Pipeline Example

```python
def detect_candidates(self, scan_image, image_extent):
    """Run the complete CIP detection pipeline."""
    
    # Get fluorescence data (same data that produces the color image)
    fluorescence = scan_image[:, :, 3]
    
    # 1. Background subtraction
    background = estimate_background(fluorescence, self.background_filter_size)
    corrected = np.maximum(fluorescence - background, 0)
    
    # 2. Normalize (auto-adjust color range)
    normalized = normalize_intensity(corrected)
    
    # 3. Noise estimation
    noise_sigma = estimate_noise(corrected)
    
    # 4. Threshold
    threshold = self.detection_threshold_sigma * noise_sigma
    threshold = max(threshold, self.min_spot_intensity)
    mask = corrected > threshold
    
    # 5. Local maxima
    neighborhood = self._calc_neighborhood_pixels(image_extent)
    maxima_positions = detect_local_maxima(corrected, mask, neighborhood)
    
    # 6. Shape validation
    radius = neighborhood // 2
    valid_positions = []
    for pos in maxima_positions:
        is_valid, circularity = validate_spot_shape(
            corrected, pos[0], pos[1], radius)
        if is_valid:
            valid_positions.append((pos, circularity))
    
    # 7. Clustering
    positions = np.array([p[0] for p in valid_positions])
    intensities = np.array([corrected[p[0][0], p[0][1]] for p in valid_positions])
    clustered = cluster_detections(positions, intensities, neighborhood)
    
    # 8. Gaussian refinement
    candidates = []
    for (pos, intensity) in clustered[:self.max_candidates]:
        x, y, amp, fit_result = refine_position_gaussian(
            corrected, pos[0], pos[1], radius,
            x_coords, y_coords, self._fit_logic)
        candidates.append(CandidateNV(x=x, y=y, intensity=amp, ...))
    
    # 9. Rank by intensity
    candidates.sort(key=lambda c: c.intensity, reverse=True)
    
    return candidates
```

## Parameter Summary

| Parameter | Default | Stage | Effect |
|-----------|---------|-------|--------|
| `background_filter_size` | 15 | 1 | Larger = smoother background, but may merge close NVs |
| `detection_threshold_sigma` | 5.0 | 4 | Higher = fewer false positives, misses dim NVs |
| `min_spot_intensity` | 1000 | 4 | Absolute floor — rejects anything below this |
| `spot_diameter` | 1.5 μm | 5, 7 | Sets neighborhood size and clustering distance |
| `max_candidates` | 50 | 8 | Safety limit on number of candidates |
