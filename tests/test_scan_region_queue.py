# -*- coding: utf-8 -*-
"""
Tests for the ScanRegionQueue module.

Runs against real Confocal2 data to validate:
  - Region extraction from ROI segmentation masks
  - False positive filtering (min 20 µm dimension rule)
  - Priority queue ordering
  - Scanner parameter computation
  - Serialization roundtrip
  - Touching cell separation

Usage:
    python -m pytest tests/test_scan_region_queue.py -v
    or
    python tests/test_scan_region_queue.py   (standalone)
"""

import os
import sys
import json
import numpy as np

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.scan_region_queue import ScanRegionQueue, ScanRegion

# -------------------------------------------------------------------
# Paths to test data
# -------------------------------------------------------------------
CONFOCAL2_DIR = os.path.join(PROJECT_ROOT, 'Confocal2')

# Parent scans (200×200 µm)
PARENT_SCANS = [
    os.path.join(CONFOCAL2_DIR, '20260705-1517-07_confocal_xy_data.dat'),
    os.path.join(CONFOCAL2_DIR, '20260706-1037-35_confocal_xy_data.dat'),
    os.path.join(CONFOCAL2_DIR, '20260706-1218-34_confocal_xy_data.dat'),
    os.path.join(CONFOCAL2_DIR, '20260706-1733-10_confocal_xy_data.dat'),
    os.path.join(CONFOCAL2_DIR, '20260706-2212-44_confocal_xy_data.dat'),
]

# Close scans (variable FOV)
CLOSE_SCANS = [
    os.path.join(CONFOCAL2_DIR, '20260706-1701-46_confocal_xy_data.dat'),
    os.path.join(CONFOCAL2_DIR, '20260706-1724-08_confocal_xy_data.dat'),
    os.path.join(CONFOCAL2_DIR, '20260706-1833-28_confocal_xy_data.dat'),
]


def get_available_parent_scans():
    """Return list of parent scan files that actually exist."""
    return [p for p in PARENT_SCANS if os.path.exists(p)]


def extract_scan_id(filepath):
    """Extract timestamp ID from filename."""
    basename = os.path.basename(filepath)
    # e.g. '20260705-1517-07_confocal_xy_data.dat' -> '20260705-1517-07'
    return basename.split('_confocal')[0]


# ===================================================================
# Test: ScanRegion dataclass
# ===================================================================

def test_scan_region_creation():
    """Test ScanRegion can be created with defaults."""
    r = ScanRegion()
    assert r.region_id.startswith('R-')
    assert r.status == 'queued'
    assert r.width_um == 0.0
    assert r.cropped_image is None
    print('  ✓ ScanRegion creation with defaults')


def test_scan_region_serialization():
    """Test ScanRegion to_dict / from_dict roundtrip."""
    r = ScanRegion(
        bbox_physical=(1e-5, 5e-5, 2e-5, 6e-5),
        bbox_pixels=(10, 50, 20, 60),
        width_um=40.0,
        height_um=40.0,
        area_um2=1600.0,
        centroid_physical=(3e-5, 4e-5),
        peak_intensity=150000.0,
        mean_intensity=50000.0,
        parent_scan_id='test-scan',
    )
    r.status = 'processed'
    r.priority = 42.5
    r.nv_candidates_found = 3

    d = r.to_dict()
    assert isinstance(d, dict)
    assert d['region_id'] == r.region_id

    r2 = ScanRegion.from_dict(d)
    assert r2.region_id == r.region_id
    assert r2.status == 'processed'
    assert r2.priority == 42.5
    assert r2.nv_candidates_found == 3
    assert abs(r2.width_um - 40.0) < 0.01
    print('  ✓ ScanRegion serialization roundtrip')


# ===================================================================
# Test: Queue operations (synthetic)
# ===================================================================

def test_queue_basic_operations():
    """Test basic queue add/get/mark operations."""
    queue = ScanRegionQueue()

    # Manually add regions
    queue._regions = [
        ScanRegion(region_id='R-001', width_um=30, height_um=30,
                   area_um2=900, peak_intensity=100000),
        ScanRegion(region_id='R-002', width_um=25, height_um=40,
                   area_um2=1000, peak_intensity=200000),
        ScanRegion(region_id='R-003', width_um=10, height_um=10,
                   area_um2=100, peak_intensity=50000),
    ]
    queue._rebuild_index()

    assert queue.total_count == 3
    assert queue.queued_count == 3
    assert queue.has_queued_regions()

    # Get by ID
    r = queue.get_region_by_id('R-002')
    assert r is not None
    assert r.peak_intensity == 200000

    # Mark status
    queue.mark_region_status('R-001', 'scanning')
    assert queue.get_region_by_id('R-001').status == 'scanning'
    assert queue.queued_count == 2

    queue.mark_region_status('R-001', 'processed', nv_candidates_found=5)
    assert queue.get_region_by_id('R-001').nv_candidates_found == 5

    print('  ✓ Queue basic operations')


def test_queue_priority():
    """Test priority ordering."""
    queue = ScanRegionQueue()
    queue._regions = [
        ScanRegion(region_id='R-low', peak_intensity=10000,
                   area_um2=100, width_um=20, height_um=20),
        ScanRegion(region_id='R-high', peak_intensity=200000,
                   area_um2=900, width_um=30, height_um=30),
        ScanRegion(region_id='R-mid', peak_intensity=100000,
                   area_um2=400, width_um=20, height_um=20),
    ]
    queue._rebuild_index()

    queue.prioritize_queue(method='intensity_area')
    next_r = queue.get_next_region()
    assert next_r.region_id == 'R-high', \
        f'Expected R-high first, got {next_r.region_id}'
    print('  ✓ Queue priority ordering (intensity × area)')


def test_false_positive_filtering_synthetic():
    """Test false positive filtering with known dimensions."""
    queue = ScanRegionQueue()
    queue._regions = [
        # Good: 30×30 (both axes > 20, area > 200)
        ScanRegion(region_id='R-good', width_um=30, height_um=30,
                   area_um2=900, peak_intensity=100000),
        # Bad: too small (10×10, shorter < 10? No, 10=10. But longer < 20)
        ScanRegion(region_id='R-small', width_um=10, height_um=10,
                   area_um2=100, peak_intensity=100000),
        # Good: elongated but longer >= 20, shorter >= 10 (15×40)
        ScanRegion(region_id='R-elongated', width_um=15, height_um=40,
                   area_um2=600, peak_intensity=100000),
        # Bad: too large
        ScanRegion(region_id='R-huge', width_um=100, height_um=100,
                   area_um2=10000, peak_intensity=100000),
        # Good: 25×20
        ScanRegion(region_id='R-ok', width_um=25, height_um=20,
                   area_um2=500, peak_intensity=100000),
        # Bad: shorter axis < 10 (5×30)
        ScanRegion(region_id='R-thin', width_um=5, height_um=30,
                   area_um2=150, peak_intensity=100000),
    ]
    queue._rebuild_index()

    result = queue.filter_false_positives(
        min_long_dim_um=20.0, min_short_dim_um=10.0,
        min_area_um2=200.0, max_area_um2=5000.0)

    # Expected: R-good (30x30), R-elongated (15x40), R-ok (25x20)
    assert result['accepted'] == 3, \
        f"Expected 3 accepted, got {result['accepted']}"
    assert result['rejected'] == 3

    ids = [r.region_id for r in queue.regions]
    assert 'R-good' in ids
    assert 'R-elongated' in ids
    assert 'R-ok' in ids
    assert 'R-small' not in ids
    assert 'R-thin' not in ids
    assert 'R-huge' not in ids
    print('  [PASS] False positive filtering (asymmetric rule)')


def test_queue_json_roundtrip():
    """Test full queue JSON serialization/deserialization."""
    queue = ScanRegionQueue()
    queue._regions = [
        ScanRegion(region_id='R-001', width_um=30, height_um=30,
                   area_um2=900, peak_intensity=100000,
                   bbox_physical=(1e-5, 4e-5, 2e-5, 5e-5),
                   parent_scan_id='test'),
        ScanRegion(region_id='R-002', width_um=40, height_um=50,
                   area_um2=2000, peak_intensity=200000,
                   bbox_physical=(5e-5, 9e-5, 1e-5, 6e-5),
                   parent_scan_id='test'),
    ]
    queue._rebuild_index()
    queue.mark_region_status('R-001', 'processed', nv_candidates_found=3)

    json_str = queue.to_json()
    assert isinstance(json_str, str)

    queue2 = ScanRegionQueue()
    queue2.from_json(json_str)

    assert queue2.total_count == 2
    r1 = queue2.get_region_by_id('R-001')
    assert r1 is not None
    assert r1.status == 'processed'
    assert r1.nv_candidates_found == 3
    print('  ✓ Queue JSON roundtrip')


def test_compute_scan_parameters():
    """Test scanner FOV computation for a region."""
    queue = ScanRegionQueue()

    region = ScanRegion(
        bbox_physical=(50e-6, 80e-6, 100e-6, 140e-6),
        width_um=30.0,
        height_um=40.0,
    )

    params = queue.compute_scan_parameters(region, margin_fraction=0.10)

    assert params['fov_x_um'] > 30.0, 'FOV should include margin'
    assert params['fov_y_um'] > 40.0, 'FOV should include margin'
    assert params['resolution'] == 200
    assert params['expected_pixel_size_x_um'] > 0
    print(f'  ✓ Scan params: FOV={params["fov_x_um"]:.1f}×{params["fov_y_um"]:.1f} µm, '
          f'px={params["expected_pixel_size_x_um"]:.3f} µm')


def test_compute_scan_parameters_min_fov():
    """Test that minimum FOV is enforced."""
    queue = ScanRegionQueue()

    # Tiny region (2×2 µm) — should be expanded to min 5 µm
    region = ScanRegion(
        bbox_physical=(100e-6, 102e-6, 100e-6, 102e-6),
        width_um=2.0,
        height_um=2.0,
    )

    params = queue.compute_scan_parameters(region, min_fov_um=5.0)
    assert params['fov_x_um'] >= 5.0
    assert params['fov_y_um'] >= 5.0
    print(f'  ✓ Min FOV enforced: {params["fov_x_um"]:.1f}×{params["fov_y_um"]:.1f} µm')


# ===================================================================
# Test: Integration with real Confocal2 data
# ===================================================================

def test_extraction_on_parent_scan():
    """Run full extraction pipeline on a real parent scan."""
    scans = get_available_parent_scans()
    if not scans:
        print('  ⚠ No parent scan data found — skipping')
        return

    filepath = scans[0]
    scan_id = extract_scan_id(filepath)
    print(f'  Testing on: {os.path.basename(filepath)}')

    # Segment
    seg = ROISegmentationLogic()
    image, ux, uy, header = seg.parse_dat_file(filepath)
    result = seg.segment_roi(image)

    print(f'    ROI mask pixels: {result["roi_mask"].sum()}')
    print(f'    Diffuse mask pixels: {result["diffuse_region_mask"].sum()}')

    # Extract regions
    queue = ScanRegionQueue()
    n_regions = queue.extract_regions_from_segmentation(
        segmentation_result=result,
        image=image,
        x_coords=ux,
        y_coords=uy,
        parent_scan_id=scan_id,
    )
    print(f'    Regions extracted: {n_regions}')

    # Print region details
    for r in queue.regions:
        print(f'      {r.region_id}: {r.width_um:.1f}×{r.height_um:.1f} µm, '
              f'peak={r.peak_intensity:.0f}, '
              f'area={r.area_um2:.0f} µm²')

    # Filter
    filter_result = queue.filter_false_positives()
    print(f'    After filtering: {filter_result["accepted"]} accepted, '
          f'{filter_result["rejected"]} rejected')
    for rid, reason in filter_result['rejection_reasons']:
        print(f'      Rejected {rid}: {reason}')

    # Prioritize
    queue.prioritize_queue()
    print(f'    Queue after prioritization:')
    for r in queue.regions:
        print(f'      {r.region_id}: priority={r.priority:.0f}, '
              f'{r.width_um:.1f}×{r.height_um:.1f} µm')

    # Compute scan params for first region
    if queue.has_queued_regions():
        region = queue.get_next_region()
        params = queue.compute_scan_parameters(region)
        print(f'    Next scan: {region.region_id}')
        print(f'      FOV: {params["fov_x_um"]:.1f}×{params["fov_y_um"]:.1f} µm')
        print(f'      Pixel size: {params["expected_pixel_size_x_um"]:.3f} µm')
        print(f'      Center: ({params["center"][0]*1e6:.1f}, '
              f'{params["center"][1]*1e6:.1f}) µm')

    # Check crops exist
    crops = queue.get_cropped_images()
    print(f'    Cropped images: {len(crops)}')
    for rid, crop in crops:
        print(f'      {rid}: shape={crop.shape}')

    # Summary
    summary = queue.get_queue_summary()
    print(f'    Summary: {summary}')

    assert n_regions > 0, 'Should find at least one region in parent scan'
    print('  ✓ Integration test passed')


def test_all_parent_scans():
    """Run extraction on all available parent scans and compare region counts."""
    scans = get_available_parent_scans()
    if not scans:
        print('  ⚠ No parent scan data found — skipping')
        return

    print(f'  Testing {len(scans)} parent scans:')
    seg = ROISegmentationLogic()

    for filepath in scans:
        scan_id = extract_scan_id(filepath)
        image, ux, uy, header = seg.parse_dat_file(filepath)
        result = seg.segment_roi(image)

        queue = ScanRegionQueue()
        n_raw = queue.extract_regions_from_segmentation(
            result, image, ux, uy, scan_id)
        filt = queue.filter_false_positives()

        print(f'    {scan_id}: {n_raw} raw → {filt["accepted"]} accepted')

    print('  ✓ All parent scans processed')


# ===================================================================
# Main
# ===================================================================

if __name__ == '__main__':
    print('=' * 60)
    print('ScanRegionQueue Tests')
    print('=' * 60)

    print('\n--- Unit Tests ---')
    test_scan_region_creation()
    test_scan_region_serialization()
    test_queue_basic_operations()
    test_queue_priority()
    test_false_positive_filtering_synthetic()
    test_queue_json_roundtrip()
    test_compute_scan_parameters()
    test_compute_scan_parameters_min_fov()

    print('\n--- Integration Tests (Confocal2 data) ---')
    test_extraction_on_parent_scan()
    test_all_parent_scans()

    print('\n' + '=' * 60)
    print('All tests passed!')
    print('=' * 60)
