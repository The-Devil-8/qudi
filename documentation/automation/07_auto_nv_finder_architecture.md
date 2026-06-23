# 07 — AutoNVFinder Architecture

## Overview

The `AutoNVFinderLogic` is the new automation engine that replaces the manual NV-finding workflow with a fully automated pipeline using CIP (Color Image Processing) techniques.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AutoNVFinderLogic                            │
│                                                                     │
│  ┌───────────┐    ┌───────────┐    ┌──────────┐    ┌────────────┐  │
│  │  Scanner   │    │   CIP     │    │Optimizer │    │   POI      │  │
│  │  Control   │───▶│ Detection │───▶│  Loop    │───▶│ Register   │  │
│  │           │    │ Pipeline  │    │          │    │            │  │
│  │ start_scan│    │ detect()  │    │ refocus()│    │ add_poi()  │  │
│  └─────┬─────┘    └─────┬─────┘    └────┬─────┘    └─────┬──────┘  │
│        │                │               │                │          │
│        ▼                ▼               ▼                ▼          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Signal System                             │    │
│  │  sigScanComplete  sigCandidatesFound  sigCandidateUpdate    │    │
│  │  sigProgressUpdate  sigAutoFindComplete  sigStateChanged    │    │
│  └──────────────────────────────┬──────────────────────────────┘    │
└─────────────────────────────────┼───────────────────────────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
            ┌────────────┐ ┌──────────┐ ┌────────────┐
            │ Confocal   │ │ Optimizer│ │  POI       │
            │ Logic      │ │ Logic    │ │  Manager   │
            │            │ │          │ │  Logic     │
            └────────────┘ └──────────┘ └────────────┘
```

## Connectors

| Connector | Interface | Purpose |
|-----------|-----------|---------|
| `confocallogic` | `ConfocalLogic` | Access scan data, control scanner position, trigger scans |
| `optimizerlogic` | `OptimizerLogic` | Run refocus optimization on each candidate |
| `poimanagerlogic` | `PoiManagerLogic` | Register confirmed NV centers as POIs |
| `fitlogic` | `FitLogic` | 2D Gaussian fitting for sub-pixel position refinement |

## State Machine

```
                    start_auto_find()
                          │
                          ▼
    ┌──────┐      ┌──────────────┐      ┌────────────┐
    │      │      │              │      │            │
    │ IDLE │─────▶│   SCANNING   │─────▶│ DETECTING  │
    │      │      │              │      │            │
    └──┬───┘      │ Acquiring    │      │ CIP image  │
       ▲          │ confocal XY  │      │ analysis   │
       │          │ scan image   │      │            │
       │          └──────────────┘      └─────┬──────┘
       │                                      │
       │                                      ▼
       │          ┌──────────────┐      ┌────────────┐
       │          │              │      │            │
       │          │  REGISTERING │◀─────│ OPTIMIZING │
       │          │              │      │            │
       │          │ Adding POI   │      │ Running    │
       │          │ to manager   │      │ refocus on │
       │          │              │      │ candidate  │
       │          └──────┬───────┘      └────────────┘
       │                 │                    ▲
       │                 │ next               │
       │                 │ candidate          │
       │                 └────────────────────┘
       │                 │
       │                 │ all done or stop requested
       │                 ▼
       │          ┌──────────────┐
       └──────────│   COMPLETE   │
                  │              │
                  │ Emit results │
                  └──────────────┘
```

### State Transitions

| From | To | Trigger |
|------|----|---------|
| IDLE | SCANNING | `start_auto_find()` called |
| SCANNING | DETECTING | Scan complete (all lines acquired) |
| DETECTING | OPTIMIZING | CIP pipeline finds candidates, starts optimizing first one |
| DETECTING | COMPLETE | No candidates found |
| OPTIMIZING | REGISTERING | Optimizer finishes, quality passes |
| OPTIMIZING | OPTIMIZING | Optimizer finishes, quality fails → next candidate |
| REGISTERING | OPTIMIZING | POI registered → next candidate |
| REGISTERING | COMPLETE | Last candidate processed |
| Any state | IDLE | `stop_auto_find()` called |

## Signal Flow

### Outgoing Signals (to GUI)

```python
# Pipeline progress
sigStateChanged = QtCore.Signal(str)           # New state name
sigScanComplete = QtCore.Signal()              # Scan image ready
sigCandidatesFound = QtCore.Signal(list)       # List of CandidateNV objects
sigCandidateUpdate = QtCore.Signal(dict)       # One candidate status update
sigProgressUpdate = QtCore.Signal(int, int)    # (current, total) count
sigAutoFindComplete = QtCore.Signal(dict)      # Final results summary
```

### Incoming Signals (from other modules)

```python
# From ConfocalLogic
confocallogic().signal_xy_image_updated  # Scan line completed
confocallogic().signal_scan_lines_next   # Scan finished

# From OptimizerLogic  
optimizerlogic().sigRefocusFinished  # Optimization complete
```

## Data Flow

```
1. SCANNING
   ConfocalLogic.start_scanning()
        │
        ▼
   xy_image array (rows × cols × 4)
   └── [:, :, 3] = fluorescence counts/s
        │
2. DETECTING
   image_analysis.ConfocalImageAnalysis
        │
        ├── estimate_background() → background array
        ├── subtract background → corrected image
        ├── normalize_intensity() → [0,1] image
        ├── estimate_noise() → sigma value
        ├── threshold → binary mask
        ├── detect_local_maxima() → pixel positions
        ├── validate_spot_shape() → filtered positions
        ├── cluster_detections() → merged positions
        └── refine_position_gaussian() → sub-pixel positions
        │
        ▼
   List[CandidateNV(x, y, z_est, intensity, confidence)]
        │
3. OPTIMIZING (for each candidate)
   OptimizerLogic.start_refocus(candidate_pos)
        │
        ▼
   Optimized position + fit quality
        │
4. REGISTERING (if quality passes)
   PoiManagerLogic.add_poi(optimized_pos, name)
        │
        ▼
   POI stored, GUI updated
```

## CandidateNV Data Class

```python
@dataclass
class CandidateNV:
    """Represents a detected NV center candidate."""
    x: float              # Physical X position (meters)
    y: float              # Physical Y position (meters)  
    z_estimate: float     # Estimated Z (from current focus plane)
    pixel_row: int        # Row in scan image
    pixel_col: int        # Column in scan image
    intensity: float      # Peak fluorescence (counts/s)
    confidence: float     # Detection confidence [0, 1]
    status: str           # 'pending' | 'optimizing' | 'accepted' | 'rejected'
    rejection_reason: str # If rejected: why (e.g., 'fit_failed', 'out_of_range')
    optimized_pos: tuple  # Final (x, y, z) after optimization
    poi_name: str         # Assigned POI name (if registered)
```

## Configuration

All parameters are `StatusVar` instances for persistence:

```python
class AutoNVFinderLogic(GenericLogic):
    # CIP Detection parameters
    detection_threshold_sigma = StatusVar('detection_threshold_sigma', 5.0)
    min_spot_intensity = StatusVar('min_spot_intensity', 1000)
    max_candidates = StatusVar('max_candidates', 50)
    spot_diameter = StatusVar('spot_diameter', 1.5e-6)  # meters
    background_filter_size = StatusVar('background_filter_size', 15)  # pixels
    
    # Optimization parameters
    optimization_timeout = StatusVar('optimization_timeout', 30)  # seconds
    min_optimization_quality = StatusVar('min_optimization_quality', 0.5)
    enable_z_optimization = StatusVar('enable_z_optimization', True)
    
    # Behavior parameters
    auto_register_poi = StatusVar('auto_register_poi', True)
    auto_color_range = StatusVar('auto_color_range', True)
    enable_multi_scale = StatusVar('enable_multi_scale', False)
```

## Error Handling

| Error Condition | Response |
|----------------|----------|
| Scanner busy (locked by confocal) | Wait and retry, or abort with error signal |
| Optimizer busy | Queue and wait |
| Optimizer timeout | Skip candidate, mark as 'timeout' |
| Fit failure | Skip candidate, mark as 'fit_failed' |
| Optimized pos outside scan area | Reject, mark as 'out_of_range' |
| Maximum candidates reached | Stop detection, proceed with optimization |
| User stop request | Complete current optimization, then stop |

## Thread Safety

- `AutoNVFinderLogic` uses `Mutex` (`threadlock`) for internal state protection
- Scanner access is serialized — only one of {ConfocalLogic, OptimizerLogic, AutoNVFinderLogic} can drive the scanner at a time
- GUI updates use `QtCore.Signal` with `QueuedConnection` for thread-safe cross-thread communication
- The optimization loop runs in the logic thread; the GUI never blocks
