# 20 — POIExtractor Module: Design & Architecture

> **Document 20 of the Automation Series**  
> Comprehensive design document for the `POIExtractor` module that takes
> `CellRegionProcessor` output, identifies POI candidates dynamically,
> scores and narrows them down, and feeds high-confidence candidates to
> the Optimizer + Verification pipeline.

**Related documents:**
- [07 — AutoNVFinder Architecture](07_auto_nv_finder_architecture.md) — existing CIP pipeline
- [08 — CIP Detection Algorithm](08_cip_detection_algorithm.md) — detection stages
- [14 — Roadmap & Status](14_automation_roadmap_and_status.md) — project overview
- [15 — Phased Implementation Plan](15_phased_implementation_plan.md) — phased next steps
- [17 — Algorithm Optimization](17_algorithm_optimization.md) — parameter tuning
- [18 — ScanRegionQueue](18_scan_region_queue.md) — upstream queue management

**Related source files:**
- `logic/cell_region_processor.py` — upstream module (CellRegionProcessor)
- `logic/image_analysis.py` — CIP utilities (ConfocalImageAnalysis)
- `logic/auto_nv_finder_logic.py` — existing detection pipeline (AutoNVFinderLogic)
- `logic/optimizer_logic.py` — downstream optimizer (OptimizerLogic)
- `logic/poi_manager_logic.py` — POI persistence (PoiManagerLogic)

---

## 1. Purpose & Problem Statement

### The Gap

The current pipeline has a **missing link** between two well-defined stages:

```
CellRegionProcessor                                    Optimizer + Verification
(produces processable zone                    →   ?   →  (needs precise NV candidate
 mask + zone statistics)                                   positions to optimize)
```

**CellRegionProcessor** tells us *where to look* — it carves out the processable
cytoplasm zone by removing the nucleus and bright NV clusters.  But it does NOT
identify individual NV centre candidates.

**OptimizerLogic** can refine a candidate position — but it needs an (x, y, z)
starting position that is already close to a real NV centre (within ~2× the PSF
width, i.e. ~1–2 µm).

### What POIExtractor Does

The `POIExtractor` bridges this gap.  It:

1. **Takes** the `CellProcessingResult` (masks, stats) and the close-scan image
2. **Runs CIP detection** confined to the processable zone mask
3. **Dynamically identifies** a variable number of POI candidates (cells have
   different NV densities — some have 20 bright spots, some have 3)
4. **Scores** each candidate on multiple quality axes
5. **Narrows down** to a smaller set of high-confidence NV candidates
6. **Outputs** a ranked list ready for the Optimizer + Verification pipeline

### Why a Separate Module?

- **Single Responsibility**: CellRegionProcessor handles cell morphology;
  POIExtractor handles NV-level detection within processed zones.
- **Reusability**: The same extraction logic works regardless of whether the
  processable zone came from a close-scan or from a manual ROI selection.
- **Tunability**: Different cells require different thresholds — POIExtractor
  encapsulates all NV-specific parameter logic.
- **Variable Output**: The number of candidates varies wildly per cell (0 to
  50+).  The narrowing logic must be adaptive, not hard-coded.

---

## 2. Position in the Pipeline

```
Wide-Field Scan (200×200 µm)
        │
        ▼
ROISegmentationLogic.segment_roi()
        │
        ▼  roi_mask, cell_mask, bright_cluster_mask
ScanRegionQueue.extract_regions_from_segmentation()
        │
        ▼  List[ScanRegion] — priority queue
For each region:
    ConfocalLogic → close scan (30-60 µm FOV)
        │
        ▼  close_scan_image (ny × nx × 4)
    CellRegionProcessor.process(close_scan_image)
        │
        ▼  CellProcessingResult
           ├── processable_mask (where to look)
           ├── nucleus_mask (excluded)
           ├── bright_cluster_mask (excluded)
           ├── zone_stats (intensity statistics)
           └── diagnostics
        │
        ▼
    ┌──────────────────────────────────────────────────┐
    │           POIExtractor  (THIS MODULE)            │
    │                                                  │
    │  Input:  CellProcessingResult + close_scan_image │
    │                                                  │
    │  Stage A: CIP Detection within processable zone  │
    │  Stage B: Multi-metric candidate scoring         │
    │  Stage C: Adaptive narrowing / filtering         │
    │  Stage D: Spatial deconfliction                  │
    │  Stage E: Ranked output                          │
    │                                                  │
    │  Output: List[POICandidate]                      │
    │          (ranked, scored, narrowed)               │
    └───────────────────┬──────────────────────────────┘
                        │
                        ▼
    ┌──────────────────────────────────────────────────┐
    │   NVCandidateVerifier  (NEXT MODULE — doc 21)    │
    │                                                  │
    │  Wraps OptimizerLogic:                           │
    │  • Sequential refocus at each POICandidate       │
    │  • 2D Gaussian fit quality (R²) gating           │
    │  • Displacement rejection                        │
    │  • Optional ODMR verification                    │
    │  • POI registration in PoiManagerLogic           │
    │                                                  │
    │  Output: List[VerifiedNV] → registered as POIs   │
    └──────────────────────────────────────────────────┘
```

**IMPORTANT**: POIExtractor does NOT call the Optimizer.  It only produces candidate
positions.  The downstream `NVCandidateVerifier` module (to be documented
in doc 21) wraps the Optimizer and verification logic.

---

## 3. Input Specification

### 3.1 From CellRegionProcessor

```python
class CellProcessingResult:
    cell_interior_mask: np.ndarray    # (ny, nx) bool — cell foreground
    nucleus_mask: np.ndarray          # (ny, nx) bool — dark nucleus void
    bright_cluster_mask: np.ndarray   # (ny, nx) bool — bright NV aggregations
    processable_mask: np.ndarray      # (ny, nx) bool — WHERE to search for NVs
    zone_stats: dict                  # area, mean/median/std intensity
    nucleus_stats: dict               # detected, area, centroid, contrast
    bright_cluster_stats: list[dict]  # per-cluster area, peak, centroid
    diagnostics: dict                 # cell_area_px, n_bright_clusters, etc.
```

**Key field**: `processable_mask` — this is the region where POIExtractor
should search.  Everything outside this mask is either substrate, nucleus,
or bright cluster.

**zone_stats when processable:**
```python
{
    'area_px': int,
    'area_fraction_of_cell': float,
    'mean_intensity': float,
    'median_intensity': float,
    'std_intensity': float,
    'min_intensity': float,
    'max_intensity': float,
    'processable': True,
}
```

### 3.2 Close-Scan Image

```python
image: np.ndarray  # (ny, nx, 4) — channels: [x, y, z, fluorescence]
```

- Channel 3 (`image[:, :, 3]`) is the fluorescence count rate (Hz / counts·s⁻¹)
- Typical close-scan dimensions: 150–200 × 150–200 pixels
- FOV: 30–60 µm (pixel size: 0.15–0.35 µm/px)

### 3.3 Physical Coordinates

```python
x_coords: np.ndarray  # 1-D, length nx — X positions in metres
y_coords: np.ndarray  # 1-D, length ny — Y positions in metres
z_current: float       # Current Z focus plane in metres
```

### 3.4 Context (from ScanRegion)

```python
scan_region: ScanRegion  # parent region from ScanRegionQueue
    .region_id: str
    .bbox_physical: (x_min, x_max, y_min, y_max)  # metres
    .peak_intensity: float
    .mean_intensity: float
```

---

## 4. Output Specification

### 4.1 POICandidate Data Structure

```python
class POICandidate:
    """A scored, narrowed-down NV centre candidate ready for optimization."""
    
    # Identity
    candidate_id: str          # e.g. 'POI-a1b2c3'
    region_id: str             # parent ScanRegion ID
    
    # Position (physical)
    x: float                   # metres (sub-pixel refined)
    y: float                   # metres (sub-pixel refined)
    z_estimate: float          # metres (current Z plane)
    
    # Position (pixel)
    pixel_row: int
    pixel_col: int
    
    # Raw detection metrics
    intensity: float           # peak fluorescence (counts/s)
    snr: float                 # signal-to-noise ratio
    circularity: float         # spot shape score [0, 1]
    fit_quality: float         # sub-pixel refinement quality [0, 1]
    contrast: float            # peak / local background ratio
    
    # Composite scores (from Stage B)
    detection_confidence: float   # weighted composite [0, 1]
    isolation_score: float        # how isolated from other candidates [0, 1]
    zone_consistency: float       # intensity consistent with zone stats [0, 1]
    
    # Overall ranking
    overall_score: float       # final composite for ranking [0, 1]
    rank: int                  # 1 = highest confidence
    
    # Classification
    classification: str        # 'strong_candidate' | 'marginal' | 'rejected'
    rejection_reason: str      # if classified as 'rejected'
    
    # Metadata
    extraction_method: str     # 'cip_masked' | 'adaptive_threshold' | etc.

    # Hardware Calibration (Temporary Test Shift)
    x_shift: float             # applied temporary hardware shift in metres (-X/20)
    x_uncalibrated: float      # original unshifted physical X position
    x_range: float             # parent Cell Region physical X range
```

### 4.2 POIExtractionResult

```python
class POIExtractionResult:
    """Complete output of the POIExtractor pipeline."""
    
    # Candidates
    candidates: List[POICandidate]        # ALL detected (before narrowing)
    strong_candidates: List[POICandidate] # Narrowed subset (for optimizer)
    marginal_candidates: List[POICandidate] # Lower-confidence (kept for review)
    rejected_candidates: List[POICandidate] # Failed filtering (with reasons)
    
    # Statistics
    stats: dict
        # 'total_detected': int — raw CIP detections in processable zone
        # 'n_strong': int — high-confidence candidates
        # 'n_marginal': int — lower-confidence but plausible
        # 'n_rejected': int — filtered out
        # 'detection_density_per_um2': float
        # 'mean_score': float — average overall_score of strong candidates
        # 'zone_coverage': float — fraction of processable zone near candidates
        
    # Diagnostics
    diagnostics: dict
        # 'noise_sigma': float — estimated noise in processable zone
        # 'threshold_used': float — actual detection threshold
        # 'background_method': str
        # 'narrowing_method': str — which narrowing strategy was applied
        # 'score_threshold': float — Otsu/percentile threshold on scores
        # 'processing_time_s': float
```

---

## 5. Algorithm Design

### Stage A: CIP Detection within Processable Zone

This stage applies the existing CIP pipeline (from `ConfocalImageAnalysis`) but
**constrained to the processable mask**.

```python
def _detect_in_processable_zone(self, image, processable_mask, 
                                 x_coords, y_coords, z_current):
    """Run CIP detection ONLY within the processable zone."""
    
    fluor = image[:, :, 3].astype(float)
    
    # CRITICAL: Apply processable mask to restrict detection
    # Option A: Zero out non-processable pixels before CIP
    masked_fluor = fluor.copy()
    masked_fluor[~processable_mask] = 0.0
    
    # Option B (preferred): Run CIP on full image, then filter results
    # This avoids edge artefacts from zeroing at mask boundaries
    # → Run CIP on full fluorescence image
    # → Post-filter: discard candidates outside processable_mask
    
    # ... CIP stages 1-9 ...
    # ... filter: keep only candidates where processable_mask[row, col] = True
```

**Masked CIP vs. Mask-Post-Filtering**: Running CIP on the full image and
post-filtering is preferred.  Zeroing non-processable pixels creates
artificial intensity edges that generate false local maxima at the mask
boundary.  The post-filter approach avoids this.

#### A.1 Adaptive Background Within Zone

For close-scan images, the standard background estimation (large-kernel median
filter) may not account for the intensity gradient *within* a cell.  The
processable zone often has a radial intensity gradient from the cell centre
outward.

```python
# Standard: global background estimation
background = median_filter(fluor, size=background_filter_size)

# Enhanced: zone-aware background
# Compute background stats within processable zone only
zone_values = fluor[processable_mask]
zone_median = np.median(zone_values)
zone_mad = np.median(np.abs(zone_values - zone_median))
zone_sigma = 1.4826 * zone_mad
```

#### A.2 Zone-Adaptive Threshold

The threshold should be computed from processable-zone statistics, not from
the full image (which includes dark substrate and bright clusters):

```python
# Standard (from full image): may over/under-threshold
noise_sigma = cip.estimate_noise_level(corrected)
threshold = detection_threshold_sigma * noise_sigma

# Zone-adaptive (preferred):
zone_corrected = corrected[processable_mask]
zone_noise = 1.4826 * np.median(np.abs(zone_corrected - np.median(zone_corrected)))
zone_threshold = detection_threshold_sigma * zone_noise

# Also enforce minimum SNR relative to zone statistics
min_threshold = zone_stats['median_intensity'] + 2.0 * zone_noise
threshold = max(zone_threshold, min_threshold)
```

Using zone-specific statistics prevents the following failure modes:
- **Bright clusters dominating noise estimate** → threshold too high → misses real NVs
- **Dark substrate dominating noise estimate** → threshold too low → false positives
- **Cell with very few NVs** → noise estimate unstable → use zone median as floor

#### A.3 Local Maxima Restricted to Zone

```python
maxima_positions = cip.detect_local_maxima(corrected, mask, neighborhood_size)

# Post-filter: keep only maxima within processable zone
zone_maxima = []
for pos in maxima_positions:
    row, col = int(pos[0]), int(pos[1])
    if processable_mask[row, col]:
        zone_maxima.append(pos)
```

---

### Stage B: Multi-Metric Candidate Scoring

Each candidate that survives CIP detection is scored on multiple independent
quality axes.  This is the core innovation over the existing pipeline, which
only ranks by raw intensity.

#### B.1 Signal-to-Noise Ratio (SNR)

```python
snr = (candidate_intensity - zone_median) / zone_noise_sigma
snr_score = min(1.0, max(0.0, snr / 20.0))  # saturates at SNR=20
```

#### B.2 Circularity (from CIP Stage 6)

Already computed by `ConfocalImageAnalysis.validate_spot_shape()`:

```python
_, circularity = cip.validate_spot_shape(corrected, row, col, radius)
shape_score = max(0.0, min(1.0, circularity))
```

#### B.3 Intensity Contrast

Peak intensity relative to the local background (annular region):

```python
contrast = cip.compute_intensity_contrast(corrected, row, col, radius)
# For a broad diffraction-limited Gaussian (sigma ~2px) at radius=2, the border 
# intensity is ~40-60% of peak, so expected contrast is ~1.5 - 2.5.
# Very high contrast (> 5.0) often indicates a sharp hot pixel artefact.
if contrast < 1.1:
    contrast_score = 0.0
elif contrast > 1.5:
    contrast_score = 1.0
else:
    contrast_score = (contrast - 1.1) / 0.4
```

#### B.4 Sub-Pixel Fit Quality

From the centre-of-mass / Gaussian refinement:

```python
refined = cip.refine_position_gaussian_2d(corrected, row, col, radius,
                                           x_coords, y_coords)
# The simple fit metric evaluates to ~0.15-0.3 for broad NVs because 
# patch edges are still bright. We map this physical reality to [0, 1].
if refined['quality'] < 0.1:
    fit_score = 0.0
elif refined['quality'] > 0.4:
    fit_score = 1.0
else:
    fit_score = (refined['quality'] - 0.1) / 0.3
```

#### B.5 Isolation Score (NEW)

How isolated is this candidate from other candidates?  Isolated NVs are more
likely to be single emitters (not cluster fragments).

```python
def _compute_isolation_score(self, candidate_pos, all_positions, 
                              spot_diameter_px):
    """Score how isolated a candidate is from its neighbors."""
    distances = cdist([candidate_pos], all_positions)[0]
    distances = distances[distances > 0]  # exclude self
    
    if len(distances) == 0:
        return 1.0  # perfectly isolated
    
    nearest = np.min(distances)
    # Score: 0 if touching (distance < spot_diameter), 1 if well-separated
    isolation = min(1.0, max(0.0, 
        (nearest - spot_diameter_px) / (3 * spot_diameter_px)))
    return isolation
```

#### B.6 Zone Consistency Score (NEW)

Is this candidate's intensity consistent with what we expect for a single NV
in this particular cell's processable zone?

```python
def _compute_zone_consistency(self, raw_candidate_intensity, zone_stats):
    """Score consistency with the zone's intensity distribution."""
    zone_median = zone_stats['median_intensity']
    zone_std = zone_stats['std_intensity']
    
    # Compare RAW candidate intensity to RAW zone stats.
    # NVs can be extremely bright compared to the zone's standard deviation.
    if zone_std <= 0:
        return 0.5
    
    z_score = (raw_candidate_intensity - zone_median) / zone_std
    
    if z_score < 1.5:
        return 0.2   # too dim — probably background fluctuation
    elif z_score < 3.0:
        return 0.6   # marginal
    elif z_score <= 30.0:
        return 1.0   # ideal range for single NV
    elif z_score <= 100.0:
        return 0.8   # very bright
    else:
        return 0.5   # extremely bright, maybe unresolved cluster fragment
```

#### B.7 Composite Scoring

```python
overall_score = (
    w_snr * snr_score +
    w_shape * shape_score +
    w_contrast * contrast_score +
    w_fit * fit_score +
    w_isolation * isolation_score +
    w_consistency * consistency_score
)

# Default weights:
# w_snr = 0.25, w_shape = 0.15, w_contrast = 0.20,
# w_fit = 0.10, w_isolation = 0.15, w_consistency = 0.15
```

---

### Stage C: Adaptive Narrowing

This is the core challenge: **how to dynamically narrow N candidates
(where N varies from 3 to 50+) to a smaller, high-confidence set.**

#### C.1 Why Fixed Thresholds Fail

Different cells have different NV densities:
- A densely-labelled cell might have 40 bright spots in the processable zone
- A sparsely-labelled cell might have only 3
- Some cells have no NVs at all (only background fluctuations)

A fixed threshold (e.g. "keep top 10") would:
- Under-report on dense cells (miss real NVs)
- Over-report on sparse cells (promote noise to "candidate" status)

#### C.2 Adaptive Narrowing Strategy

POIExtractor uses a **multi-gate adaptive narrowing** approach:

```python
def _narrow_candidates(self, candidates, zone_stats):
    """
    Dynamically narrow candidates using adaptive gates.
    
    Strategy:
    1. Score-based classification (strong / marginal / rejected)
    2. Statistical outlier detection (Otsu on score distribution)
    3. Density-aware capping
    4. Spatial deconfliction
    """
```

##### Gate 1: Absolute Quality Floor

Remove candidates that fail minimum quality standards regardless of other
candidates:

```python
# Minimum acceptable criteria (non-negotiable)
MIN_SNR = 3.0                    # Must be 3σ above zone noise
MIN_CIRCULARITY = 0.4            # Must be somewhat round
MIN_OVERALL_SCORE = 0.25         # Composite score floor
```

##### Gate 2: Statistical Score Separation (Otsu's Method on Scores)

Use Otsu's thresholding on the distribution of `overall_score` values to
find a natural break point between strong and marginal candidates:

```python
from skimage.filters import threshold_otsu

scores = np.array([c.overall_score for c in surviving_candidates])

if len(scores) >= 4:  # Need enough samples for Otsu
    try:
        score_threshold = threshold_otsu(scores)
    except ValueError:
        # Fallback: use median as threshold
        score_threshold = np.median(scores)
    
    strong = [c for c in candidates if c.overall_score >= score_threshold]
    marginal = [c for c in candidates if c.overall_score < score_threshold]
else:
    # Too few candidates — keep all as strong
    strong = candidates
    marginal = []
```

**Why Otsu on scores?** Otsu's method finds the threshold that minimises
intra-class variance.  If there's a natural gap between "real NV" scores and
"noise/artifact" scores, Otsu will find it automatically.  This is the same
principle used for cell/substrate separation in the upstream segmentation.

##### Gate 3: Density-Aware Capping

Even after Otsu filtering, very dense cells may yield too many candidates for
efficient optimization.  Apply a density cap:

```python
# Compute detection density
processable_area_um2 = zone_stats['area_px'] * pixel_size_um**2
density = len(strong) / max(processable_area_um2, 1.0)

# Expected single-NV density range: 0.01–0.5 per µm²
MAX_DENSITY = 0.5  # candidates per µm²
MAX_ABSOLUTE = 30  # hard cap on candidates per cell

if len(strong) > MAX_ABSOLUTE:
    strong = sorted(strong, key=lambda c: c.overall_score, reverse=True)
    overflow = strong[MAX_ABSOLUTE:]
    strong = strong[:MAX_ABSOLUTE]
    for c in overflow:
        c.classification = 'marginal'
        c.rejection_reason = f'density_cap (>{MAX_ABSOLUTE})'
    marginal.extend(overflow)
```

##### Gate 4: Spatial Deconfliction (Stage D)

If two candidates are within `min_separation` of each other, keep only the
higher-scoring one:

```python
def _spatial_deconflict(self, candidates, min_separation_px):
    """Remove spatially redundant candidates (keep highest score)."""
    candidates = sorted(candidates, key=lambda c: c.overall_score, reverse=True)
    kept = []
    positions = []
    
    for c in candidates:
        pos = np.array([c.pixel_row, c.pixel_col])
        if len(positions) == 0:
            kept.append(c)
            positions.append(pos)
            continue
        
        dists = cdist([pos], np.array(positions))[0]
        if np.min(dists) >= min_separation_px:
            kept.append(c)
            positions.append(pos)
        else:
            c.classification = 'rejected'
            c.rejection_reason = 'too_close_to_higher_scored_candidate'
    
    return kept
```

#### C.3 Narrowing Decision Tree

```
All CIP detections in processable zone
        │ (N candidates, variable)
        ▼
Gate 1: Absolute quality floor
        │ SNR ≥ 3, circularity ≥ 0.4, score ≥ 0.25
        │ → reject obvious noise
        ▼
Gate 2: Otsu score separation
        │ Natural break in score distribution
        │ → classify as strong / marginal
        ▼
Gate 3: Density cap
        │ Max 30 per cell, max 0.5 per µm²
        │ → demote excess to marginal
        ▼
Gate 4: Spatial deconfliction
        │ Min separation ≥ spot_diameter
        │ → keep highest-score if overlap
        ▼
Final: strong_candidates (→ Optimizer)
       marginal_candidates (→ review)
       rejected_candidates (→ log)
```

---

### Stage E: Ranking & Output

Strong candidates are ranked by `overall_score` (descending).  The output
`POIExtractionResult` contains all three categories with full metadata.

```python
# Final ranking
strong_candidates.sort(key=lambda c: c.overall_score, reverse=True)
for rank, c in enumerate(strong_candidates, 1):
    c.rank = rank
    c.classification = 'strong_candidate'
```

---

## 6. Handling Edge Cases

### 6.1 Cells with No NVs

If CIP finds 0 candidates in the processable zone:
- Return an empty `POIExtractionResult` with `stats['total_detected'] = 0`
- Set `stats['reason'] = 'no_detections_in_processable_zone'`
- The region is marked as processed (not failed) — absence of NVs is valid

### 6.2 Cells with Only Bright Clusters (no processable zone)

If `CellProcessingResult.zone_stats['processable'] == False`:
- POIExtractor should short-circuit and return empty result
- Optionally: log the bright cluster locations from `bright_cluster_stats`
  for potential future sub-zoom analysis

### 6.3 Very Dense NV Distributions

If Gate 3 triggers (>30 candidates surviving Otsu):
- Cap at 30 and demote rest to marginal
- Log warning: "Dense NV distribution — consider sub-region zooming"
- The verifier can process all 30 sequentially

### 6.4 Highly Variable Noise Across the Zone

If the processable zone has strong intensity gradients:
- Use a local noise estimate per candidate (annular background)
- Fall back to contrast-based scoring instead of global SNR

### 6.5 Candidates Near Zone Boundaries

Candidates within `spot_diameter / 2` of the processable mask edge:
- Flag with `edge_candidate = True`
- Reduce their overall_score by 20% (edge artifacts more likely)
- Let the Optimizer handle final validation

---

## 7. Configuration Parameters

### 7.1 Detection Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `detection_threshold_sigma` | 5.0 | 3.0–10.0 | CIP threshold in MAD-sigma units |
| `min_spot_intensity` | 1000 | 0–50000 | Absolute minimum fluorescence (Hz) |
| `spot_diameter_m` | 1.5e-6 | 0.5e-6–5e-6 | Expected NV PSF diameter (metres) |
| `background_filter_size` | 15 | 7–31 | Median filter kernel for background |
| `use_zone_adaptive_threshold` | True | — | Use processable-zone stats for threshold |
| `max_candidates` | 50 | 10–200 | Hard cap on raw CIP detections |

### 7.2 Scoring Weights

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `w_snr` | 0.25 | 0.0–1.0 | Weight for SNR score |
| `w_shape` | 0.15 | 0.0–1.0 | Weight for circularity |
| `w_contrast` | 0.20 | 0.0–1.0 | Weight for intensity contrast |
| `w_fit` | 0.10 | 0.0–1.0 | Weight for Gaussian fit quality |
| `w_isolation` | 0.15 | 0.0–1.0 | Weight for spatial isolation |
| `w_consistency` | 0.15 | 0.0–1.0 | Weight for zone consistency |

### 7.3 Narrowing Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `min_snr` | 3.0 | 1.0–10.0 | Gate 1: minimum SNR |
| `min_circularity` | 0.4 | 0.2–0.8 | Gate 1: minimum shape score |
| `min_overall_score` | 0.25 | 0.1–0.5 | Gate 1: composite score floor |
| `narrowing_method` | 'otsu' | 'otsu' / 'percentile' / 'fixed' | Gate 2: classification method |
| `percentile_threshold` | 50 | 25–75 | Used when `narrowing_method='percentile'` |
| `fixed_score_threshold` | 0.5 | 0.2–0.8 | Used when `narrowing_method='fixed'` |
| `max_strong_per_cell` | 30 | 5–100 | Gate 3: density cap |
| `max_density_per_um2` | 0.5 | 0.1–2.0 | Gate 3: area-based density cap |
| `min_separation_factor` | 1.0 | 0.5–3.0 | Gate 4: × spot_diameter for deconfliction |
| `edge_penalty` | 0.2 | 0.0–0.5 | Score reduction for edge candidates |

---

## 8. Class Interface Design

```python
class POIExtractor:
    """
    Extracts and narrows down NV centre POI candidates from a
    CellRegionProcessor result.
    
    Typical usage::
    
        extractor = POIExtractor()
        result = extractor.extract(
            cell_result=cell_processing_result,
            image=close_scan_image,
            x_coords=ux,
            y_coords=uy,
            z_current=z,
        )
        
        # Feed strong candidates to the verifier:
        for candidate in result.strong_candidates:
            verifier.verify(candidate)
    """
    
    def __init__(self, **config):
        """Initialize with optional configuration overrides."""
    
    def extract(self, cell_result, image, x_coords, y_coords,
                z_current, scan_region=None, **kwargs) -> POIExtractionResult:
        """
        Run the full POI extraction pipeline.
        
        Parameters
        ----------
        cell_result : CellProcessingResult
            Output of CellRegionProcessor.process().
        image : np.ndarray
            Close-scan image (ny, nx, 4).
        x_coords : np.ndarray
            1-D X coordinates (metres).
        y_coords : np.ndarray
            1-D Y coordinates (metres).
        z_current : float
            Current Z focus plane (metres).
        scan_region : ScanRegion, optional
            Parent ScanRegion for metadata.
        **kwargs
            Override any configuration parameter for this run.
        
        Returns
        -------
        POIExtractionResult
        """
    
    # --- Internal stages ---
    def _detect_in_zone(self, fluor, processable_mask, 
                         x_coords, y_coords, z_current):
        """Stage A: CIP detection restricted to processable zone."""
    
    def _score_candidates(self, raw_candidates, zone_stats, 
                           fluor, processable_mask):
        """Stage B: Multi-metric scoring."""
    
    def _narrow_candidates(self, scored_candidates, zone_stats):
        """Stage C: Adaptive narrowing with multi-gate filtering."""
    
    def _spatial_deconflict(self, candidates, min_separation_px):
        """Stage D: Remove spatially redundant candidates."""
    
    def _build_result(self, strong, marginal, rejected, diagnostics):
        """Stage E: Assemble POIExtractionResult."""
    
    # --- Visualization ---
    def get_candidate_overlay(self, result, image_shape):
        """Generate RGBA overlay showing candidate classifications.
        
        Returns (ny, nx, 4) RGBA float array:
        - Green circles: strong candidates
        - Yellow circles: marginal candidates  
        - Red × marks: rejected candidates
        """
```

---

## 9. Data Flow Diagram

```
close_scan_image (ny×nx×4)
        │
        │   CellProcessingResult
        │         │
        ▼         ▼
┌─────────────────────────────────────────────────────────┐
│                    POIExtractor.extract()                │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Stage A: CIP Detection                          │   │
│  │                                                  │   │
│  │  1. Extract fluorescence channel                 │   │
│  │  2. Background estimation (global)               │   │
│  │  3. Background subtraction                       │   │
│  │  4. Zone-adaptive noise estimation               │   │
│  │  5. Zone-adaptive thresholding                   │   │
│  │  6. Local maxima detection                       │   │
│  │  7. Post-filter: keep only within processable    │   │
│  │  8. Shape validation                             │   │
│  │  9. Spatial clustering (brightest-first)         │   │
│  │  10. Sub-pixel Gaussian refinement               │   │
│  │                                                  │   │
│  │  Output: N raw candidates (variable count)       │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │  Stage B: Multi-Metric Scoring                   │   │
│  │                                                  │   │
│  │  For each candidate:                             │   │
│  │    • SNR score (vs zone noise)                   │   │
│  │    • Shape/circularity score                     │   │
│  │    • Contrast score (peak / local bg)            │   │
│  │    • Fit quality score                           │   │
│  │    • Isolation score (distance to nearest)       │   │
│  │    • Zone consistency score                      │   │
│  │    → overall_score (weighted composite)          │   │
│  │                                                  │   │
│  │  Output: N scored candidates                     │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │  Stage C: Adaptive Narrowing                     │   │
│  │                                                  │   │
│  │  Gate 1: Absolute quality floor                  │   │
│  │  Gate 2: Otsu on score distribution              │   │
│  │  Gate 3: Density cap (per cell + per µm²)        │   │
│  │                                                  │   │
│  │  Output: strong + marginal + rejected            │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │  Stage D: Spatial Deconfliction                  │   │
│  │                                                  │   │
│  │  Remove overlapping candidates (keep best score) │   │
│  │                                                  │   │
│  │  Output: deconflicted strong candidates          │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │  Stage E: Ranking & Result Assembly              │   │
│  │                                                  │   │
│  │  Sort by overall_score, assign ranks             │   │
│  │  Build POIExtractionResult                       │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
└─────────────────────────┼───────────────────────────────┘
                          │
                          ▼
              POIExtractionResult
              ├── strong_candidates  → NVCandidateVerifier
              ├── marginal_candidates → review / log
              └── rejected_candidates → diagnostics
```

---

## 10. Integration with Downstream: NVCandidateVerifier (Preview)

The **NVCandidateVerifier** (to be fully documented in doc 21) will:

1. **Accept** `POIExtractionResult.strong_candidates` as input
2. **Sequentially** call `OptimizerLogic.start_refocus()` at each candidate position
3. **Gate** on optimizer fit quality (R², displacement)
4. **Optionally** run ODMR at confirmed positions
5. **Register** verified NVs as POIs in `PoiManagerLogic`

```
POIExtractor.extract()
        │
        ▼  POIExtractionResult.strong_candidates
NVCandidateVerifier.verify_batch(candidates)
        │
        ├── for each candidate:
        │       OptimizerLogic.start_refocus(x, y, z)
        │       wait for sigRefocusFinished
        │       check fit R² ≥ min_quality
        │       check displacement < max_displacement
        │       → accept / reject
        │
        │   for each accepted:
        │       optional: ODMRLogic.quick_sweep() → confirm 2.87 GHz dip
        │       PoiManagerLogic.add_poi(position, name='NV_XXX')
        │
        ▼  List[VerifiedNV] + statistics
```

---

## 11. Test Plan

### 11.1 Unit Tests

| Test | Input | Expected |
|------|-------|----------|
| `test_empty_processable_zone` | Empty processable mask | 0 candidates, `reason='no_detections'` |
| `test_single_synthetic_nv` | One bright Gaussian spot in zone | 1 strong candidate near true position |
| `test_multiple_synthetic_nvs` | 5 spots at known positions | 5 strong candidates, positions within 2 px |
| `test_noise_rejection` | Zone with noise but no NVs | 0 strong candidates |
| `test_cluster_fragment_rejection` | Zone with 1 very broad bright spot | 0 strong (failed circularity) |
| `test_edge_candidate_penalty` | NV at processable zone edge | Reduced score, flagged as edge |
| `test_scoring_weights` | Various weight combinations | Score order changes appropriately |
| `test_otsu_separation` | Bimodal score distribution | Natural split into strong/marginal |
| `test_density_cap` | 50 candidates in small zone | Max 30 strong, rest marginal |
| `test_spatial_deconfliction` | Two candidates within spot_diameter | Only higher-score survives |

### 11.2 Integration Tests (Real Close-Scan Data)

| Test | Data | Validation |
|------|------|------------|
| `test_real_close_scan_17` | `Confocal2/20260706-1701-46` (~30×39 µm) | Candidates in processable zone only |
| `test_real_close_scan_24` | `Confocal2/20260706-1724-08` (~44×55 µm) | Reasonable candidate count (3-20) |
| `test_real_close_scan_33` | `Confocal2/20260706-1833-28` (~53×73 µm) | No candidates outside zone |

### 11.3 Visual Tests

Generate overlay PNGs showing:
- Processable zone (green transparency)
- Strong candidates (green circles)
- Marginal candidates (yellow circles)
- Rejected candidates (red × marks)
- Nucleus (blue transparency)
- Bright clusters (red transparency)

Save to `tests/output_visuals/poi_extractor_output.png`.

---

## 12. File Location & Naming

| File | Purpose |
|------|---------|
| `logic/poi_extractor.py` | Main module |
| `tests/test_poi_extractor.py` | Unit + integration tests |
| `documentation/automation/20_poi_extractor_module.md` | This document |
| `documentation/automation/21_nv_candidate_verifier.md` | Downstream module (to be created) |

---

## 13. Open Questions

### Q1: Should POIExtractor be a Qudi Logic Module?

Currently `CellRegionProcessor` is a plain Python class (not a Qudi `GenericLogic`
subclass).  Should `POIExtractor` follow the same pattern, or should it be a
full Qudi Logic module with `StatusVar` parameters and connectors?

**Recommendation**: Start as a plain class (like CellRegionProcessor), then
promote to Logic module in Phase 3 when live integration requires it.

### Q2: Scoring Weight Optimization

The default scoring weights (w_snr=0.25, w_shape=0.15, etc.) are initial
estimates.  Should we build a weight optimization script that tunes weights
against labelled ground-truth data?

**Recommendation**: Yes, in Phase 2 — but only after we have ≥3 annotated
close-scan images with manually-identified NV positions.

### Q3: Marginal Candidate Handling

Should marginal candidates be passed to the Optimizer at all?  Options:
- **A)** Only strong → Optimizer (conservative, saves time)
- **B)** Strong first, then marginal if time permits (balanced)
- **C)** All surviving → Optimizer (thorough but slow)

**Recommendation**: Option B — process strong candidates first; if `max_time`
budget remains, also process marginals.

### Q4: Bright Cluster Sub-Analysis

Should POIExtractor also attempt to extract candidates from the bright
cluster regions (which CellRegionProcessor excluded)?  Some bright clusters
may contain resolvable NVs at their edges.

**Recommendation**: Not in v1.  Add as optional mode in a future iteration
if ODMR-confirmed NVs are found at cluster edges in real experiments.

---

## 14. Summary

| Aspect | Detail |
|--------|--------|
| **Module** | `POIExtractor` |
| **Location** | `logic/poi_extractor.py` |
| **Input** | `CellProcessingResult` + close-scan image |
| **Output** | `POIExtractionResult` (strong + marginal + rejected candidates) |
| **Key Innovation** | Multi-metric scoring + adaptive Otsu-based narrowing |
| **Handles Variable NV Density** | Yes — Otsu + density cap adapts automatically |
| **Downstream Consumer** | `NVCandidateVerifier` (wraps Optimizer + ODMR) |
| **Current Status** | Design complete — ready for implementation |
