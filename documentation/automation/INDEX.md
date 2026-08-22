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

### Operations & Planning

| # | Document | Description |
|---|----------|-------------|
| 10 | [Configuration Guide](10_configuration_guide.md) | How to set up the auto NV finder in Qudi config files |
| 11 | [Troubleshooting](11_troubleshooting.md) | Common issues, parameter tuning, debugging tips |
| 12 | [End-to-End User Guide](12_user_guide.md) | **Complete how-to-run guide**: prerequisites, config, step-by-step, parameter tuning, TaskRunner |
| 13 | [Validation Steps (HBT/ODMR)](13_validation_steps.md) | Auto-HBT & Auto-ODMR: what's implemented, what's missing, future roadmap |
| 14 | [Automation Roadmap & Status](14_automation_roadmap_and_status.md) | Executive summary, approach decision, current status |
| 15 | [**Phased Implementation Plan**](15_phased_implementation_plan.md) | **Next steps: 7 phases, module wiring, standalone→connected checklist** |
| 16 | [**Testing Data Requirements**](16_testing_data_requirements.md) | **Dataset catalog, gaps in 200 µm data, acquisition & annotation checklist** |
| 17 | [**Algorithm Optimization**](17_algorithm_optimization.md) | **Cell boundary, ROI, cluster bboxes, CIP tuning parameters & metrics** |
| 18 | [Scan Region Queue](18_scan_region_queue.md) | Documentation for ScanRegionQueue module (multi-scale automation) |
| 19 | [Merged Legacy Modules](19_merged_legacy_modules.md) | Dispositions and explanations for modules pulled from docs branch |
| 20 | [POI Extractor](20_poi_extractor_module.md) | Processable-zone candidate scoring, narrowing, and ranking |
| 21 | [NVCandidateVerifier](21_nv_candidate_verifier.md) | Repeated asynchronous optical verification and auditable POI registration |
| 22 | [POI Verification Logging](22_poi_verification_logging.md) | Durable per-POI optimizer evidence, drift analysis, and tuning exports |
| 23 | [Pulsed Measurement Executor](23_pulsed_measurement_executor.md) | Asynchronous pulsed sequence execution and fidelity tracking |
| 24 | [Full Experiment Loop](24_full_experiment_loop.md) | Multi-cell scanning, POI filtering, verification, and drift tracking loop |
| 25 | [Algorithm Intuitions & Upgrades](25_algorithm_intuitions_and_upgrades.md) | Upgraded heuristics for ROI segmentation, noise bounding, and POI extraction |
| 26 | [Sample Characterization Engine](26_sample_characterization_engine.md) | Intelligent pre-segmentation analyzer, router, and algorithm dueler |

## Quick Start

1. Read [15 — Phased Implementation Plan](15_phased_implementation_plan.md) for **comprehensive next steps**
2. Read [16 — Testing Data Requirements](16_testing_data_requirements.md) before Phase 2+ (current 200 µm data is incomplete)
3. Read [17 — Algorithm Optimization](17_algorithm_optimization.md) when tuning cell / ROI / CIP
4. Read [26 — Sample Characterization Engine](26_sample_characterization_engine.md) for pre-segmentation algorithm selection
5. Read [14 — Roadmap & Status](14_automation_roadmap_and_status.md) for executive summary
6. Read [12 — User Guide](12_user_guide.md) for how to run today's single-scale pipeline
7. Read [07 — Architecture](07_auto_nv_finder_architecture.md) for system design
8. Refer to [11 — Troubleshooting](11_troubleshooting.md) if issues arise

## Related Files

| File | Role |
|------|------|
| `logic/auto_nv_finder_logic.py` | Core automation engine (single-scale CIP → optimize → POI) |
| `logic/sample_characterization_engine.py` | Intelligent pre-segmentation router & algorithm dueler |
| `logic/cell_segmentation_sparse.py` | AlgoA: Seeded hysteresis sparse cell segmentation |
| `logic/cell_segmentation_logic.py` | AlgoB: White Top-Hat adaptive dense cell segmentation |
| `logic/roi_segmentation_logic.py` | Cell ROI + bright cluster rejection (offline; Phase 1 target) |
| `logic/image_analysis.py` | CIP utility functions |
| `logic/image_rebuild_logic.py` | `.dat` → image visualization |
| `logic/optimizer_logic.py` | Position optimization |
| `logic/optimizer2.py` | Offline bounded 2-D Gaussian replay path; does not control hardware |
| `logic/poi_manager_logic.py` | POI storage and tracking |
| `logic/confocal_logic.py` | Confocal scan acquisition + FOV control |
| `logic/automation.py` | Legacy task tree skeleton (not active pipeline) |
| `gui/poimanager/poimangui.py` | GUI with Auto NV Finder dock |
| `gui/automation/automationgui.py` | Legacy automation GUI skeleton |
| `logic/tasks/auto_nv_find.py` | TaskRunner integration |
| `Confocal/*.dat` | Sample 200×200 µm scan data for offline tests |
