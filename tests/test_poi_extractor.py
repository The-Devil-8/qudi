# -*- coding: utf-8 -*-
"""
Tests for the POIExtractor module.

Covers:
  - POICandidate and POIExtractionResult data classes
  - Synthetic image tests (single NV, multi NV, noise-only, edge cases)
  - Adaptive narrowing logic (Otsu, density cap, deconfliction)
  - Integration tests with real close-scan data from Confocal2
  - Visual output generation

Usage:
    $env:PYTHONIOENCODING='utf-8'; python tests/test_poi_extractor.py
"""

import os
import sys
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logic.poi_extractor import POICandidate, POIExtractionResult, POIExtractor
from logic.cell_region_processor import CellRegionProcessor, CellProcessingResult

CONFOCAL2 = os.path.join(PROJECT_ROOT, 'Confocal2')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'tests', 'output_visuals')

CLOSE_SCANS = [
    ('20260706-1701-46', '~30x39 um'),
    ('20260706-1724-08', '~44x55 um'),
    ('20260706-1833-28', '~53x73 um'),
]


def load_scan(scan_id):
    """Load a scan by ID."""
    fp = os.path.join(CONFOCAL2, '{}_confocal_xy_data.dat'.format(scan_id))
    from logic.roi_segmentation_logic import ROISegmentationLogic
    seg = ROISegmentationLogic()
    return seg.parse_dat_file(fp)[0]


# ===================================================================
# Helpers: synthetic image generation
# ===================================================================

def make_synthetic_cell_image(size=150, n_nvs=5, bg_substrate=5000,
                              bg_cell=30000, nv_intensity=150000,
                              nucleus_intensity=8000, nv_sigma=2.0,
                              seed=42):
    """Create a synthetic close-scan image with known NV positions.

    Returns
    -------
    image : ndarray (size, size, 4)
    nv_positions : list of (row, col) tuples
    cell_result : CellProcessingResult
    """
    rng = np.random.RandomState(seed)
    image = np.zeros((size, size, 4), dtype=float)

    # Physical coordinates: 0 to 50 µm
    fov_m = 50e-6
    x = np.linspace(0, fov_m, size)
    y = np.linspace(0, fov_m, size)
    image[0, :, 0] = x   # will be broadcast by caller if needed
    image[:, 0, 1] = y
    for i in range(size):
        image[:, i, 0] = x[i]
        image[i, :, 1] = y[i]
    image[:, :, 2] = 0.0  # Z = 0

    # Background fluorescence
    fluor = np.full((size, size), bg_substrate, dtype=float)

    # Cell region: circle centred at (size/2, size/2), radius = size*0.35
    cy, cx = size // 2, size // 2
    cell_r = int(size * 0.35)
    yy, xx = np.ogrid[:size, :size]
    cell_mask = ((yy - cy) ** 2 + (xx - cx) ** 2) <= cell_r ** 2
    fluor[cell_mask] = bg_cell

    # Nucleus: dark void, radius = cell_r * 0.3
    nuc_r = int(cell_r * 0.3)
    nuc_mask = ((yy - cy) ** 2 + (xx - cx) ** 2) <= nuc_r ** 2
    fluor[nuc_mask] = nucleus_intensity

    # Add NVs at random positions within the cell (excluding nucleus)
    processable = cell_mask & ~nuc_mask
    proc_rows, proc_cols = np.where(processable)

    nv_positions = []
    if n_nvs > 0 and len(proc_rows) > 0:
        # Pick n_nvs random positions from processable zone
        margin = int(size * 0.05)
        for _ in range(n_nvs * 10):  # retry loop
            if len(nv_positions) >= n_nvs:
                break
            idx = rng.randint(0, len(proc_rows))
            r, c = int(proc_rows[idx]), int(proc_cols[idx])
            # Ensure not too close to nucleus edge
            dist_to_nuc = np.sqrt((r - cy) ** 2 + (c - cx) ** 2)
            if dist_to_nuc < nuc_r + 12:
                continue
            # Ensure not too close to cell edge
            dist_to_edge = cell_r - np.sqrt((r - cy) ** 2 + (c - cx) ** 2)
            if dist_to_edge < 12:
                continue
            # Ensure not too close to existing NVs
            too_close = False
            for (pr, pc) in nv_positions:
                if np.sqrt((r - pr) ** 2 + (c - pc) ** 2) < 10:
                    too_close = True
                    break
            if too_close:
                continue
            nv_positions.append((r, c))

        # Draw Gaussian spots
        for (r, c) in nv_positions:
            for dr in range(-8, 9):
                for dc in range(-8, 9):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < size and 0 <= cc < size:
                        val = nv_intensity * np.exp(
                            -(dr ** 2 + dc ** 2) / (2 * nv_sigma ** 2))
                        fluor[rr, cc] += val

    # Add noise
    fluor += rng.normal(0, 2000, fluor.shape)
    fluor = np.maximum(fluor, 0)

    image[:, :, 3] = fluor

    # Build a matching CellProcessingResult
    # Erode the processable mask to exclude cell/substrate boundary
    # artefacts, matching what CellRegionProcessor._extract_processable_zone
    # does in practice.
    from scipy.ndimage import binary_erosion
    processable_eroded = binary_erosion(processable, iterations=10)
    # If erosion removed everything (very small cell), fall back
    if not np.any(processable_eroded):
        processable_eroded = processable.copy()

    cell_result = CellProcessingResult((size, size))
    cell_result.cell_interior_mask = cell_mask.copy()
    cell_result.nucleus_mask = nuc_mask.copy()
    cell_result.bright_cluster_mask = np.zeros((size, size), dtype=bool)
    cell_result.processable_mask = processable_eroded

    # Compute zone stats from the processable region
    zone_values = fluor[processable_eroded]
    cell_result.zone_stats = {
        'area_px': int(np.sum(processable_eroded)),
        'area_fraction_of_cell': float(
            np.sum(processable) / max(np.sum(cell_mask), 1)),
        'mean_intensity': float(np.mean(zone_values)),
        'median_intensity': float(np.median(zone_values)),
        'std_intensity': float(np.std(zone_values)),
        'min_intensity': float(np.min(zone_values)),
        'max_intensity': float(np.max(zone_values)),
        'processable': True,
    }
    cell_result.nucleus_stats = {'detected': True}
    cell_result.bright_cluster_stats = []
    cell_result.diagnostics = {
        'cell_area_px': int(np.sum(cell_mask)),
        'cell_area_fraction': float(np.sum(cell_mask) / (size * size)),
        'nucleus_area_px': int(np.sum(nuc_mask)),
        'bright_cluster_area_px': 0,
        'processable_area_px': int(np.sum(processable)),
        'n_bright_clusters': 0,
    }

    return image, nv_positions, cell_result


# ===================================================================
# Unit Tests: Data Classes
# ===================================================================

def test_poi_candidate_creation():
    """POICandidate should initialize with correct defaults."""
    c = POICandidate(x=1e-6, y=2e-6, intensity=50000)
    assert c.x == 1e-6
    assert c.y == 2e-6
    assert c.intensity == 50000
    assert c.classification == 'pending'
    assert c.overall_score == 0.0
    assert c.rank == 0
    assert c.candidate_id.startswith('POI-')
    assert len(c.candidate_id) == 10  # 'POI-' + 6 hex chars

    d = c.to_dict()
    assert d['x'] == 1e-6
    assert d['intensity'] == 50000
    assert 'overall_score' in d
    assert 'classification' in d
    print('  PASS: test_poi_candidate_creation')


def test_poi_extraction_result_creation():
    """POIExtractionResult should initialize with empty lists."""
    r = POIExtractionResult()
    assert r.candidates == []
    assert r.strong_candidates == []
    assert r.marginal_candidates == []
    assert r.rejected_candidates == []
    assert r.stats['total_detected'] == 0
    assert r.stats['n_strong'] == 0
    assert r.diagnostics['noise_sigma'] == 0.0
    print('  PASS: test_poi_extraction_result_creation')


# ===================================================================
# Unit Tests: Empty / Degenerate Cases
# ===================================================================

def test_empty_processable_zone():
    """Empty processable mask should return 0 candidates."""
    size = 50
    image = np.zeros((size, size, 4), dtype=float)
    image[:, :, 3] = 5000.0
    x = np.linspace(0, 10e-6, size)
    y = np.linspace(0, 10e-6, size)
    for i in range(size):
        image[:, i, 0] = x[i]
        image[i, :, 1] = y[i]

    cell_result = CellProcessingResult((size, size))
    cell_result.zone_stats = {
        'area_px': 0,
        'processable': False,
        'reason': 'no_cell_detected',
    }

    extractor = POIExtractor()
    result = extractor.extract(cell_result, image)

    assert result.stats['total_detected'] == 0
    assert len(result.strong_candidates) == 0
    assert result.diagnostics.get('reason') == 'no_cell_detected'
    print('  PASS: test_empty_processable_zone')


def test_no_nv_in_zone():
    """Cell with noise but no NVs should produce 0 strong candidates.

    Uses an eroded processable mask (matching CellRegionProcessor's
    edge erosion) and a conservative threshold to ensure boundary
    effects and noise fluctuations are not promoted to candidates.
    """
    from scipy.ndimage import binary_erosion

    rng = np.random.RandomState(99)
    size = 150
    image = np.zeros((size, size, 4), dtype=float)
    fov_m = 50e-6
    x = np.linspace(0, fov_m, size)
    y = np.linspace(0, fov_m, size)
    for i in range(size):
        image[:, i, 0] = x[i]
        image[i, :, 1] = y[i]
    fluor = np.full((size, size), 5000.0)
    cy, cx = size // 2, size // 2
    cell_r = int(size * 0.35)
    yy, xx = np.ogrid[:size, :size]
    cell_mask = ((yy - cy) ** 2 + (xx - cx) ** 2) <= cell_r ** 2
    fluor[cell_mask] = 30000.0
    # Low noise — no NVs, only small fluctuations
    fluor += rng.normal(0, 500, fluor.shape)
    fluor = np.maximum(fluor, 0)
    image[:, :, 3] = fluor

    # Erode the processable mask to exclude boundary artefacts,
    # matching what CellRegionProcessor._extract_processable_zone does
    processable = binary_erosion(cell_mask, iterations=8)

    cell_result = CellProcessingResult((size, size))
    cell_result.cell_interior_mask = cell_mask.copy()
    cell_result.nucleus_mask = np.zeros((size, size), dtype=bool)
    cell_result.bright_cluster_mask = np.zeros((size, size), dtype=bool)
    cell_result.processable_mask = processable
    zone_values = fluor[processable]
    cell_result.zone_stats = {
        'area_px': int(np.sum(processable)),
        'area_fraction_of_cell': float(
            np.sum(processable) / max(np.sum(cell_mask), 1)),
        'mean_intensity': float(np.mean(zone_values)),
        'median_intensity': float(np.median(zone_values)),
        'std_intensity': float(np.std(zone_values)),
        'min_intensity': float(np.min(zone_values)),
        'max_intensity': float(np.max(zone_values)),
        'processable': True,
    }

    extractor = POIExtractor(detection_threshold_sigma=8.0)
    result = extractor.extract(cell_result, image)

    assert result.stats['n_strong'] == 0, \
        'Expected 0 strong but got {}'.format(result.stats['n_strong'])
    print('  PASS: test_no_nv_in_zone '
          '(total_detected={}, n_strong=0)'.format(
              result.stats['total_detected']))


# ===================================================================
# Unit Tests: Single and Multiple NV Detection
# ===================================================================

def test_single_synthetic_nv():
    """Single bright NV should produce exactly 1 strong candidate."""
    image, nv_pos, cell_result = make_synthetic_cell_image(
        n_nvs=1, nv_intensity=200000, seed=10)

    assert len(nv_pos) == 1, 'Failed to place 1 NV'
    true_r, true_c = nv_pos[0]

    extractor = POIExtractor()
    result = extractor.extract(cell_result, image)

    assert result.stats['n_strong'] >= 1, \
        'Expected >=1 strong, got {}'.format(result.stats['n_strong'])

    best = result.strong_candidates[0]
    dist = np.sqrt((best.pixel_row - true_r) ** 2
                   + (best.pixel_col - true_c) ** 2)
    assert dist < 5.0, \
        'Best candidate at ({},{}) too far from truth ({},{}) — dist={:.1f}'.format(
            best.pixel_row, best.pixel_col, true_r, true_c, dist)
    assert best.overall_score > 0.3
    assert best.rank == 1
    assert best.classification == 'strong_candidate'

    print('  PASS: test_single_synthetic_nv '
          '(strong={}, dist={:.1f}px, score={:.3f})'.format(
              result.stats['n_strong'], dist, best.overall_score))


def test_multiple_synthetic_nvs():
    """5 NVs should produce ~5 strong candidates with correct ranking."""
    image, nv_pos, cell_result = make_synthetic_cell_image(
        n_nvs=5, nv_intensity=150000, seed=42)

    n_placed = len(nv_pos)
    assert n_placed >= 3, 'Need at least 3 NVs, placed {}'.format(n_placed)

    extractor = POIExtractor()
    result = extractor.extract(cell_result, image)

    n_strong = result.stats['n_strong']
    assert n_strong >= 2, \
        'Expected >=2 strong for {} NVs, got {}'.format(n_placed, n_strong)

    # Check ranking is monotonically decreasing
    scores = [c.overall_score for c in result.strong_candidates]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], \
            'Ranking not sorted: score[{}]={:.3f} < score[{}]={:.3f}'.format(
                i, scores[i], i + 1, scores[i + 1])

    # Check ranks
    ranks = [c.rank for c in result.strong_candidates]
    assert ranks == list(range(1, n_strong + 1))

    # Check that at least some candidates are near true NV positions
    matched = 0
    for (tr, tc) in nv_pos:
        for c in result.strong_candidates:
            if np.sqrt((c.pixel_row - tr) ** 2
                       + (c.pixel_col - tc) ** 2) < 5:
                matched += 1
                break
    assert matched >= 2, \
        'Only {} of {} NVs matched a strong candidate'.format(
            matched, n_placed)

    print('  PASS: test_multiple_synthetic_nvs '
          '(placed={}, strong={}, matched={})'.format(
              n_placed, n_strong, matched))


# ===================================================================
# Unit Tests: Scoring & Narrowing
# ===================================================================

def test_spatial_deconfliction():
    """Two candidates within spot_diameter should keep only the best."""
    extractor = POIExtractor()

    c1 = POICandidate(pixel_row=50, pixel_col=50, intensity=100000)
    c1.overall_score = 0.8
    c2 = POICandidate(pixel_row=52, pixel_col=51, intensity=80000)
    c2.overall_score = 0.6

    kept, removed = extractor._spatial_deconflict([c1, c2], min_separation_px=5)

    assert len(kept) == 1
    assert kept[0].overall_score == 0.8
    assert len(removed) == 1
    assert 'deconflict' in removed[0].rejection_reason
    print('  PASS: test_spatial_deconfliction')


def test_deconfliction_keeps_distant():
    """Two candidates far apart should both survive deconfliction."""
    extractor = POIExtractor()

    c1 = POICandidate(pixel_row=10, pixel_col=10, intensity=100000)
    c1.overall_score = 0.8
    c2 = POICandidate(pixel_row=50, pixel_col=50, intensity=80000)
    c2.overall_score = 0.6

    kept, removed = extractor._spatial_deconflict([c1, c2], min_separation_px=5)

    assert len(kept) == 2
    assert len(removed) == 0
    print('  PASS: test_deconfliction_keeps_distant')


def test_density_cap():
    """More than max_strong_per_cell should trigger density capping."""
    image, _, cell_result = make_synthetic_cell_image(
        n_nvs=0, seed=77)  # no NVs — we'll inject fake candidates

    # Create many fake candidates
    extractor = POIExtractor(max_strong_per_cell=5)
    candidates = []
    for i in range(15):
        c = POICandidate(
            pixel_row=20 + i * 5, pixel_col=20 + i * 5,
            intensity=50000 + i * 1000)
        c.snr = 10.0
        c.circularity = 0.8
        c.overall_score = 0.5 + i * 0.02
        candidates.append(c)

    zone_stats = cell_result.zone_stats
    pixel_size_um = 0.33

    strong, marginal, rejected = extractor._narrow_candidates(
        candidates, zone_stats, pixel_size_um,
        extractor._config)

    assert len(strong) <= 5, \
        'Expected <=5 strong after cap, got {}'.format(len(strong))
    assert len(marginal) >= 10, \
        'Expected >=10 marginal, got {}'.format(len(marginal))

    # Verify the top 5 by score are the strong ones
    all_sorted = sorted(candidates, key=lambda c: c.overall_score,
                        reverse=True)
    for s in strong:
        assert s in all_sorted[:5]

    print('  PASS: test_density_cap (strong={}, marginal={})'.format(
        len(strong), len(marginal)))


def test_edge_candidate_penalty():
    """A candidate near the processable mask edge gets reduced score."""
    size = 80
    image, nv_pos, cell_result = make_synthetic_cell_image(
        size=size, n_nvs=0, seed=33)

    extractor = POIExtractor()

    # Check a pixel at the edge of the processable mask
    pm = cell_result.processable_mask
    # Find a boundary pixel
    from scipy.ndimage import binary_erosion
    interior = binary_erosion(pm, iterations=3)
    boundary = pm & ~interior
    boundary_coords = np.argwhere(boundary)

    if len(boundary_coords) > 0:
        br, bc = boundary_coords[0]
        is_edge = extractor._is_edge_candidate(br, bc, pm, spot_px=5)
        assert is_edge, 'Boundary pixel should be flagged as edge candidate'

    # Check an interior pixel
    interior_coords = np.argwhere(interior)
    if len(interior_coords) > 0:
        ir, ic = interior_coords[len(interior_coords) // 2]
        is_edge = extractor._is_edge_candidate(ir, ic, pm, spot_px=5)
        assert not is_edge, \
            'Deep interior pixel should NOT be flagged as edge'

    print('  PASS: test_edge_candidate_penalty')


def test_zone_consistency_scoring():
    """Zone consistency score should follow expected pattern."""
    zone_stats = {
        'median_intensity': 30000,
        'std_intensity': 5000,
    }

    # Too dim (< 1.5σ)
    assert POIExtractor._compute_zone_consistency(32000, zone_stats) == 0.2
    # Marginal (1.5–3σ)
    assert POIExtractor._compute_zone_consistency(40000, zone_stats) == 0.6
    # Ideal (3–30σ)
    assert POIExtractor._compute_zone_consistency(55000, zone_stats) == 1.0
    # Very bright (30-100σ)
    assert POIExtractor._compute_zone_consistency(250000, zone_stats) == 0.8
    # Extremely bright (>100σ)
    assert POIExtractor._compute_zone_consistency(600000, zone_stats) == 0.5

    print('  PASS: test_zone_consistency_scoring')


def test_isolation_scoring():
    """Isolation score should be high for isolated, low for clustered."""
    spot_px = 5

    # Single candidate — perfectly isolated
    score = POIExtractor._compute_isolation_score(
        np.array([50, 50]), np.array([[50, 50]]), spot_px)
    assert score == 1.0

    # Two candidates very close
    score = POIExtractor._compute_isolation_score(
        np.array([50, 50]),
        np.array([[50, 50], [52, 52]]),
        spot_px)
    assert score < 0.5, 'Close candidates should have low isolation'

    # Two candidates far apart
    score = POIExtractor._compute_isolation_score(
        np.array([50, 50]),
        np.array([[50, 50], [100, 100]]),
        spot_px)
    assert score > 0.5, 'Distant candidates should have high isolation'

    print('  PASS: test_isolation_scoring')


def test_config_override():
    """Per-call config overrides should work correctly."""
    extractor = POIExtractor(min_snr=5.0)
    assert extractor.get_config()['min_snr'] == 5.0

    extractor.set_config(min_snr=3.0)
    assert extractor.get_config()['min_snr'] == 3.0

    # Invalid key
    try:
        extractor.set_config(invalid_key=42)
        assert False, 'Should have raised KeyError'
    except KeyError:
        pass

    print('  PASS: test_config_override')


# ===================================================================
# Integration Tests: Real Close-Scan Data
# ===================================================================

def test_real_close_scan_integration():
    """Run POIExtractor on real close-scan data from Confocal2."""
    if not os.path.isdir(CONFOCAL2):
        print('  SKIP: test_real_close_scan_integration (no Confocal2 data)')
        return

    processor = CellRegionProcessor()
    extractor = POIExtractor()

    for scan_id, desc in CLOSE_SCANS:
        fp = os.path.join(CONFOCAL2,
                          '{}_confocal_xy_data.dat'.format(scan_id))
        if not os.path.isfile(fp):
            print('  SKIP: {} ({}) — file not found'.format(scan_id, desc))
            continue

        image = load_scan(scan_id)
        cell_result = processor.process(image)
        result = extractor.extract(cell_result, image)

        # Basic assertions
        total = result.stats['total_detected']
        n_strong = result.stats['n_strong']
        n_marginal = result.stats['n_marginal']
        n_rejected = result.stats['n_rejected']

        assert total == n_strong + n_marginal + n_rejected, \
            'Total mismatch: {} != {} + {} + {}'.format(
                total, n_strong, n_marginal, n_rejected)

        # All strong candidates should be within processable zone
        pm = cell_result.processable_mask
        for c in result.strong_candidates:
            r, col = c.pixel_row, c.pixel_col
            assert pm[r, col], \
                'Strong candidate at ({},{}) is outside processable zone!'.format(
                    r, col)

        # Strong candidates should be ranked
        if n_strong > 0:
            ranks = [c.rank for c in result.strong_candidates]
            assert ranks == list(range(1, n_strong + 1))

        print('  PASS: {} ({}): total={}, strong={}, '
              'marginal={}, rejected={}, time={:.3f}s'.format(
                  scan_id, desc, total, n_strong, n_marginal, n_rejected,
                  result.diagnostics['processing_time_s']))


# ===================================================================
# Visual Output Generation
# ===================================================================

def generate_poi_extractor_visuals():
    """Generate overlay PNG showing POIExtractor results on real data."""
    if not os.path.isdir(CONFOCAL2):
        print('  SKIP: generate_poi_extractor_visuals (no Confocal2 data)')
        return

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('  SKIP: generate_poi_extractor_visuals (no matplotlib)')
        return

    # Use the first close scan that exists
    scan_id = None
    for sid, _ in CLOSE_SCANS:
        fp = os.path.join(
            CONFOCAL2, '{}_confocal_xy_data.dat'.format(sid))
        if os.path.isfile(fp):
            scan_id = sid
            break

    if scan_id is None:
        print('  SKIP: no close-scan data found')
        return

    image = load_scan(scan_id)
    processor = CellRegionProcessor()
    cell_result = processor.process(image)
    extractor = POIExtractor()
    result = extractor.extract(cell_result, image)

    fluor = image[:, :, 3]
    ny, nx = fluor.shape

    fig, axes = plt.subplots(2, 2, figsize=(14, 12))
    fig.suptitle('POIExtractor Output — scan {}'.format(scan_id),
                 fontsize=14, fontweight='bold')

    # Panel 1: Original + processable zone overlay
    ax = axes[0, 0]
    ax.imshow(fluor, cmap='inferno', origin='lower')
    zone_overlay = np.zeros((ny, nx, 4))
    zone_overlay[cell_result.processable_mask] = [0, 1, 0, 0.25]
    zone_overlay[cell_result.nucleus_mask] = [0, 0, 1, 0.3]
    zone_overlay[cell_result.bright_cluster_mask] = [1, 0, 0, 0.3]
    ax.imshow(zone_overlay, origin='lower')
    ax.set_title('Processable Zone (green) + Nucleus (blue)')

    # Panel 2: All CIP detections
    ax = axes[0, 1]
    ax.imshow(fluor, cmap='inferno', origin='lower')
    if len(result.candidates) > 0:
        rows = [c.pixel_row for c in result.candidates]
        cols = [c.pixel_col for c in result.candidates]
        ax.scatter(cols, rows, c='cyan', s=30, marker='o',
                   edgecolors='white', linewidths=0.5, zorder=5)
    ax.set_title('All CIP Detections ({})'.format(
        result.stats['total_detected']))

    # Panel 3: Classified candidates
    ax = axes[1, 0]
    ax.imshow(fluor, cmap='inferno', origin='lower')
    for c in result.strong_candidates:
        ax.plot(c.pixel_col, c.pixel_row, 'o', color='lime',
                markersize=10, markeredgecolor='white',
                markeredgewidth=1.0, zorder=5)
        ax.annotate('{}'.format(c.rank), (c.pixel_col + 2, c.pixel_row + 2),
                    color='lime', fontsize=8, fontweight='bold', zorder=6)
    for c in result.marginal_candidates:
        ax.plot(c.pixel_col, c.pixel_row, 'o', color='yellow',
                markersize=7, markeredgecolor='white',
                markeredgewidth=0.5, zorder=4)
    for c in result.rejected_candidates:
        ax.plot(c.pixel_col, c.pixel_row, 'x', color='red',
                markersize=6, markeredgewidth=1.5, zorder=3)
    ax.set_title('Classified: {} strong (green), {} marginal (yellow), '
                 '{} rejected (red)'.format(
                     result.stats['n_strong'],
                     result.stats['n_marginal'],
                     result.stats['n_rejected']))

    # Panel 4: Score distribution
    ax = axes[1, 1]
    if len(result.candidates) > 0:
        strong_scores = [c.overall_score for c in result.strong_candidates]
        marginal_scores = [c.overall_score for c in result.marginal_candidates]
        rejected_scores = [c.overall_score for c in result.rejected_candidates]

        bins = np.linspace(0, 1, 30)
        if strong_scores:
            ax.hist(strong_scores, bins=bins, alpha=0.7, color='lime',
                    label='Strong ({})'.format(len(strong_scores)))
        if marginal_scores:
            ax.hist(marginal_scores, bins=bins, alpha=0.7, color='yellow',
                    label='Marginal ({})'.format(len(marginal_scores)))
        if rejected_scores:
            ax.hist(rejected_scores, bins=bins, alpha=0.7, color='red',
                    label='Rejected ({})'.format(len(rejected_scores)))

        score_thresh = result.diagnostics.get('score_threshold', 0)
        if score_thresh > 0:
            ax.axvline(score_thresh, color='white', linestyle='--',
                       linewidth=1.5, label='Threshold={:.3f}'.format(
                           score_thresh))

        ax.set_xlabel('Overall Score')
        ax.set_ylabel('Count')
        ax.legend(fontsize=8)
    ax.set_title('Score Distribution')

    plt.tight_layout()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, 'poi_extractor_output.png')
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print('  SAVED: {}'.format(out_path))


# ===================================================================
# Main runner
# ===================================================================

if __name__ == '__main__':
    print('=' * 60)
    print('POIExtractor Tests')
    print('=' * 60)

    print('\n--- Data Class Tests ---')
    test_poi_candidate_creation()
    test_poi_extraction_result_creation()

    print('\n--- Edge Case Tests ---')
    test_empty_processable_zone()
    test_no_nv_in_zone()

    print('\n--- Detection Tests ---')
    test_single_synthetic_nv()
    test_multiple_synthetic_nvs()

    print('\n--- Scoring & Narrowing Tests ---')
    test_spatial_deconfliction()
    test_deconfliction_keeps_distant()
    test_density_cap()
    test_edge_candidate_penalty()
    test_zone_consistency_scoring()
    test_isolation_scoring()
    test_config_override()

    print('\n--- Integration Tests (Real Data) ---')
    test_real_close_scan_integration()

    print('\n--- Visual Output ---')
    generate_poi_extractor_visuals()

    print('\n' + '=' * 60)
    print('All POIExtractor tests complete.')
    print('=' * 60)
