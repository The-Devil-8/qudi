# 03 — CIP: Color Image Processing

## What Is CIP?

**CIP (Color Image Processing)** refers to the techniques used to:
1. **Map** raw fluorescence intensity values to a visual color scale
2. **Display** the resulting color image to the user
3. **Analyze** the color/intensity patterns to extract information (e.g., NV center locations)

In Qudi's confocal microscope, CIP is the bridge between raw photon count data and actionable NV center positions.

## The Fluorescence-to-Color Pipeline

```
Photons hitting    →  Count rate     →  Color lookup   →  Pixel color    →  Display
the detector          (counts/s)        table (LUT)       (R, G, B, A)      on screen
                      
  ○ ○ ○ ○ ○           127,500          Inferno LUT        (255, 210, 0)     🟡 Bright
  ○ ○                  12,000          Inferno LUT        (80, 10, 50)      🟣 Dark
```

### Step 1: Raw Data Acquisition

The confocal scanner produces a 2D array of fluorescence count rates:

```python
# From confocal_logic.py — the raw intensity data
fluorescence_data = xy_image[:, :, 3]  # shape: (rows, cols), dtype: float
# Values are in counts/second, typically ranging from ~1,000 to ~200,000
```

### Step 2: Color Mapping via the Inferno LUT

Qudi uses the **Inferno** colormap (defined in `gui/colordefs.py`) to convert scalar intensity values to colors:

```
Intensity:  LOW ◄─────────────────────────────────────────────► HIGH

Inferno:    ██████████████████████████████████████████████████
            black → dark purple → magenta → red → orange → yellow → white

Meaning:    No signal    Background     Moderate        NV center!
            (noise)      fluorescence   fluorescence    (bright spot)
```

The color mapping is implemented as a **Look-Up Table (LUT)** — a 256-entry array mapping normalized intensity values [0, 255] to RGBA colors:

```python
# From gui/colordefs.py — ColorScaleInferno class
class ColorScaleInferno:
    # Contains self.lut — a 256×4 NumPy array of (R, G, B, A) values
    # Index 0   → black   (0, 0, 4, 255)      — lowest intensity
    # Index 128 → red     (187, 55, 84, 255)   — mid intensity  
    # Index 255 → yellow  (252, 255, 164, 255) — highest intensity
```

### Step 3: Color Range (Min/Max Scaling)

Before applying the LUT, the raw intensity values are scaled to the [0, 255] range:

```
pixel_color_index = 255 × (intensity - color_min) / (color_max - color_min)
```

The user controls `color_min` and `color_max` via:
- **Percentile mode**: e.g., 5th–95th percentile of the data
- **Manual mode**: user-specified absolute values

This directly affects NV visibility:

```
Poor color range (too wide):          Good color range (tight):
All spots look similar in color       NV spots pop out as bright colors
┌──────────────────┐                  ┌──────────────────┐
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │                  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │                  │ ▓▓▓▓▓▓▓▓▓░▓▓▓▓▓ │
│ ▓▓▓▓▓▓▓▓░▓▓▓▓▓▓ │  hard to see     │ ▓▓▓▓▓▓▓░███░▓▓▓ │  obvious!
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │                  │ ▓▓▓▓▓▓▓▓▓░▓▓▓▓▓ │
└──────────────────┘                  └──────────────────┘
```

### Step 4: Color Bar

The **color bar** is the legend that shows what each color means in physical units:

```
Fluorescence (kc/s)
┌──┐
│██│ 150    ← white/yellow = highest fluorescence
│██│ 125
│██│ 100    ← orange/red
│██│  75
│██│  50    ← dark red/magenta
│██│  25
│██│   0    ← black/dark purple = background
└──┘
```

Implementation: `gui/guiutils.py` → `ColorBar` class, connected to `gui/poimanager/poimangui.py` and confocal GUI.

## CIP Analysis Techniques for NV Detection

These techniques automate what a human does when looking at the color image:

### 1. Background Estimation & Subtraction

**What the user does**: Mentally ignores the uniform dim background and focuses on spots that "stand out" in color.

**Algorithm**: Apply a large-kernel median filter to estimate the slowly-varying background fluorescence, then subtract it:

```python
import numpy as np
from scipy.ndimage import median_filter

def estimate_background(image, kernel_size=15):
    """Estimate background from the fluorescence image.
    
    The background is the slowly-varying component — it appears as a
    uniform base color in the color image. Removing it makes NV spots
    (bright color regions) easier to detect.
    """
    return median_filter(image, size=kernel_size)

corrected = image - estimate_background(image)
```

### 2. Intensity Normalization (Color Range Auto-Tuning)

**What the user does**: Adjusts the color bar range (percentile sliders) until NV spots are clearly visible.

**Algorithm**: Normalize intensity using robust percentiles:

```python
def normalize_intensity(image, low_pct=2, high_pct=98):
    """Normalize image intensity to [0, 1] range.
    
    Equivalent to auto-adjusting the color bar so that
    NV spots appear as the brightest (hottest) colors.
    """
    vmin = np.percentile(image, low_pct)
    vmax = np.percentile(image, high_pct)
    return np.clip((image - vmin) / (vmax - vmin), 0, 1)
```

### 3. Noise Estimation

**What the user does**: Distinguishes real bright spots from random noise fluctuations based on experience.

**Algorithm**: Use MAD (Median Absolute Deviation) for robust noise estimation:

```python
def estimate_noise(image):
    """Estimate the noise level in the fluorescence image.
    
    This tells us the minimum intensity difference that counts
    as a real signal vs. just random color fluctuation.
    """
    median_val = np.median(image)
    mad = np.median(np.abs(image - median_val))
    sigma = 1.4826 * mad  # Convert MAD to standard deviation
    return sigma
```

### 4. Intensity Thresholding

**What the user does**: Ignores dim regions (cold/dark colors) and only looks at clearly bright (hot color) spots.

**Algorithm**: Apply a threshold based on background + noise:

```python
threshold = background_level + detection_sigma * noise_sigma
candidates_mask = corrected_image > threshold
```

### 5. Local Maxima Detection

**What the user does**: Among the bright regions, finds the single brightest pixel — the center of each NV spot.

**Algorithm**: A pixel is a local maximum if it's the highest-intensity pixel in its neighborhood:

```python
def detect_local_maxima(image, neighborhood_size):
    """Find pixels that are brighter than all their neighbors.
    
    These correspond to the "hottest colored" pixel in each
    local region — the most likely NV center positions.
    """
    from scipy.ndimage import maximum_filter
    local_max = maximum_filter(image, size=neighborhood_size)
    return (image == local_max) & (image > threshold)
```

### 6. Spot Shape Validation

**What the user does**: Recognizes that real NV spots are roughly circular, while artifacts (scratches, dust) have irregular shapes.

**Algorithm**: Check that the intensity profile is approximately circular:

```python
def validate_spot_shape(image, center_row, center_col, radius):
    """Check if the intensity pattern around a candidate is circular.
    
    A true NV center has a symmetric Gaussian-like intensity falloff
    in all directions (circular color gradient). Artifacts show
    asymmetric or elongated color patterns.
    """
    patch = image[center_row-radius:center_row+radius,
                  center_col-radius:center_col+radius]
    h_profile = patch[radius, :].mean()  # horizontal intensity
    v_profile = patch[:, radius].mean()  # vertical intensity
    # Profiles should be similar for a circular spot
    ratio = max(h_profile, v_profile) / min(h_profile, v_profile)
    return ratio < 1.3  # Allow 30% asymmetry
```

### 7. Sub-Pixel Gaussian Refinement

**What the user does**: The optimizer performs a fine scan and fits a Gaussian to the intensity profile to find the exact NV position.

**Algorithm**: Fit a 2D Gaussian to the local intensity patch:

```python
def refine_position_gaussian(image, center, radius):
    """Refine NV position by fitting 2D Gaussian to intensity data.
    
    The color intensity profile of an NV spot follows the microscope's
    Point Spread Function (PSF), which is well-approximated by a 2D
    Gaussian. Fitting gives sub-pixel position accuracy.
    """
    # Uses FitLogic.make_twoDgaussian_fit()
    # Returns refined (x, y) position and fit quality
```

## Color Image in the Qudi GUI

### Confocal GUI
- Image rendered with `ScanImageItem` (pyqtgraph) using Inferno LUT
- Color bar shows counts/s → color mapping
- User can zoom, pan, adjust color range
- Crosshair shows current scanner position

### POI Manager GUI  
- Shows the same fluorescence color image as background
- POI markers overlaid on the color image
- User clicks on bright-colored spots to add POIs

### Auto NV Finder (new)
- Processes the color/intensity image automatically
- Overlays detection results as colored markers:
  - 🟢 Green = confirmed NV (optimizer passed)
  - 🔴 Red = rejected candidate (optimizer failed)
  - 🟡 Yellow = pending optimization
  - 🔵 Blue = currently being optimized

## Key Source Files

| File | CIP Role |
|------|----------|
| `gui/colordefs.py` | Defines the Inferno color LUT (intensity → RGBA) |
| `gui/guiutils.py` | `ColorBar` class for the color scale legend |
| `gui/confocal/confocalgui.py` | Renders the color image in the confocal window |
| `gui/poimanager/poimangui.py` | Renders the color image in the POI manager |
| `qtwidgets/scan_plotwidget.py` | `ScanImageItem` — the pyqtgraph image widget |
| `logic/confocal_logic.py` | `draw_figure()` — matplotlib color image export |
| `logic/image_analysis.py` | **(NEW)** CIP analysis functions |
