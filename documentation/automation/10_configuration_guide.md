# 10 — Configuration Guide

## Overview

This guide explains how to configure the Auto NV Finder in your Qudi configuration file. The system uses Qudi's standard module/connector architecture.

## Minimal Configuration

Add these entries to your Qudi `.cfg` file:

```yaml
logic:
    # ... existing modules ...
    
    auto_nv_finder_logic:
        module.Class: 'auto_nv_finder_logic.AutoNVFinderLogic'
        connect:
            confocallogic: 'scannerlogic'
            optimizerlogic: 'optimizerlogic'
            poimanagerlogic: 'poimanagerlogic'
            fitlogic: 'fitlogic'

gui:
    poimanagergui:
        module.Class: 'poimanager.poimangui.PoiManagerGui'
        connect:
            poimanagerlogic: 'poimanagerlogic'
            scannerlogic: 'scannerlogic'
            # Add this connector for Auto NV Finder integration:
            auto_nv_finder: 'auto_nv_finder_logic'
```

## Full Configuration with Custom Parameters

```yaml
logic:
    auto_nv_finder_logic:
        module.Class: 'auto_nv_finder_logic.AutoNVFinderLogic'
        connect:
            confocallogic: 'scannerlogic'
            optimizerlogic: 'optimizerlogic'
            poimanagerlogic: 'poimanagerlogic'
            fitlogic: 'fitlogic'
        
        # CIP Detection parameters
        detection_threshold_sigma: 5.0    # Noise sigma multiplier for threshold
        min_spot_intensity: 1000          # Absolute minimum counts/s
        max_candidates: 50                # Safety limit on candidates
        spot_diameter: 1.5e-6             # Expected NV spot size (meters)
        background_filter_size: 15        # Background estimation kernel (pixels)
        
        # Optimization parameters
        optimization_timeout: 30          # Max seconds per optimization
        min_optimization_quality: 0.5     # Min R² for fit acceptance
        enable_z_optimization: True       # Optimize Z axis too
        
        # Behavior
        auto_register_poi: True           # Auto-add confirmed NVs as POIs
        auto_color_range: True            # Auto-adjust color scale
        enable_multi_scale: False         # Two-pass coarse→fine scanning
```

## TaskRunner Configuration

To use the Auto NV Finder as a scheduled task:

```yaml
logic:
    tasklogic:
        module.Class: 'taskrunner.TaskRunner'
        tasks:
            auto_nv_find:
                module: 'auto_nv_find'
                needsmodules:
                    auto_nv_finder: 'auto_nv_finder_logic'
                pausetasks: ['scan']      # Pause ongoing scans before running
                config:
                    threshold_sigma: 5.0
                    max_candidates: 20
```

## Parameter Reference

### CIP Detection Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `detection_threshold_sigma` | float | 5.0 | 2.0 – 20.0 | How many noise standard deviations above background a pixel must be to count as a candidate. Lower = more sensitive but more false positives. |
| `min_spot_intensity` | float | 1000 | 0 – ∞ | Absolute minimum fluorescence (counts/s). Spots below this are always rejected regardless of the sigma threshold. |
| `max_candidates` | int | 50 | 1 – 1000 | Maximum number of candidates to detect and optimize. Safety limit to prevent runaway scans. |
| `spot_diameter` | float | 1.5e-6 | 0.3e-6 – 10e-6 | Expected NV spot diameter in meters. Sets the local neighborhood size for peak detection and the clustering distance. |
| `background_filter_size` | int | 15 | 5 – 51 | Size of the median filter kernel (in pixels) for background estimation. Must be odd. Larger = smoother background but slower. Should be 3–5× larger than the spot size in pixels. |

### Optimization Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `optimization_timeout` | float | 30 | 5 – 300 | Maximum seconds to spend optimizing one candidate. If exceeded, the candidate is marked as 'timeout' and skipped. |
| `min_optimization_quality` | float | 0.5 | 0.0 – 1.0 | Minimum fit quality (R²) for the 2D Gaussian fit during optimization. Candidates with lower R² are rejected. |
| `enable_z_optimization` | bool | True | — | Whether to perform Z-axis optimization after XY optimization. Disable for faster processing if Z is already well-focused. |

### Behavior Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `auto_register_poi` | bool | True | Automatically register confirmed NV centers as POIs in the POI Manager. If False, candidates are detected and optimized but not added as POIs. |
| `auto_color_range` | bool | True | Automatically adjust the color bar range during detection for optimal contrast. |
| `enable_multi_scale` | bool | False | Enable two-pass detection: coarse scan of full area, then fine scan around each candidate. Slower but more precise. |

## Module Dependencies

The Auto NV Finder requires these modules to be loaded:

```
auto_nv_finder_logic
    ├── confocallogic (ConfocalLogic)
    │   └── scanner hardware (ConfocalScannerInterface)
    ├── optimizerlogic (OptimizerLogic)
    │   ├── scanner hardware (shared with confocallogic)
    │   └── fitlogic (FitLogic)
    ├── poimanagerlogic (PoiManagerLogic)
    │   ├── confocallogic (shared)
    │   ├── optimizerlogic (shared)
    │   └── savelogic (SaveLogic)
    └── fitlogic (FitLogic)
```

## Example Configurations

### High-Sensitivity (dim NV centers)

```yaml
auto_nv_finder_logic:
    detection_threshold_sigma: 3.0    # More sensitive
    min_spot_intensity: 500           # Accept dimmer spots
    max_candidates: 100               # Expect more candidates
    background_filter_size: 21        # Smoother background for better sensitivity
```

### Fast Survey (bright NV centers only)

```yaml
auto_nv_finder_logic:
    detection_threshold_sigma: 8.0    # Only very bright spots
    min_spot_intensity: 5000          # High absolute threshold
    max_candidates: 20                # Limit processing time
    enable_z_optimization: False      # Skip Z (faster)
    optimization_timeout: 15          # Quick timeout
```

### Dense NV Array (engineered diamond)

```yaml
auto_nv_finder_logic:
    detection_threshold_sigma: 5.0
    spot_diameter: 0.8e-6             # NVs are closer together
    background_filter_size: 9         # Smaller kernel for tight spacing
    max_candidates: 200               # Many NVs expected
```

## Troubleshooting Configuration

| Problem | Possible Cause | Fix |
|---------|---------------|-----|
| Module won't load | Missing connector target | Check that all connected modules exist in config |
| No candidates found | Threshold too high | Lower `detection_threshold_sigma` to 3.0 |
| Too many false positives | Threshold too low | Raise `detection_threshold_sigma` to 8.0 |
| Optimization always times out | Scanner too slow | Increase `optimization_timeout` |
| Wrong spot size detection | Incorrect diameter | Measure actual spot FWHM and set `spot_diameter` |
