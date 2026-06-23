# Automated NV-Center Finding — Documentation Index

This folder contains comprehensive documentation for the automated NV (Nitrogen-Vacancy) center finding system in Qudi, built on **CIP (Color Image Processing)** concepts.

## What This System Does

Automates the process of locating NV centers in a diamond sample by:

1. Performing a confocal XY scan to acquire a fluorescence image
2. Analyzing the color/intensity image using CIP techniques to detect candidate NV centers
3. Running the optimizer on each candidate to refine its position
4. Registering confirmed NV centers as Points of Interest (POIs)
5. Continuously updating the GUI with progress and results

## Documentation Sections

### Foundations

| # | Document | Description |
|---|----------|-------------|
| 01 | [NV Center Basics](01_nv_center_basics.md) | Physics of NV centers, fluorescence properties, why we need to find them |
| 02 | [Confocal Scanning in Qudi](02_confocal_scanning.md) | How the confocal scanner acquires fluorescence images line-by-line |
| 03 | [CIP — Color Image Processing](03_cip_color_image_processing.md) | How fluorescence intensity maps to colors, the Inferno colormap, color bar mechanics, and CIP analysis techniques |

### Current System

| # | Document | Description |
|---|----------|-------------|
| 04 | [Current Manual Workflow](04_current_manual_workflow.md) | Step-by-step: how NV centers are found manually today |
| 05 | [Optimizer Deep Dive](05_optimizer_deep_dive.md) | Internal workings of `OptimizerLogic`: XY scan, 2D Gaussian fit, Z optimization |
| 06 | [POI Manager Deep Dive](06_poi_manager_deep_dive.md) | `PoiManagerLogic`: regions of interest, drift tracking, periodic refocus, existing `auto_catch_poi` |

### New Automation System

| # | Document | Description |
|---|----------|-------------|
| 07 | [AutoNVFinder Architecture](07_auto_nv_finder_architecture.md) | Architecture of the new `AutoNVFinderLogic`: state machine, connectors, signal flow |
| 08 | [CIP Detection Algorithm](08_cip_detection_algorithm.md) | Detailed algorithm: background subtraction, thresholding, local maxima, shape filtering, Gaussian refinement |
| 09 | [GUI Integration](09_gui_integration.md) | Auto NV Finder dock widget: controls, candidate table, color overlay markers |

### Operations

| # | Document | Description |
|---|----------|-------------|
| 10 | [Configuration Guide](10_configuration_guide.md) | How to set up the auto NV finder in Qudi config files |
| 11 | [Troubleshooting](11_troubleshooting.md) | Common issues, parameter tuning, debugging tips |
| 12 | [End-to-End User Guide](12_user_guide.md) | **Complete how-to-run guide**: prerequisites, config, step-by-step, parameter tuning, TaskRunner |
| 13 | [Validation Steps (HBT/ODMR)](13_validation_steps.md) | Auto-HBT & Auto-ODMR: what's implemented, what's missing, future roadmap |

## Quick Start

1. Read [12 — User Guide](12_user_guide.md) for the complete how-to-run instructions
2. Read [03 — CIP Concepts](03_cip_color_image_processing.md) to understand the detection approach
3. Read [07 — Architecture](07_auto_nv_finder_architecture.md) for the system design
4. Read [13 — Validation Steps](13_validation_steps.md) for HBT/ODMR validation status
5. Refer to [11 — Troubleshooting](11_troubleshooting.md) if issues arise

## Related Files

| File | Role |
|------|------|
| `logic/auto_nv_finder_logic.py` | Core automation engine |
| `logic/image_analysis.py` | CIP utility functions |
| `logic/optimizer_logic.py` | Position optimization |
| `logic/poi_manager_logic.py` | POI storage and tracking |
| `logic/confocal_logic.py` | Confocal scan acquisition |
| `gui/poimanager/poimangui.py` | GUI with Auto NV Finder dock |
| `logic/tasks/auto_nv_find.py` | TaskRunner integration |
