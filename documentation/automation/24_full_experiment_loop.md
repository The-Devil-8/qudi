# Full Experiment Loop

## Overview

The `FullExperimentLoop` orchestrates the end-to-end automated characterization of NV centers across an entire sample. It integrates scanning, identification, verification, pulsed measurement, and drift tracking into a single, cohesive workflow.

```mermaid
flowchart TD
    Start([Start Experiment]) --> LoopGrid[For each Grid Cell]
    
    LoopGrid --> ConfocalScan[1. Confocal Scan]
    ConfocalScan --> FindPOI[2. Find POIs (Spots)]
    FindPOI --> FilterPOI[3. Filter Repeated POIs]
    
    FilterPOI --> LoopPOI[For each valid POI]
    
    LoopPOI --> ZSurfaceScan[4. Z-Surface Find (Optional)]
    ZSurfaceScan --> VerifyNV[5. NVCandidateVerifier]
    
    VerifyNV -- "Not NV" --> LoopPOI
    VerifyNV -- "Verified NV" --> RunMeasurement[6. PulsedMeasurementExecutor]
    
    RunMeasurement --> TrackDrift[7. Track Drift Data]
    TrackDrift --> CheckTargetCount{Target Count Reached?}
    
    CheckTargetCount -- "No" --> LoopPOI
    CheckTargetCount -- "Yes" --> ArchiveCell[8. CellDataLogger: Interpolate & Archive Cell Close-Scan]
    
    LoopPOI -- "No more POIs" --> ArchiveCell
    ArchiveCell --> EndCell[End Current Cell]
    
    EndCell --> RescanCheck{Need Rescan?}
    RescanCheck -- "Yes" --> ConfocalScan
    RescanCheck -- "No" --> LoopGrid
    
    LoopGrid -- "No more cells" --> Complete([Experiment Complete: Finalize Run Manifest])
```

## User Input Parameters

The orchestrator requires high-level configuration from the user:

- **Scan Area**: Bounding box (X_min, X_max, Y_min, Y_max) for the total region to explore.
- **Grid Size**: Dimensions of individual confocal scan cells (e.g., 20x20 µm). {Why though this depends upon cell size itself.}
- **Target NV Count**: Stop condition (e.g., "Find and characterize 50 NVs").
- **Verification Thresholds**: ODMR contrast threshold, peak width limits. 
- **Measurement Recipe**: The type of pulsed measurement to run (e.g., T1, T2) and its parameters. {T1 currently}
- **Rescan Tolerance**: Maximum allowed coordinate shift before triggering a re-scan of the current cell.

## The Nested Loop Workflow

### 1. Cell Iteration
The total scan area is divided into smaller, manageable grid cells. The stage moves to the center of the first cell, and a high-resolution 2D confocal scan is acquired.

### 2. POI Identification & Filtering
The `ImageAnalyzer` logic processes the confocal scan to identify Points of Interest (POIs).
- **Non-Repetition Filtering**: A global registry of investigated coordinates (across all cells) is maintained. If a new POI falls within a predefined radius (e.g., 500 nm) of a previously investigated point, it is skipped. This prevents measuring the same NV multiple times, especially near cell boundaries or if a cell is rescanned.

### 3. Z-Surface Finding (Stub)
*(Documented Stub for Next Iteration)*
Before detailed verification, the Z-axis (focus) must be optimized. The `ZSurfaceFinder` module (currently a stub) will perform a 1D Z-scan over the POI to locate the surface and position the objective at the optimal depth for the NV center.

### 4. Verification (Hybrid Mode)
The POI is passed to the `NVCandidateVerifier`.
- **Hybrid Mode**: The verifier can operate in a hybrid mode, utilizing both continuous-wave (CW) ODMR and pulsed ODMR or auto-correlation (g(2)) if configured. This multi-step verification ensures high confidence that the POI is a single NV center before committing to long measurements.

### 5. Pulsed Measurement
If the verifier confirms an NV, the `PulsedMeasurementExecutor` is invoked with the user-defined recipe (e.g., a T1 relaxometry sequence). The system waits asynchronously for completion.

### 6. Drift Tracking
After measurement, the system records current environmental and hardware parameters:
- Stage coordinates (X, Y, Z)
- Temperature (if available)
- Laser power fluctuations
- Magnet temperature/position
*Currently, this is for data collection only. Active drift correction based on this data is planned for a future iteration.*

### 7. Target Counting
The global counter of successfully characterized NVs is incremented. **Crucially, only POIs that pass verification AND complete the pulsed measurement are counted towards the user's target.**

### 8. Cell Close-Scan Pinpointing & Systematic Archiving
Before completing a cell and advancing to the next queued region, the orchestrator invokes `CellDataLogger.save_cell_data()`:
- Verified and measured NVs are mapped to exact sub-pixel coordinates on the close-scan image via `interpolate_physical_to_pixel`.
- High-resolution annotated PNG (200 DPI) and vector PDF diagnostic plots are generated with dual-ring targets, badges, and side-panel manifests.
- Raw 4-channel scan arrays (`micro_scan_raw.npz`), JSON manifests (`cell_summary.json`), and tabular POI summaries (`cell_pois.csv`) cross-referencing pulsed measurement `save_tag` identifiers are saved to `data/AutoNV_YYYYMMDD_HHMMSS_<run_id>/Cell_<region_id>_<timestamp>/`.

### 9. Per-Cell Re-scanning
If significant drift is detected during the characterization of POIs within a cell (e.g., tracking a specific NV reveals the sample has moved beyond the `Rescan Tolerance`), the current cell is marked for re-scanning. The system will discard the remaining uninvestigated POIs in the current list, acquire a new confocal scan of the cell, and find new POIs (filtering out the ones already measured).

## Future Work Items

- **Active Drift Correction**: Utilize the collected drift data to actively reposition the stage or adjust focus during long measurements.
- **Implementation of ZSurfaceFinder**: Complete the 1D Z-scan logic.
- **ODMR Quality Gates**: Implement stricter pass/fail criteria based on ODMR linewidth, contrast, and signal-to-noise ratio within the Verifier.
- **Adaptive Grid Sizing**: Dynamically adjust the cell size based on NV density found in previous cells.
