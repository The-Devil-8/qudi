# 05 — Optimizer Deep Dive

## Overview

The `OptimizerLogic` module (in `logic/optimizer_logic.py`) refines the scanner position to maximize fluorescence signal at a known bright spot. It is the key component that converts a rough CIP-detected candidate position into a precise NV center location.

## Architecture

```
┌─────────────────────┐
│   OptimizerLogic    │
│                     │
│ Connectors:         │
│  ├ confocallogic1   │──→ ConfocalLogic (gets scan image, position)
│  └ fitlogic         │──→ FitLogic (2D/1D Gaussian fitting)
│                     │
│ Uses:               │
│  └ _scanning_device │──→ ConfocalScannerInterface (shared with ConfocalLogic)
│                     │
│ Thread Safety:      │
│  └ threadlock       │──→ Mutex (prevents concurrent optimization)
└─────────────────────┘
```

## Optimization Sequence

When `start_refocus()` is called, the optimizer performs this sequence:

```
start_refocus(initial_pos, caller_tag)
       │
       ▼
  ┌─────────────────┐
  │  Lock scanner    │  Acquire hardware mutex
  │  Set up clock    │  Configure acquisition timing
  │  Set up scanner  │  Initialize hardware
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │  XY Optimization │  Small area XY scan + 2D Gaussian fit
  │  (if enabled)    │  Finds optimal (x, y) position
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │  Z Optimization  │  1D Z scan + 1D Gaussian fit
  │  (if enabled)    │  Finds optimal z focus depth
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │  Move to optimal │  Scanner moves to (x_opt, y_opt, z_opt)
  │  Clean up        │  Release hardware
  │  Emit signal     │  sigRefocusFinished / sigRefocusXySizeChanged
  └─────────────────┘
```

## XY Optimization

### Scan Phase

The optimizer performs a small XY scan centered on the initial position:

```python
# Scan area: optimise_xy_size × optimise_xy_size 
# Default: 0.6 μm × 0.6 μm
# Resolution: optimizer_resolution × optimizer_resolution
# Default: 10 × 10 pixels

x_range = [pos[0] - size/2, pos[0] + size/2]
y_range = [pos[1] - size/2, pos[1] + size/2]
```

Each pixel is scanned as a single-point acquisition (not a continuous line scan like confocal). This is slower per pixel but gives more precise individual count values.

Source: `optimizer_logic.py`, lines 237–383

### Fit Phase

A **2D Gaussian** model is fit to the XY intensity data:

```
f(x, y) = offset + amplitude × exp(
    -( (x - x0)² / (2 σx²) + (y - y0)² / (2 σy²) )
)
```

Parameters extracted:
- `x0, y0` — the peak position (optimal NV location)
- `amplitude` — peak fluorescence above background
- `σx, σy` — spot width (should be ~PSF width)
- `offset` — local background level

Source: `_set_optimized_xy_from_fit()`, lines 385–422

### Quality Checks

The optimizer validates the fit result:
1. **Position within scan range**: If the fitted position is outside the scan area, it's rejected — the initial position is kept
2. **Fit convergence**: If the fit fails to converge, the initial position is used

## Z Optimization

### Scan Phase

A 1D scan along the Z axis at the (optimized) XY position:

```python
# Z range: refocus_Z_size centered on current Z
# Default: ±1 μm
# Resolution: optimizer_resolution points
```

Source: `do_z_optimization()`, lines 424–490

### Fit Phase

A **1D Gaussian** is fit to the Z intensity data:

```
f(z) = offset + amplitude × exp( -(z - z0)² / (2 σz²) )
```

Parameters extracted:
- `z0` — optimal Z focus position
- `amplitude` — peak fluorescence at best focus
- `σz` — axial point spread function width (~1 μm typically)

### Quality Checks

Same as XY: the fitted Z position must be within the scan range.

## Key Configuration Parameters

| Parameter | StatusVar | Default | Description |
|-----------|-----------|---------|-------------|
| `refocus_XY_size` | `optimise_xy_size` | 0.6 μm | Side length of XY optimization scan area |
| `refocus_Z_size` | `refocus_Z_size` | 2 μm | Total range of Z optimization scan |
| `optimizer_resolution` | `optimizer_resolution` | 10 | Pixels per axis in optimization scans |
| `do_surface_subtraction` | `do_surface_subtraction` | False | Subtract a surface fit from Z data |
| `surface_subtr_scan_offset` | `surface_subtr_scan_offset` | 1 μm | Offset for surface subtraction reference |
| `clock_frequency` | — | 50 Hz | Acquisition rate for optimizer scans |

## Signals

| Signal | Payload | When Emitted |
|--------|---------|-------------|
| `sigRefocusStarted` | — | Optimization begins |
| `sigRefocusFinished` | `(x, y, z, caller_tag)` | Optimization complete |
| `sigRefocusXySizeChanged` | — | XY scan size was adjusted |
| `sigImageUpdated` | — | Optimizer image data updated |
| `sigPositionUpdated` | `(x, y, z, a)` | Position changed during optimization |

## Integration with NV Automation

The `AutoNVFinderLogic` will call `start_refocus()` for each CIP-detected candidate:

```python
# For each candidate from CIP detection:
optimizer.start_refocus(
    initial_pos=[candidate.x, candidate.y, current_z],
    caller_tag='auto_nv_finder'
)
# Wait for sigRefocusFinished
# Check if optimal position is within tolerance of initial estimate
# Accept or reject candidate based on optimization quality
```

## Thread Safety

The optimizer uses a `Mutex` (`threadlock`) to prevent concurrent optimization attempts. The confocal scanner hardware is shared between `ConfocalLogic` and `OptimizerLogic`, so only one can use it at a time. The optimizer acquires the scanner lock via `module_state.lock()` before scanning.

## Limitations

1. **Single-point only**: The optimizer handles one position at a time. For N candidates, it must be called N times sequentially.
2. **No batch mode**: Cannot optimize multiple positions in a single scan.
3. **Fixed scan pattern**: Always does a full grid scan; no adaptive/sparse scanning.
4. **No quality score**: Returns position but no numerical quality metric (R², SNR). The automation will need to compute this from the fit result.
