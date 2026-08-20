# 25 — Algorithm Intuitions & Upgrades

This document outlines the upgraded algorithms for the Confocal automation pipeline to better handle challenging datasets (like `Confocal3`) featuring heavy 3D overlapping low-lit cells and extreme NV cluster spikes.

## 1. ROI Segmentation & Cell Region Processing
**Legacy Problem**: The original pipeline used a global median filter followed by a simple Otsu threshold (`threshold_otsu`) for both wide-field macro scans (`ROISegmentationLogic`) and high-res micro scans (`CellRegionProcessor`). Extreme NV spikes (1M+ counts/sec) would heavily skew the heavy-tailed Otsu histogram, resulting in thresholds that dropped the entire "faint" cell body. The hardcoded 60th percentile fallback was too brittle and often still discarded processable cell regions. Furthermore, macro-scans with overlapping cells merged into a single generic blob.

**Upgraded Intuition: Seeded Hysteresis (bounded by MAD)**
1. **Log-Scale Winsorization**: The raw fluorescence is log-transformed, and extreme top percentiles (e.g. 92nd percentile) are capped. This prevents extreme NV clusters from skewing the statistics.
2. **MAD Noise Floor**: A local noise floor (`noise_sigma`) is estimated using the Median Absolute Deviation (MAD) of the background-subtracted log data.
3. **Seeded Hysteresis Thresholding**:
   - Instead of a single global threshold, we use two: a high seed threshold (`t_otsu`) and a lower adaptive boundary threshold (`t_adaptive = max(0.4 * t_otsu, 2.5 * noise_sigma)`).
   - The cell bodies are grown (via morphological propagation) from the strong seeds outwards, stopping exactly at the noise floor. This perfectly captures faint boundaries of cells without bleeding into the diamond substrate.
4. **Instance Segmentation**: Overlapping macro cells are split into individual 3D instances using a distance/intensity peak-finding watershed, allowing distinct bounding boxes per cell for the `ScanRegionQueue`.

## 2. POI Extraction (Narrowing Gate 2)
**Legacy Problem**: In `POIExtractor`, after scoring candidates, an Otsu threshold was applied on a tiny 1D array of scores (e.g., 4 to 10 candidates). When candidates were all of similarly high quality (tight scores), Otsu would incorrectly place a threshold higher than the maximum score, resulting in a 100% rejection rate. The fallback to `np.median` forced a 50% rejection rate, unconditionally destroying good candidates.

**Upgraded Intuition: Max-Gap Splitting**
1. **Variance Check**: If the range of scores (`max_score - min_score`) is very small (e.g. < 0.15 on a normalized scale), it implies all candidates are of similar quality. In this case, we accept all surviving candidates.
2. **Maximum Gap Split (Jenks-like)**: For wider distributions, we sort the scores and find the largest single gap between adjacent scores. The threshold is placed precisely in the middle of this maximum gap, naturally clustering the candidates into "Strong" and "Marginal" groups without arbitrarily cutting distributions in half.
