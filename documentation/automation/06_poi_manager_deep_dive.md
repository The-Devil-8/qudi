# 06 — POI Manager Deep Dive

## Overview

The `PoiManagerLogic` module (in `logic/poi_manager_logic.py`) manages Points of Interest (POIs) — known NV center locations that the user wants to track and revisit. It is the storage and tracking layer that sits between NV detection and experiment execution.

## Architecture

```
┌────────────────────────┐
│    PoiManagerLogic      │
│                         │
│  Connectors:            │
│   ├ scannerlogic        │──→ ConfocalLogic (scan image, position)
│   ├ optimizer1          │──→ OptimizerLogic (refocus)
│   └ savelogic           │──→ SaveLogic (file I/O)
│                         │
│  Contains:              │
│   ├ RegionOfInterest    │ ← Container for all POIs + drift history
│   └ Timer               │ ← Periodic refocus scheduler
└────────────────────────┘
```

## Data Model

### RegionOfInterest (ROI)

A single ROI object holds:
- `name` — user-defined label (e.g., "diamond_sample_1")
- `creation_time` — timestamp
- `poi_list` — dictionary of POIs (`{poi_name: PointOfInterest}`)
- `scan_image` — the confocal color image snapshot
- `scan_image_extent` — physical coordinates of the image boundaries
- `pos_history` — list of `(timestamp, x, y, z)` tuples tracking sample drift

### PointOfInterest (POI)

Each POI stores:
- `name` — unique identifier (e.g., "NV_001")
- `position` — `[x, y, z]` in physical units (meters)
- `creation_time` — when it was found

## Key Operations

### Adding a POI

```python
def add_poi(self, position=None, name=None):
    """Add a new POI at the given or current scanner position."""
    if position is None:
        position = self._confocal_logic().get_position()[:3]
    # Generate unique name with nametag prefix
    # Add to roi.poi_list
    # Emit sigPoiUpdated
```

### Going to a POI

```python
def go_to_poi(self, name=None):
    """Move the scanner to a POI's position."""
    position = self.get_poi_position(name)
    self._confocal_logic().set_position('poimanager', *position)
```

### Optimizing a POI (Refocus)

```python
def optimise_poi_position(self, name=None):
    """Run optimizer on the active POI to refine its position."""
    position = self.get_poi_position(name)
    self._optimizer_logic().start_refocus(
        initial_pos=position,
        caller_tag='poimanager'
    )
    # When optimizer finishes → _optimisation_callback() is called
    # Updates POI position and ROI drift history
```

### Periodic Refocus (Drift Tracking)

The POI Manager can automatically refocus on the active POI at regular intervals:

```
┌───────┐    timer    ┌──────────┐   refocus   ┌───────────┐
│ Timer │───fires────▶│ POI Mgr  │──────────▶│ Optimizer  │
│       │            │           │◀──────────│            │
│       │            │ Update    │  new pos   │ Gaussian   │
│       │            │ drift     │            │ fit result │
│       │            │ history   │            │            │
└───────┘            └──────────┘            └───────────┘
```

Configuration:
- `refocus_period` — seconds between refocus attempts (e.g., 120 s)
- `move_scanner_after_optimise` — whether to reposition scanner after refocus

## Auto-Catch POI (Existing Basic Detection)

The `auto_catch_poi()` method provides rudimentary automated NV detection:

```python
def auto_catch_poi(self):
    """Automatically detect POIs from the current confocal scan image.
    
    Algorithm:
    1. Get the fluorescence intensity image from confocal logic
    2. Compute threshold: mean_intensity × poi_threshold
    3. Find local maxima using sliding window
    4. Filter by spot shape symmetry
    5. Convert pixel positions to physical coordinates
    6. Add each passing spot as a new POI
    """
```

### Supporting Methods

#### `_local_max(image, threshold, neighborhood_size)`
Finds local maxima by checking if each pixel is the maximum in its neighborhood and above the threshold.

#### `_is_spot_shape(image, row, col, radius)`
Validates spot symmetry by comparing horizontal and vertical intensity profiles through the candidate position. A real NV spot should have similar profiles in both directions (circular PSF).

### Current Limitations

| Limitation | Impact |
|-----------|--------|
| No background subtraction | Fails on tilted samples or uneven illumination |
| Global threshold (mean × factor) | Misses dim NVs, catches noise on bright backgrounds |
| No sub-pixel refinement | POI positions limited to pixel grid |
| No optimization after detection | User must manually optimize each POI |
| No confidence score | No way to rank detections by quality |
| No spatial clustering | May detect the same NV twice from neighboring pixels |
| No progress feedback | User doesn't know when detection finishes |

## Configuration Parameters

| Parameter | StatusVar | Default | Description |
|-----------|-----------|---------|-------------|
| `poi_threshold` | `poi_threshold` | 0.5 | Multiplied by mean intensity for threshold |
| `poi_diameter` | `poi_diameter` | 1.5e-6 | Expected POI diameter in meters |
| `refocus_period` | `refocus_period` | 120 | Seconds between periodic refocus |
| `poi_nametag` | `poi_nametag` | '' | Prefix for auto-generated POI names |
| `move_scanner_after_optimise` | — | True | Move scanner to optimized position |

## Signals

| Signal | Payload | Purpose |
|--------|---------|---------|
| `sigPoiUpdated` | `(old_name, new_name, position)` | POI added/renamed/moved/deleted |
| `sigActivePoiUpdated` | `name` | Active POI selection changed |
| `sigRoiUpdated` | `dict` | ROI-level changes (name, image, POIs, history) |
| `sigRefocusTimerUpdated` | `(is_active, period, time_remaining)` | Timer state changed |
| `sigRefocusStateUpdated` | `is_active` | Refocus in progress or finished |
| `sigThresholdUpdated` | `threshold` | Detection threshold changed |
| `sigDiameterUpdated` | `diameter` | Spot diameter changed |

## Save/Load

ROI data (including all POIs and their positions) can be saved to `.dat` files and loaded later:

```python
def save_roi(self):
    """Save current ROI with all POIs to a data file."""
    # Saves positions, names, scan image, history

def load_roi(self, complete_path=None):
    """Load a previously saved ROI from file."""
    # Restores all POI positions and scan image
```

## Integration with NV Automation

The `AutoNVFinderLogic` will use POI Manager to:

1. **Get the scan image**: `set_scan_image()` copies the latest confocal scan
2. **Register detected NVs**: Call `add_poi()` for each confirmed candidate
3. **Leverage existing refocus**: Use the periodic refocus mechanism for long-term tracking
4. **Batch operations**: The enhanced detection pipeline will replace `auto_catch_poi()` with CIP-based methods
