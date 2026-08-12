# End-to-End User Guide: Automated NV Center Finder
To be updated.

> **Document 12 of the Automation Series**
> See [INDEX.md](INDEX.md) for the full documentation list.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Configuration](#3-configuration)
4. [Step-by-Step Usage](#4-step-by-step-usage)
5. [GUI Controls Reference](#5-gui-controls-reference)
6. [Understanding the Results](#6-understanding-the-results)
7. [Parameter Tuning Guide](#7-parameter-tuning-guide)
8. [Advanced: TaskRunner Usage](#8-advanced-taskrunner-usage)
9. [Validation Steps (HBT / ODMR)](#9-validation-steps-hbt--odmr)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Overview

The Automated NV Center Finder uses **CIP (Color Image Processing)** to detect NV centers from the confocal fluorescence image. It replaces the manual workflow of:

1. ❌ Visually scanning the Inferno color image for bright spots
2. ❌ Manually clicking on each candidate
3. ❌ Running "Optimize Position" on each candidate
4. ❌ Manually adding each confirmed NV as a POI

With a single-click automated pipeline:

```
▶ Start Auto Find
    ↓
[1] Grab current confocal scan image
    ↓
[2] CIP Detection: background subtraction → noise estimation
    → thresholding → local maxima → shape validation → clustering
    → sub-pixel Gaussian refinement → confidence scoring
    ↓
[3] For each candidate (brightest first):
    → Move scanner to candidate position
    → Run OptimizerLogic refocus (2D Gaussian XY fit + Z fit)
    → Accept/reject based on fit quality and displacement
    ↓
[4] Register accepted NVs as POIs in POI Manager
    ↓
✅ Done — all NV centers detected, optimized, and registered
```

> [!IMPORTANT]
> The current automation covers optical detection and position optimization only.
> It does **not** include spin validation steps (Auto-HBT or Auto-ODMR).
> See [Section 9](#9-validation-steps-hbt--odmr) for details on validation and
> [13_validation_steps.md](13_validation_steps.md) for the future roadmap.

---

## 2. Prerequisites

### Hardware
- Confocal scanner (real or simulated with `ConfocalScannerDummy`)
- Photon counter
- XYZ piezo scanner for optimization

### Software
- Qudi with the following modules loaded:
  - `ConfocalLogic` (scanner control)
  - `OptimizerLogic` (position optimization)
  - `PoiManagerLogic` (POI storage)
  - `FitLogic` (Gaussian fitting)
  - **`AutoNVFinderLogic`** (the new automation module)

### Python Environment & Dependencies
The Automated NV Center Finder is designed for modern Python environments. It is strongly recommended to use **Python 3.9+**.

You can rebuild your conda environment using the provided Windows script:
1. Open an **Anaconda Prompt** or **PowerShell**.
2. Navigate to the tools directory: `cd tools`
3. Run the rebuild script to cleanly install the modern Python 3.9 environment:
   ```powershell
   .\rebuild_conda_env.ps1
   ```
*(Note: If you are constrained by legacy hardware to Python 3.6, you can run `.\rebuild_conda_env.ps1 -EnvFile "conda-env-win10-64bit-qt5.yml"`, but future updates may not be supported).*

Specific module requirements:
- `numpy` (already required by Qudi)
- `scipy` (required for `ndimage.median_filter` and `ndimage.maximum_filter`)

Verify your environment by checking that `scipy` is available:
```bash
python -c "import scipy; print(scipy.__version__)"
```

---

## 3. Configuration

### 3.1 Add to Qudi Config File

Add the `AutoNVFinderLogic` module to the `logic:` section of your config file (e.g., `config/example/default.cfg`):

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
```

### 3.2 Connect to POI Manager GUI

Add the auto_nv_finder connector to the `poimanager` GUI entry:

```yaml
gui:
    poimanager:
        module.Class: 'poimanager.poimangui.PoiManagerGui'
        connect:
            poimanagerlogic: 'poimanagerlogic'
            scannerlogic: 'scannerlogic'
            auto_nv_finder: 'auto_nv_finder_logic'   # ← ADD THIS LINE
```

### 3.3 (Optional) Add TaskRunner Task

For scheduled/managed execution:

```yaml
logic:
    tasklogic:
        module.Class: 'taskrunner.TaskRunner'
        tasks:
            auto_nv_find:
                module: 'auto_nv_find'
                pausetasks: ['scan']
                needsmodules:
                    auto_nv_finder: 'auto_nv_finder_logic'
                config:
                    threshold_sigma: 5.0
                    max_candidates: 20
```

### 3.4 Full Working Config Example (Dummy Hardware)

```yaml
global:
    startup: ['man', 'tray']

hardware:
    mydummyscanner:
        module.Class: 'confocal_scanner_dummy.ConfocalScannerDummy'
        clock_frequency: 100
        connect:
            fitlogic: 'fitlogic'

logic:
    scannerlogic:
        module.Class: 'confocal_logic.ConfocalLogic'
        connect:
            confocalscanner1: 'mydummyscanner'
            savelogic: 'savelogic'

    optimizerlogic:
        module.Class: 'optimizer_logic.OptimizerLogic'
        connect:
            confocalscanner1: 'mydummyscanner'
            fitlogic: 'fitlogic'

    poimanagerlogic:
        module.Class: 'poi_manager_logic.PoiManagerLogic'
        connect:
            scannerlogic: 'scannerlogic'
            optimiserlogic: 'optimizerlogic'
            savelogic: 'savelogic'

    fitlogic:
        module.Class: 'fit_logic.FitLogic'

    savelogic:
        module.Class: 'save_logic.SaveLogic'
        win_data_directory: 'C:/Data'

    auto_nv_finder_logic:
        module.Class: 'auto_nv_finder_logic.AutoNVFinderLogic'
        connect:
            confocallogic: 'scannerlogic'
            optimizerlogic: 'optimizerlogic'
            poimanagerlogic: 'poimanagerlogic'
            fitlogic: 'fitlogic'

gui:
    man:
        module.Class: 'manager.managergui.ManagerGui'

    tray:
        module.Class: 'trayicon.TrayIcon'

    confocal:
        module.Class: 'confocal.confocalgui.ConfocalGui'
        connect:
            confocallogic1: 'scannerlogic'
            savelogic: 'savelogic'
            optimizerlogic1: 'optimizerlogic'

    poimanager:
        module.Class: 'poimanager.poimangui.PoiManagerGui'
        connect:
            poimanagerlogic: 'poimanagerlogic'
            scannerlogic: 'scannerlogic'
            auto_nv_finder: 'auto_nv_finder_logic'
```

---

## 4. Step-by-Step Usage

### Step 1: Perform a Confocal Scan

1. Open the **Confocal GUI** from the Manager
2. Set your scan range (e.g., 20 μm × 20 μm)
3. Set resolution (e.g., 100 × 100 pixels)
4. Click **Scan XY**
5. Wait for the scan to complete — you should see NV centers as bright spots on the Inferno color image

### Step 2: Open POI Manager

1. Open **POI Manager** from the Manager
2. Click **Get ROI from Confocal** to load the scan image
3. You should see the same Inferno color image in the POI Manager

### Step 3: Use Auto NV Finder

The **Auto NV Finder** dock widget should appear at the bottom of the POI Manager window.

1. **Adjust parameters** (optional):
   - **Threshold σ**: Detection sensitivity. Default: 5.0. Lower = more candidates (may include noise). Higher = fewer, more confident candidates.
   - **Min Intensity**: Absolute minimum counts/s. Default: 1000. Rejects dim features.
   - **Spot Diameter**: Expected NV spot size. Default: 1.5 μm. Match to your optical resolution.

2. Click **▶ Start Auto Find**

3. Watch the pipeline run:
   - The **progress bar** shows optimization progress
   - The **candidate table** populates with detected NV centers
   - **Color-coded markers** appear on the scan image:
     - 🟡 Yellow = pending
     - 🔵 Blue = currently being optimized
     - 🟢 Green = accepted
     - 🔴 Red = rejected
   - The **log panel** shows detailed messages

4. When complete, all accepted NV centers are registered as POIs

### Step 4: Review Results

- Click on any row in the candidate table to highlight it on the image
- Check the **Log** panel for detailed diagnostics
- POIs appear in the main POI Manager list with names like `NV_001`, `NV_002`, etc.

### Step 5: Stop (if needed)

- Click **⏹ Stop** to gracefully stop after the current candidate
- Already-processed candidates are preserved

---

## 5. GUI Controls Reference

| Control | Description |
|---------|-------------|
| **▶ Start Auto Find** | Start the full detection + optimization pipeline |
| **⏹ Stop** | Stop after current candidate (graceful) |
| **Threshold σ** | Detection threshold in noise sigma units (1–50, default: 5) |
| **Min Intensity** | Minimum absolute counts/s (0–1M, default: 1000) |
| **Spot Diameter** | Expected spot size in μm (0.1–50, default: 1.5) |
| **Auto Register POIs** | If checked, accepted NVs are auto-added as POIs |
| **Z Optimization** | If checked, also optimize the Z (focus) axis |
| **Candidates Table** | Shows all detected candidates with status |
| **Log** | Real-time log messages from the pipeline |

---

## 6. Understanding the Results

### Candidate Statuses

| Status | Meaning |
|--------|---------|
| ✅ Accepted | Optimization succeeded. Position refined and registered as POI. |
| ❌ Rejected | Optimization failed. Reasons: timeout, position displaced too far, or fit failed. |
| ⏭️ Skipped | Pipeline was stopped before this candidate was processed. |

### Result Summary

When the pipeline finishes, the log shows:
```
[DONE] Results: 12 detected, 9 accepted, 2 rejected, 1 skipped
```

### Confidence Score

Each candidate has a confidence score (0–1) based on:
- **SNR** (signal-to-noise ratio) — 50% weight
- **Circularity** (spot shape) — 30% weight
- **Fit quality** (Gaussian fit R²) — 20% weight

High confidence (>0.8) = very likely a real NV center.

---

## 7. Parameter Tuning Guide

### "I'm getting too many false positives"
- **Increase** Threshold σ (try 8–10)
- **Increase** Min Intensity (try 5000–10000)
- Ensure Spot Diameter matches your optical resolution

### "I'm missing dim NV centers"
- **Decrease** Threshold σ (try 3)
- **Decrease** Min Intensity (try 500)

### "All candidates are being rejected after optimization"
- The optimizer may be failing. Check:
  - Is the scanner hardware responsive?
  - Is the optimizer range large enough?
  - Try increasing `optimization_timeout` in the logic

### "Detection is too slow"
- Reduce the scan resolution (fewer pixels)
- Increase Threshold σ (fewer candidates to optimize)
- Set a lower `max_candidates` limit

### Quick Reference

| Scenario | Threshold σ | Min Intensity | Spot Diameter |
|----------|-------------|---------------|---------------|
| Dense NV ensemble | 3–5 | 1000 | 1.0 μm |
| Single NV in bulk diamond | 5–8 | 5000 | 1.5 μm |
| Shallow implanted NV | 4–6 | 2000 | 1.2 μm |
| Very noisy background | 8–15 | 10000 | 1.5 μm |

---

## 8. Advanced: TaskRunner Usage

### Running via TaskRunner GUI

1. Open the **Task Runner** GUI
2. Select the `auto_nv_find` task
3. Click **Start**
4. The task will run the same pipeline as the GUI button

### Running from Jupyter Notebook

```python
# Get the auto NV finder logic
finder = manager.get_module('auto_nv_finder_logic')

# Set parameters
finder.set_threshold(5.0)
finder.set_min_intensity(2000)
finder.set_spot_diameter(1.5e-6)

# Run the pipeline
finder.start_auto_find()

# Wait for completion
import time
while finder.is_running:
    time.sleep(1)

# Check results
for c in finder.candidates:
    print(f"{c.poi_name}: status={c.status}, "
          f"pos=({c.x*1e6:.2f}, {c.y*1e6:.2f}) μm, "
          f"intensity={c.intensity:.0f} c/s")
```

---

## 9. Validation Steps (HBT / ODMR)

> [!WARNING]
> The current implementation does **NOT** include Auto-HBT or Auto-ODMR validation.
> The pipeline stops at position optimization. Spin/photon validation must be
> done manually after the auto-finder completes.

### What's Missing (Step D)

After the auto-finder registers POIs, a full NV characterization workflow would include:

#### Auto-HBT (Single-Photon Validation)
- Run a g²(τ) second-order autocorrelation measurement at each POI
- If g²(0) < 0.5 → confirmed single NV center
- **Status**: No HBT/autocorrelation module exists in this codebase. Would require hardware (HBT beam splitter + 2 APDs + time correlator) and new logic module.

#### Auto-ODMR (Spin Validation)
- Run a rapid ODMR frequency sweep (2.7–3.0 GHz) at each POI
- If a 10–30% contrast dip is detected → confirmed NV⁻ center
- **Status**: `ODMRLogic` exists in this codebase at `logic/odmr_logic.py`. Auto-ODMR integration is architecturally feasible but not yet implemented.

### How to Validate Manually (Current Workflow)

After the auto-finder completes:

1. In POI Manager, select a POI (e.g., `NV_001`)
2. Click **Go to POI** to move the scanner there
3. Open the **ODMR GUI** and run a frequency sweep
4. If you see a dip at ~2.87 GHz → it's an NV⁻ center
5. Repeat for each POI

### Future: Auto-ODMR Integration Roadmap

See [13_validation_steps.md](13_validation_steps.md) for the detailed design of how Auto-ODMR could be integrated into the pipeline.

---

## 10. Troubleshooting

### "Auto NV Finder dock widget is not visible"

- Check that `auto_nv_finder_logic` is configured in your config file
- Check that the `auto_nv_finder` connector is added to the `poimanager` GUI config
- Check the Qudi log for: `AutoNVFinderLogic not connected`
- Go to **View** menu and enable the dock widget

### "No candidates found"

- Check that you have a scan image loaded (click **Get ROI from Confocal**)
- Lower the Threshold σ
- Lower the Min Intensity
- Ensure the scan actually contains NV centers (bright spots visible)

### "'scipy' not installed"

```bash
pip install scipy
```

### "Optimizer keeps timing out"

- Increase `optimization_timeout` in the logic (default: 30s)
- Check that the scanner hardware is responsive
- Check that OptimizerLogic is properly configured

### "Candidates are detected but all rejected"

- The optimized position may be too far from the initial estimate
- Check `spot_diameter` — if too small, the acceptance radius is too tight
- Increase `spot_diameter` to allow more displacement tolerance

---

## Quick Start Checklist

- [ ] Scipy installed
- [ ] Config file updated with `auto_nv_finder_logic`
- [ ] Config file updated with `auto_nv_finder` connector on `poimanager` GUI
- [ ] Confocal scan completed
- [ ] ROI loaded in POI Manager
- [ ] Auto NV Finder dock widget visible
- [ ] Click **▶ Start Auto Find**
- [ ] Review accepted POIs
- [ ] (Manual) Run ODMR on each POI to confirm NV⁻
