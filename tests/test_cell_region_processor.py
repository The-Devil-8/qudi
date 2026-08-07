# -*- coding: utf-8 -*-
"""
Tests for the CellRegionProcessor module.

Runs against real close-scan data from Confocal2 to validate:
  - Cell interior detection (foreground vs substrate)
  - Nucleus detection (dark void)
  - Bright cluster masking
  - Processable zone extraction
  - Zone statistics

Usage:
    $env:PYTHONIOENCODING='utf-8'; python tests/test_cell_region_processor.py
"""

import os
import sys
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.cell_region_processor import CellRegionProcessor, CellProcessingResult

CONFOCAL2 = os.path.join(PROJECT_ROOT, 'Confocal2')

CLOSE_SCANS = [
    ('20260706-1701-46', '~30x39 um'),
    ('20260706-1724-08', '~44x55 um'),
    ('20260706-1833-28', '~53x73 um'),
]

PARENT_SCANS = [
    ('20260705-1517-07', '200x200 um'),
]


def load_scan(scan_id):
    """Load a scan by ID."""
    fp = os.path.join(CONFOCAL2, f'{scan_id}_confocal_xy_data.dat')
    seg = ROISegmentationLogic()
    return seg.parse_dat_file(fp)


# ===================================================================
# Unit Tests: CellProcessingResult
# ===================================================================

def test_result_creation():
    """Test CellProcessingResult initializes with zeros."""
    r = CellProcessingResult((100, 80))
    assert r.cell_interior_mask.shape == (100, 80)
    assert r.nucleus_mask.shape == (100, 80)
    assert not r.cell_interior_mask.any()
    assert not r.processable_mask.any()
    print('  [PASS] CellProcessingResult creation')


# ===================================================================
# Unit Tests: Synthetic data
# ===================================================================

def test_synthetic_cell():
    """Test processing on a synthetic cell image with known anatomy."""
    ny, nx = 100, 100
    # Build a 4-channel image like parse_dat_file returns
    image = np.zeros((ny, nx, 4))

    # Background (substrate): low intensity
    image[:, :, 3] = 5000.0

    # Cell body: circular region with mid intensity
    yy, xx = np.ogrid[:ny, :nx]
    cell_radius = 35
    cell_dist = np.sqrt((yy - 50)**2 + (xx - 50)**2)
    cell_mask = cell_dist < cell_radius
    image[cell_mask, 3] = 30000.0

    # Nucleus: dark void in centre
    nuc_radius = 12
    nuc_mask = cell_dist < nuc_radius
    image[nuc_mask, 3] = 7000.0

    # Bright clusters: at periphery
    for cx, cy in [(25, 50), (75, 50), (50, 25), (50, 75)]:
        spot_dist = np.sqrt((yy - cy)**2 + (xx - cx)**2)
        spot = (spot_dist < 4) & cell_mask
        image[spot, 3] = 200000.0

    # Add some noise
    noise = np.random.RandomState(42).normal(0, 2000, (ny, nx))
    image[:, :, 3] = np.maximum(image[:, :, 3] + noise, 0)

    # Process default (mask_bright_clusters=False)
    proc = CellRegionProcessor()
    result = proc.process(image)

    # Validate cell detection
    assert result.cell_interior_mask.any(), 'Cell should be detected'
    cell_area = result.cell_interior_mask.sum()
    expected_cell = np.pi * cell_radius**2
    assert abs(cell_area - expected_cell) / expected_cell < 0.3, \
        f'Cell area {cell_area} too far from expected {expected_cell:.0f}'

    # Validate nucleus detection
    assert result.nucleus_stats.get('detected', False), \
        'Nucleus should be detected'
    nuc_area = result.nucleus_mask.sum()
    assert nuc_area > 50, f'Nucleus too small: {nuc_area}'
    print(f'    Nucleus area: {nuc_area} px (expected ~{np.pi * nuc_radius**2:.0f})')

    # Validate bright clusters (detected for diagnostics/stats)
    assert result.bright_cluster_mask.any(), 'Bright clusters should be detected'
    n_clusters = len(result.bright_cluster_stats)
    assert n_clusters >= 2, f'Expected >=2 clusters, got {n_clusters}'
    print(f'    Bright clusters: {n_clusters}')

    # Validate processable zone (contains bright NV spots by default)
    assert result.processable_mask.any(), 'Processable zone should exist'
    zone_area = result.processable_mask.sum()
    print(f'    Processable zone: {zone_area} px')
    assert result.zone_stats['processable'], 'Zone should be marked processable'

    # Zone should NOT overlap with nucleus
    nuc_overlap = (result.processable_mask & result.nucleus_mask).sum()
    assert nuc_overlap == 0, f'Processable zone overlaps nucleus: {nuc_overlap} px'

    # Test with mask_bright_clusters=True (explicit cluster masking)
    result_masked = proc.process(image, mask_bright_clusters=True)
    nuc_overlap_masked = (result_masked.processable_mask & result_masked.nucleus_mask).sum()
    bright_overlap_masked = (result_masked.processable_mask & result_masked.bright_cluster_mask).sum()
    assert nuc_overlap_masked == 0, f'Processable zone overlaps nucleus: {nuc_overlap_masked} px'
    assert bright_overlap_masked == 0, f'Masked zone overlaps bright clusters: {bright_overlap_masked} px'

    print('  [PASS] Synthetic cell processing')


def test_empty_image():
    """Test processing on a blank (no cell) image."""
    image = np.ones((100, 100, 4)) * 3000.0
    proc = CellRegionProcessor()
    result = proc.process(image)

    assert not result.processable_mask.any(), 'No processable zone expected'
    assert result.zone_stats.get('processable') == False
    print('  [PASS] Empty image handling')


def test_overlay_generation():
    """Test diagnostic overlay output."""
    ny, nx = 50, 50
    result = CellProcessingResult((ny, nx))
    result.cell_interior_mask[10:40, 10:40] = True
    result.nucleus_mask[20:30, 20:30] = True
    result.bright_cluster_mask[15:18, 15:18] = True
    result.processable_mask[10:40, 10:40] = True
    result.processable_mask[20:30, 20:30] = False  # exclude nucleus
    result.processable_mask[15:18, 15:18] = False  # exclude clusters

    proc = CellRegionProcessor()
    overlay = proc.get_overlay_colors(result)

    assert overlay.shape == (ny, nx, 4)
    # Nucleus should be blue
    assert overlay[25, 25, 2] == 1.0  # blue channel
    # Bright cluster should be red
    assert overlay[16, 16, 0] == 1.0  # red channel
    # Processable zone should be green
    assert overlay[12, 12, 1] == 1.0  # green channel
    print('  [PASS] Overlay generation')


# ===================================================================
# Integration Tests: Real close-scan data
# ===================================================================

def test_close_scan_processing():
    """Process all available close scans and report results."""
    proc = CellRegionProcessor()

    for scan_id, desc in CLOSE_SCANS:
        fp = os.path.join(CONFOCAL2, f'{scan_id}_confocal_xy_data.dat')
        if not os.path.exists(fp):
            print(f'  [SKIP] {scan_id} ({desc}) - file not found')
            continue

        image, ux, uy, hdr = load_scan(scan_id)
        ny, nx = image.shape[:2]
        fluor = image[:, :, 3]

        print(f'\n  === {scan_id} ({desc}, {nx}x{ny}) ===')
        print(f'    Image intensity: {fluor.min():.0f}-{fluor.max():.0f} Hz, '
              f'median={np.median(fluor):.0f}')

        result = proc.process(image)

        # Cell interior
        cell_px = result.cell_interior_mask.sum()
        cell_frac = result.diagnostics.get('cell_area_fraction', 0)
        print(f'    Cell interior: {cell_px} px ({cell_frac*100:.1f}% of image)')

        # Nucleus
        nuc = result.nucleus_stats
        if nuc.get('detected'):
            print(f'    Nucleus: DETECTED, {nuc["area_px"]} px '
                  f'({nuc["area_fraction_of_cell"]*100:.1f}% of cell), '
                  f'compactness={nuc["compactness"]:.2f}, '
                  f'ring_contrast={nuc["ring_contrast"]:.3f}')
        else:
            print(f'    Nucleus: NOT detected ({nuc.get("reason", "?")})')

        # Bright clusters
        n_clusters = len(result.bright_cluster_stats)
        bright_px = result.bright_cluster_mask.sum()
        print(f'    Bright clusters: {n_clusters} clusters, {bright_px} px total')
        for i, cs in enumerate(result.bright_cluster_stats[:5]):
            print(f'      Cluster {i+1}: {cs["area_px"]} px, '
                  f'peak={cs["peak_intensity"]:.0f} Hz')

        # Processable zone
        zs = result.zone_stats
        if zs.get('processable'):
            print(f'    Processable zone: {zs["area_px"]} px '
                  f'({zs["area_fraction_of_cell"]*100:.1f}% of cell)')
            print(f'      Intensity: mean={zs["mean_intensity"]:.0f}, '
                  f'median={zs["median_intensity"]:.0f}, '
                  f'range={zs["min_intensity"]:.0f}-{zs["max_intensity"]:.0f} Hz')
        else:
            print(f'    Processable zone: EMPTY ({zs.get("reason", "?")})')

        # Validation
        assert result.cell_interior_mask.any(), \
            f'{scan_id}: Cell should be detected in close scan'
        assert result.zone_stats.get('processable'), \
            f'{scan_id}: Should have processable zone'

        # Nucleus exclusion check
        nuc_proc_overlap = (result.processable_mask & result.nucleus_mask).sum()
        assert nuc_proc_overlap == 0, \
            f'{scan_id}: Processable overlaps nucleus ({nuc_proc_overlap}px)'

        # Masked mode test
        res_masked = proc.process(image, mask_bright_clusters=True)
        bright_masked_overlap = (res_masked.processable_mask & res_masked.bright_cluster_mask).sum()
        assert bright_masked_overlap == 0, \
            f'{scan_id}: Masked processable overlaps bright ({bright_masked_overlap}px)'

    print('\n  [PASS] All close scans processed')


def test_parent_scan_processing():
    """Process a parent 200x200 scan (should detect multiple cells)."""
    proc = CellRegionProcessor()

    for scan_id, desc in PARENT_SCANS:
        fp = os.path.join(CONFOCAL2, f'{scan_id}_confocal_xy_data.dat')
        if not os.path.exists(fp):
            print(f'  [SKIP] {scan_id}'); continue

        image, ux, uy, hdr = load_scan(scan_id)
        print(f'\n  === Parent: {scan_id} ({desc}) ===')

        result = proc.process(image)
        cell_frac = result.diagnostics.get('cell_area_fraction', 0)
        print(f'    Cell area: {cell_frac*100:.1f}% (many cells merged into one)')
        print(f'    Nucleus detected: {result.nucleus_stats.get("detected", False)}')
        print(f'    Bright clusters: {len(result.bright_cluster_stats)}')
        print(f'    Processable: {result.zone_stats.get("processable", False)}, '
              f'area={result.zone_stats.get("area_px", 0)} px')

        # Parent scan cell detection should find something
        assert result.cell_interior_mask.any(), 'Parent scan should detect cell regions'

    print('\n  [PASS] Parent scan processing (note: this is meant for close scans)')


# ===================================================================
# Main
# ===================================================================

if __name__ == '__main__':
    print('=' * 60)
    print('CellRegionProcessor Tests')
    print('=' * 60)

    print('\n--- Unit Tests ---')
    test_result_creation()
    test_synthetic_cell()
    test_empty_image()
    test_overlay_generation()

    print('\n--- Integration Tests (Confocal2 close scans) ---')
    test_close_scan_processing()
    test_parent_scan_processing()

    print('\n' + '=' * 60)
    print('All tests passed!')
    print('=' * 60)
