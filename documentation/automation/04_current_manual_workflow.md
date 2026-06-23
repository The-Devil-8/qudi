# 04 — Current Manual Workflow

## Overview

This document describes the step-by-step process that a user currently follows to find and optimize NV centers manually in Qudi. Understanding this workflow is essential for designing the automation that will replace it.

## Prerequisites

Before starting NV finding:
1. Qudi is running with confocal scanner hardware configured
2. The green excitation laser (532 nm) is on
3. The diamond sample is mounted and roughly focused
4. The confocal GUI and POI Manager GUI are open

## Step-by-Step Manual Workflow

### Step 1: Set Scan Area

**User action**: In the Confocal GUI, set the XY scan range and resolution.

**What happens in code**:
- User adjusts `image_x_range` and `image_y_range` spinboxes
- `ConfocalLogic` stores these as the scan boundaries
- `xy_resolution` determines pixel count (e.g., 100 = 100×100 image)

**Typical values**:
- Range: 10 μm × 10 μm (or up to 100 μm for wide-area survey)
- Resolution: 100–200 pixels per line
- Clock frequency: 500 Hz

### Step 2: Perform XY Scan

**User action**: Click "Start XY Scan" button.

**What happens in code**:
1. `ConfocalLogic.start_scanning(zscan=False)` is called
2. `initialize_image()` creates the empty image array
3. Scanner hardware clock is configured
4. `_scan_line()` is called repeatedly via signal chain
5. Each line's count data fills one row of `xy_image`
6. `signal_xy_image_updated` fires after each line → GUI updates color image
7. After all lines, scan stops

**Duration**: 20 seconds to several minutes depending on resolution and clock rate.

**Result**: A fluorescence color image is displayed in the Confocal GUI using the Inferno colormap.

### Step 3: Visually Inspect Color Image

**User action**: Look at the color image and identify bright spots.

**What the user sees**:
- Dark purple/black background = low fluorescence (not NV centers)
- Bright yellow/white spots = high fluorescence = potential NV centers
- Orange/red intermediate regions = could be NV or background variation

**User mental process**:
1. Adjust the color bar range (percentile or manual) to maximize contrast
2. Scan the image visually for small, bright, circular spots
3. Distinguish real NV spots from:
   - Background fluctuations (too dim, not localized)
   - Dust or dirt (irregular shape, often very bright)
   - Ensemble NV clusters (too large, extended bright areas)

### Step 4: Move Crosshair to Candidate

**User action**: Click on a bright spot in the color image to move the crosshair there.

**What happens in code**:
- Mouse click position is converted to physical (x, y) coordinates
- `ConfocalLogic.set_position(tag, x, y)` moves the scanner
- `_change_position()` sends the position to hardware
- Crosshair marker updates in the GUI

### Step 5: Optimize Position

**User action**: Click "Refocus" or "Optimize" button in the Confocal GUI (or POI Manager).

**What happens in code** (`OptimizerLogic.start_refocus()`):
1. Small XY scan centered on crosshair position (e.g., 0.6 μm × 0.6 μm)
2. 2D Gaussian fit to the XY intensity data → finds the true XY peak
3. Z scan through the focus range (e.g., ±1 μm around current Z)
4. 1D Gaussian fit to the Z intensity data → finds optimal Z focus
5. Scanner moves to the optimized (x, y, z) position
6. `sigRefocusFinished` signal emitted with the optimal position

**What the user checks**:
- Did the optimizer converge? (fit success)
- Is the optimized position close to where they clicked? (sanity check)
- Did the fluorescence increase after optimization? (good sign)

### Step 6: Add as POI

**User action**: Click "Add POI" in the POI Manager GUI (or Confocal GUI).

**What happens in code**:
- `PoiManagerLogic.add_poi()` is called
- Current scanner position is used as the POI coordinates
- A `PointOfInterest` object is created and added to the `RegionOfInterest`
- POI marker appears on the color image in the POI Manager GUI
- POI is added to the active POI ComboBox

### Step 7: Repeat for Next Candidate

The user goes back to Step 4 and clicks on the next bright spot. This continues until all visible NV centers have been found and optimized.

### Step 8 (Optional): Periodic Refocus

**User action**: Enable "Track POI" in the POI Manager to compensate for sample drift.

**What happens in code**:
- `PoiManagerLogic.start_periodic_refocus()` starts a timer
- Every `refocus_period` seconds, the optimizer is run on the active POI
- The position difference is used to update the ROI drift history
- All POI positions are adjusted accordingly

## Semi-Automated Option: Auto-Catch POI

The POI Manager has a basic `auto_catch_poi()` function that partially automates Step 3:

**User action**: Click "Auto POIs" button in POI Manager.

**What it does**:
1. Takes the current confocal scan image (the color image data)
2. Computes a global threshold (`mean_intensity × poi_threshold`)
3. Finds local maxima using sliding window
4. Checks spot shape symmetry
5. Adds all passing spots as POIs

**Limitations**:
- No background subtraction — fails on tilted/uneven samples
- No optimization — POI positions are at pixel resolution only
- No quality filtering — accepts anything above threshold
- No live GUI feedback during detection
- User must manually optimize each POI afterward
- Threshold is relative to global mean, not local background

## Time Analysis

For a typical session finding ~10 NV centers:

| Step | Time per NV | Total (10 NVs) |
|------|------------|----------------|
| XY Scan | 30–120 s | 30–120 s (once) |
| Visual inspection | 5–10 s | 50–100 s |
| Move crosshair | 2 s | 20 s |
| Optimize | 10–30 s | 100–300 s |
| Add POI | 2 s | 20 s |
| **Total** | | **~4–10 minutes** |

The automation aims to reduce this to: **press one button, wait ~3–5 minutes, get all NVs found and optimized**.

## Pain Points That Automation Solves

1. **Tedious repetition** — clicking on each spot, optimizing, adding POI × N
2. **Human inconsistency** — some NV centers might be missed visually
3. **No overnight operation** — user must be present for the entire process
4. **Suboptimal color range** — user might not set the best color contrast for detection
5. **No systematic coverage** — user might miss NVs in corners or low-contrast areas
6. **No quality metrics** — no objective record of why a spot was accepted/rejected
