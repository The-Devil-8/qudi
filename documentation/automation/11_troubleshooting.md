# 11 — Troubleshooting

## Common Issues and Solutions

### Detection Problems

#### No candidates detected

**Symptoms**: CIP pipeline completes but reports 0 candidates.

**Possible causes and fixes**:

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| Threshold too high | Check log: "0 pixels above threshold" | Lower `detection_threshold_sigma` (try 3.0) |
| No NVs in scan area | Check color image — is it uniformly dark? | Scan a different area or check laser alignment |
| Wrong Z focus | Image is blurry / no distinct spots | Optimize Z manually first, then re-scan |
| Background too high | Color image is bright everywhere | Check for stray light; increase `background_filter_size` |
| Sample fluorescence too low | Very low count rates (<1000 c/s) | Increase laser power or check APD alignment |
| `min_spot_intensity` too high | Spots exist but below absolute threshold | Lower `min_spot_intensity` |

#### Too many false positives

**Symptoms**: Many candidates detected but most fail optimization.

**Possible causes and fixes**:

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| Threshold too low | Hundreds of candidates found | Raise `detection_threshold_sigma` (try 8.0–10.0) |
| Dirty sample | Irregular bright spots in color image | Clean diamond surface |
| Noisy scan | Speckled color image | Increase scanner clock frequency (more averages per pixel) |
| Wrong spot diameter | Neighborhood too small | Increase `spot_diameter` to match actual spot size |
| Background not removed | Slowly-varying bright regions detected | Increase `background_filter_size` |

#### Wrong positions detected

**Symptoms**: Candidates are found but at wrong locations (e.g., on image edges or artifacts).

**Possible causes and fixes**:

| Cause | Fix |
|-------|-----|
| Edge effects from background subtraction | Check that scan edges have valid data |
| Saturated pixels | Reduce laser power so no pixel saturates the detector |
| Cosmic ray or glitch pixels | Re-scan the area; single-pixel artifacts won't reproduce |

### Optimization Problems

#### All candidates rejected

**Symptoms**: Candidates are found by CIP but all fail the optimization step.

| Cause | Fix |
|-------|-----|
| `min_optimization_quality` too high | Lower to 0.3 |
| Optimizer scan range too small | Check `OptimizerLogic.optimise_xy_size` — may need to be larger |
| Z out of focus | Run a manual Z scan first to find the right focal plane |
| Scanner hardware issue | Check that scanner responds to position commands |

#### Optimization takes too long

| Cause | Fix |
|-------|-----|
| Too many candidates | Reduce `max_candidates` |
| Slow scanner hardware | Increase `optimization_timeout` or reduce `optimizer_resolution` |
| Z optimization slow | Set `enable_z_optimization: False` if Z is already correct |

#### Optimized position far from CIP estimate

This usually means the CIP detection hit a false positive, or the scan resolution is too low for accurate initial estimates.

**Fix**: Increase confocal scan resolution (more pixels per μm).

### GUI Problems

#### Auto NV Finder dock not visible

**Causes**:
1. `AutoNVFinderLogic` not loaded — check config has the module defined
2. Connector not configured — GUI needs `auto_nv_finder` connector in config
3. Dock hidden — use **View** menu → **Auto NV Finder**, or **Restore Default View**

#### Candidate markers not visible on color image

**Causes**:
1. Color range issue — markers might blend with background. Try changing color bar range.
2. Zoom level — zoom in to see markers
3. Z-order — markers might be behind the image. This is a rendering bug.

#### Progress bar stuck

**Causes**:
1. Optimizer hanging — check if `OptimizerLogic` is in locked state
2. Scanner hardware unresponsive — may need hardware reset
3. Thread deadlock — check Qudi log for mutex/lock warnings

**Recovery**: Click **Stop**, wait for cleanup. If still stuck, restart the logic module.

## Parameter Tuning Guide

### Finding the Right Threshold

Start with defaults and adjust based on results:

```
Step 1: Run with detection_threshold_sigma = 5.0 (default)
        ├── Too few candidates? → Lower to 3.0
        └── Too many candidates? → Raise to 8.0

Step 2: Check the candidate list
        ├── Many rejections during optimization? → Threshold too low, raise it
        └── All accepted? → Could try lowering for more sensitivity

Step 3: Fine-tune in 0.5 increments until satisfied
```

### Finding the Right Spot Diameter

Measure the actual spot size in the confocal image:

1. Zoom in on a known NV center in the color image
2. Count the number of "bright" pixels across the spot
3. Multiply by pixel size: `spot_diameter = bright_pixels × pixel_size`
4. Pixel size = `scan_range / resolution`

Example:
```
Scan range: 10 μm, Resolution: 100 pixels
Pixel size: 0.1 μm
Bright pixels across NV spot: 5
spot_diameter = 5 × 0.1 = 0.5 μm
```

### Adjusting Background Filter Size

The median filter kernel should be 3–5× the spot size in pixels:

```
Spot size in pixels = spot_diameter / pixel_size
background_filter_size = 3 × spot_size_pixels (minimum)

Example:
Spot size = 5 pixels → background_filter_size = 15 (good)
Spot size = 3 pixels → background_filter_size = 9–11
```

**Warning**: If `background_filter_size` is too small, NV spots will be partially removed during background subtraction.

## Diagnostic Checks

### Check 1: Is the scan image good?

Before running auto-find, verify the confocal image:
- [ ] Image shows distinct bright spots on a dark background
- [ ] Color bar range is appropriate (not all one color)
- [ ] No excessive noise or artifacts
- [ ] Z focus is correct (spots are point-like, not blurry rings)

### Check 2: Are the connected modules working?

Test each module independently:
- [ ] **Confocal**: Can you run a manual XY scan?
- [ ] **Optimizer**: Can you manually optimize a known bright spot?
- [ ] **POI Manager**: Can you manually add and go to a POI?
- [ ] **Fit Logic**: Is it loaded? (check Qudi module manager)

### Check 3: Check the Qudi log

The Qudi log (`logger` module) will show:
- Module activation/deactivation messages
- Connector binding confirmations
- Error messages with stack traces
- Auto NV Finder pipeline progress messages

## Performance Expectations

| Scenario | Scan | Detection | Optimization | Total |
|----------|------|-----------|-------------|-------|
| 10×10 μm, 100px, ~5 NVs | 20s | <1s | 5× 10s = 50s | ~70s |
| 50×50 μm, 200px, ~20 NVs | 80s | <2s | 20× 10s = 200s | ~280s |
| 100×100 μm, 300px, ~50 NVs | 180s | <3s | 50× 10s = 500s | ~680s |

The bottleneck is optimization (sequential, one NV at a time). Detection (CIP) is fast.

## Error Messages Reference

| Error | Meaning | Action |
|-------|---------|--------|
| "Scanner is locked" | Another module is using the scanner | Wait for it to finish, or stop the other operation |
| "Optimization timeout for candidate N" | Optimizer took too long on one candidate | Usually safe to ignore — the candidate is skipped |
| "Fit failed for candidate N" | 2D Gaussian fit didn't converge | Candidate is rejected; may be noise or artifact |
| "Position out of range" | Optimized position fell outside scan area | Candidate near image edge; usually a false positive |
| "Maximum candidates reached" | Safety limit hit | Increase `max_candidates` or raise threshold |
| "No scan image available" | Confocal hasn't scanned yet | Run a confocal XY scan first |
