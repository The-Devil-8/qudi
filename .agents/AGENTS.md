# Qudi Automation Agent Instructions

This workspace contains the source code for the **Qudi** automation pipeline, specifically focusing on the automated detection, optimization, and characterization of NV (Nitrogen-Vacancy) centers in cells using confocal microscopy.

When working in this repository, follow these guidelines:

## 1. Project Context & Philosophy
- **Domain**: Confocal fluorescence microscopy, image processing, and hardware automation.
- **Pipeline Architecture**: The automation is strictly modular. The pipeline follows a coarse-to-fine zoom orchestration:
  1. **Wide-Field Segmentation** (`ROISegmentationLogic`): Identifies cell bounding boxes and macro-clusters from low-res scans.
  2. **Queuing** (`ScanRegionQueue`): Prioritizes bounding boxes.
  3. **Close-Scan Processing** (`CellRegionProcessor`): Analyzes high-res scans to find the "processable zone" (cell body minus nucleus and minus macro-clusters).
  4. **Candidate Extraction** (`POIExtractor`): Runs Confocal Image Processing (CIP) on the processable zone to find single NV candidates using adaptive scoring.
  5. **Verification & Optimization** (`NVCandidateVerifier` - WIP): Uses `OptimizerLogic` to physically refocus on candidates and verify them (fit quality, displacement) before registration.

## 2. Mandatory Reading
- **ALWAYS** read `documentation/automation/00_CURRENT_IMPLEMENTATION_AND_INTUITION.md` before beginning work or writing a plan. It contains the most up-to-date intuition, design decisions, and state of the implementation.
- Refer to the individual design documents in `documentation/automation/` (e.g., 20_poi_extractor_module.md) for detailed architecture decisions of specific modules.

## 3. Coding Guidelines
- **Language**: Python 3.
- **Libraries**: Use `numpy` and `scipy.ndimage` for image processing. Avoid adding heavy new dependencies.
- **UI**: Qudi uses PyQt5. Keep logic decoupled from UI whenever possible.
- **Data Structures**: Use strict data classes (e.g., `CellProcessingResult`, `POIExtractionResult`) rather than raw dictionaries to pass data between pipeline stages.
- **Type Checking & Docstrings**: Provide clear docstrings (numpy or sphinx style) explaining the purpose and return types.

## 4. Testing & Verification
- All core logic modules must have corresponding tests in the `tests/` directory (e.g., `tests/test_poi_extractor.py`).
- Use synthetic image generators in the test files to simulate specific edge cases (e.g., cells with no NVs, dense NV clusters, background noise).
- Save visual diagnostic outputs (like candidate overlays) to `tests/output_visuals/` so the user can easily inspect algorithm behavior without running the full hardware suite.
