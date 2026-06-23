# 02 — Confocal Scanning in Qudi

## Overview

Qudi's confocal scanning system acquires fluorescence images by raster-scanning a focused laser spot across the diamond sample and recording the emitted photon count rate at each position.

## Architecture

```
┌──────────────────┐     ┌────────────────────┐     ┌──────────────┐
│   Confocal GUI   │────▶│   ConfocalLogic    │────▶│  Scanner HW  │
│ (confocalgui.py) │◀────│(confocal_logic.py) │◀────│ (NI card /   │
│                  │     │                    │     │  dummy)      │
│ • XY image view  │     │ • Scan control     │     │              │
│ • Color bar      │     │ • Image arrays     │     │ • set_pos()  │
│ • Crosshair      │     │ • Position mgmt    │     │ • scan_line()│
│ • Settings       │     │ • History          │     │ • get_pos()  │
└──────────────────┘     └────────────────────┘     └──────────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │    SaveLogic       │
                         │  (save_logic.py)   │
                         │ • Data files       │
                         │ • Figure export    │
                         └────────────────────┘
```

## How a Scan Works

### 1. Image Initialization (`initialize_image()`)

The scan area is defined by `image_x_range`, `image_y_range`, and `xy_resolution`. The logic creates a 3D NumPy array:

```python
# xy_image shape: (y_pixels, x_pixels, 3 + num_count_channels)
# Each pixel stores: [x_position, y_position, z_position, counts_ch1, counts_ch2, ...]
self.xy_image = np.zeros((len(Y), len(X), 3 + len(count_channels)))
```

Source: `confocal_logic.py`, lines 436–565

### 2. Line-by-Line Scanning (`_scan_line()`)

The scanner acquires one horizontal line at a time:

```
Scan direction →
Line 0:  ──────────────────▶ (scan)
         ◀──────────────────  (retrace, counts discarded)
Line 1:  ──────────────────▶ 
         ◀──────────────────
Line 2:  ──────────────────▶
         ...
```

For each line:
1. Build an array of (x, y, z) positions along the line
2. Call `scanning_device.scan_line(line)` — the hardware moves and counts simultaneously
3. Store the returned count values in `xy_image[line_index, :, 3:]`
4. Emit `signal_xy_image_updated` so the GUI refreshes the color image
5. Perform a retrace (move back to line start) — counts are discarded
6. Increment scan counter, move to next line

Source: `confocal_logic.py`, lines 715–853

### 3. Scanner Hardware Interface

The scanner hardware implements `ConfocalScannerInterface`:

| Method | Purpose |
|--------|---------|
| `get_position_range()` | Physical limits of the scanner (meters) |
| `get_scanner_axes()` | Axis names, e.g., `['x', 'y', 'z']` |
| `get_scanner_count_channels()` | Counter channel names |
| `set_up_scanner_clock(freq)` | Configure acquisition timing |
| `set_up_scanner()` | Initialize scanner hardware |
| `scanner_set_position(x, y, z)` | Move to a single position |
| `scan_line(line_path)` | Scan along a line, return counts |
| `close_scanner()` | Clean up hardware |

Source: `interface/confocal_scanner_interface.py`

### 4. Image Data Structure

After a complete XY scan, `xy_image` contains:

```
xy_image[row, col, 0] = x position (meters)
xy_image[row, col, 1] = y position (meters)  
xy_image[row, col, 2] = z position (meters)
xy_image[row, col, 3] = fluorescence count rate channel 1 (counts/s)
xy_image[row, col, 4] = fluorescence count rate channel 2 (if present)
```

The fluorescence data (`xy_image[:, :, 3]`) is what gets rendered as the color image.

## Scan Types

### XY Scan (default)
- Scans a horizontal plane at the current Z position
- Produces the primary fluorescence color image used for NV detection

### Depth Scan (XZ or YZ)
- Scans a vertical plane through the sample
- Used to find the optimal Z focus depth
- Controlled by `depth_scan_dir_is_xz` flag

## Key Signals

| Signal | When Emitted | Purpose |
|--------|-------------|---------|
| `signal_xy_image_updated` | After each scan line | GUI updates the color image in real time |
| `signal_depth_image_updated` | After each depth scan line | GUI updates the depth color image |
| `signal_change_position` | When crosshair moves | GUI updates crosshair position |
| `signal_save_started` | When save begins | GUI shows save progress |

## Scan Parameters

| Parameter | StatusVar Name | Default | Description |
|-----------|---------------|---------|-------------|
| Clock frequency | `clock_frequency` | 500 Hz | Pixel acquisition rate |
| Return slowness | `return_slowness` | 50 | Steps during line retrace |
| XY resolution | `xy_resolution` | 100 | Pixels per scan line |
| Z resolution | `z_resolution` | 50 | Pixels in depth scan |
| Permanent scan | `permanent_scan` | False | Loop scan continuously |

## Tilt Correction

For tilted diamond samples, the confocal logic supports tilt correction using three reference points:

1. Set three points on the sample surface (`set_tilt_point1/2/3`)
2. Calculate the tilt plane (`calc_tilt_correction`)
3. Enable tilt correction — Z is adjusted automatically during XY scans

Source: `confocal_logic.py`, lines 1205–1243

## Relationship to NV Finding

The confocal XY scan produces the fluorescence image that is the **input** to the NV detection pipeline:

```
Confocal scan → Raw count array → Color mapping (Inferno) → Display
                       ↓
              CIP Detection Pipeline
                       ↓
              Candidate NV positions
```

The quality of the scan directly affects detection:
- **Higher resolution** → more precise candidate positions, but slower
- **Higher clock frequency** → lower noise per pixel (more photons averaged)
- **Correct Z focus** → maximum fluorescence contrast for NV centers
