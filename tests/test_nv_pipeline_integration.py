"""Replay close-cell scans through CellProcessor -> POIExtractor -> Optimizer2D.

The test intentionally uses only archived confocal data.  It is an integration
and diagnostic test, not a live-optimizer or NV-confirmation test: no scanner
hardware is instantiated or moved.

Running this file creates per-scan combined diagnostic figures and structured
candidate/fit data in ``tests/output_visuals/nv_pipeline_integration``.
"""

import csv
import json
import os

import matplotlib.pyplot as plt
import numpy as np

try:
    import pytest
except ImportError:
    class _MockPytest:
        @staticmethod
        def mark():
            pass
        class mark:
            @staticmethod
            def parametrize(*args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator
        @staticmethod
        def skip(reason=''):
            pass
    pytest = _MockPytest()

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logic.cell_region_processor import CellRegionProcessor
from logic.cell_segmentation_logic import CellSegmentationLogic
from logic.optimizer2 import Optimizer2D
from logic.poi_extractor import POIExtractor


CONFOCAL2_DIR = os.path.join(PROJECT_ROOT, 'Confocal2')
OUTPUT_DIR = os.path.join(
    PROJECT_ROOT, 'tests', 'output_visuals', 'nv_pipeline_integration')

# These are the close-cell scans in the currently available Confocal2 corpus.
CLOSE_SCANS = (
    '20260706-1701-46',  # 29.65 x 38.52 um
    '20260706-1724-08',  # 43.65 x 54.59 um
    '20260706-1833-28',  # 52.59 x 72.94 um
)
MAX_OPTIMIZER2_FITS = 12
LOCAL_FIT_WINDOW_M = 3.0e-6

_STATUS_COLORS = {
    'strong_candidate': 'lime',
    'marginal': 'gold',
    'rejected': 'red',
    'pending': 'white',
}


def _scan_path(scan_id):
    return os.path.join(CONFOCAL2_DIR, '{}_confocal_xy_data.dat'.format(scan_id))


def _json_float(value):
    return None if value is None else float(value)


def _fit_record(scan_id, candidate, fit_result):
    """Return a stable, JSON/CSV-friendly record for one candidate replay."""
    x_min, x_max, y_min, y_max = fit_result.sampled_bounds_m
    record = {
        # POICandidate IDs are UUIDs created at extraction time.  Persist a
        # reproducible scan/pixel label for replay artifacts instead.
        'candidate_id': '{}_r{:03d}_c{:03d}'.format(
            scan_id, candidate.pixel_row, candidate.pixel_col),
        'classification': candidate.classification,
        'rank': int(candidate.rank),
        'candidate_x_m': float(candidate.x),
        'candidate_y_m': float(candidate.y),
        'pixel_row': int(candidate.pixel_row),
        'pixel_col': int(candidate.pixel_col),
        'overall_score': float(candidate.overall_score),
        'candidate_snr': float(candidate.snr),
        'optimizer2_success': bool(fit_result.success),
        'optimizer2_x_m': (
            _json_float(fit_result.position_m[0]) if fit_result.position_m else None),
        'optimizer2_y_m': (
            _json_float(fit_result.position_m[1]) if fit_result.position_m else None),
        'optimizer2_sigma_x_m': (
            _json_float(fit_result.sigma_m[0]) if fit_result.sigma_m else None),
        'optimizer2_sigma_y_m': (
            _json_float(fit_result.sigma_m[1]) if fit_result.sigma_m else None),
        'optimizer2_r_squared': _json_float(fit_result.r_squared),
        'optimizer2_edge_fit': bool(fit_result.is_edge_fit),
        'optimizer2_error': fit_result.error,
        'fit_x_min_m': x_min,
        'fit_x_max_m': x_max,
        'fit_y_min_m': y_min,
        'fit_y_max_m': y_max,
        'fit_pitch_x_m': fit_result.pitch_m[0],
        'fit_pitch_y_m': fit_result.pitch_m[1],
        'fit_rows': fit_result.sample_shape[0],
        'fit_cols': fit_result.sample_shape[1],
    }
    return record


def _candidate_groups(extraction):
    return {
        'strong_candidate': list(extraction.strong_candidates),
        'marginal': list(extraction.marginal_candidates),
        'rejected': list(extraction.rejected_candidates),
    }


def _write_scan_outputs(scan_id, image, x_coords, y_coords, cell_result,
                        extraction, records):
    """Write one combined pipeline figure and its machine-readable results."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_prefix = os.path.join(OUTPUT_DIR, scan_id)
    groups = _candidate_groups(extraction)
    counts = image[:, :, 3]
    extent = (x_coords[0] * 1e6, x_coords[-1] * 1e6,
              y_coords[0] * 1e6, y_coords[-1] * 1e6)

    fig, axes = plt.subplots(2, 3, figsize=(16, 10), constrained_layout=True)
    axes = axes.ravel()

    raw = axes[0].imshow(np.log10(np.maximum(counts, 1.0)), origin='lower',
                         extent=extent, cmap='inferno', aspect='auto')
    axes[0].set_title('Raw close-cell counts (log10 Hz)')
    fig.colorbar(raw, ax=axes[0], label='log10(count rate / Hz)')

    mask_rgb = np.zeros(counts.shape + (3,), dtype=float)
    mask_rgb[:, :, 1] = cell_result.cell_interior_mask.astype(float)
    mask_rgb[:, :, 2] = cell_result.nucleus_mask.astype(float)
    mask_rgb[:, :, 0] = cell_result.bright_cluster_mask.astype(float)
    axes[1].imshow(mask_rgb, origin='lower', extent=extent, aspect='auto')
    axes[1].set_title('Cell mask: green; nucleus: blue; clusters: red')

    axes[2].imshow(np.log10(np.maximum(counts, 1.0)), origin='lower',
                   extent=extent, cmap='inferno', aspect='auto')
    processable = np.ma.masked_where(~cell_result.processable_mask,
                                     cell_result.processable_mask)
    axes[2].imshow(processable, origin='lower', extent=extent, cmap='Greens',
                   alpha=0.35, aspect='auto')
    for status, candidates in groups.items():
        if not candidates:
            continue
        axes[2].scatter([candidate.x * 1e6 for candidate in candidates],
                        [candidate.y * 1e6 for candidate in candidates],
                        s=32, facecolors='none', edgecolors=_STATUS_COLORS[status],
                        label='{} ({})'.format(status, len(candidates)))
    axes[2].set_title('Processable zone and POIExtractor candidates')
    axes[2].legend(loc='upper right', fontsize=8)

    axes[3].imshow(np.log10(np.maximum(counts, 1.0)), origin='lower',
                   extent=extent, cmap='inferno', aspect='auto')
    for record in records:
        color = _STATUS_COLORS[record['classification']]
        axes[3].plot(record['candidate_x_m'] * 1e6,
                     record['candidate_y_m'] * 1e6, marker='x', color=color,
                     markersize=7)
        if record['optimizer2_success']:
            axes[3].plot(record['optimizer2_x_m'] * 1e6,
                         record['optimizer2_y_m'] * 1e6, marker='+', color='cyan',
                         markersize=8, markeredgewidth=1.5)
    axes[3].set_title('Selected candidates (x) and Optimizer2 centres (+)')

    successful = [record for record in records if record['optimizer2_success']]
    for status in ('strong_candidate', 'marginal', 'rejected'):
        status_records = [record for record in successful
                          if record['classification'] == status]
        if status_records:
            axes[4].scatter(
                [record['overall_score'] for record in status_records],
                [record['optimizer2_r_squared'] for record in status_records],
                c=_STATUS_COLORS[status], label=status, alpha=0.8)
    axes[4].set_xlabel('POIExtractor overall score')
    axes[4].set_ylabel('Optimizer2 R²')
    axes[4].set_ylim(-0.1, 1.05)
    axes[4].set_title('Offline fit quality is diagnostic only')
    if successful:
        axes[4].legend(fontsize=8)

    counts_by_status = [len(groups[status]) for status in
                        ('strong_candidate', 'marginal', 'rejected')]
    axes[5].bar(('strong', 'marginal', 'rejected'), counts_by_status,
                color=(_STATUS_COLORS['strong_candidate'],
                       _STATUS_COLORS['marginal'], _STATUS_COLORS['rejected']))
    axes[5].set_ylabel('candidate count')
    axes[5].set_title('Pipeline summary')
    max_count = max(counts_by_status) if counts_by_status else 1
    axes[5].set_ylim(0, max(1, max_count) * 1.35)
    text = (
        'processable: {0} px\n'
        'optimizer2 attempts: {1}\n'
        'successful fits: {2}\n'
        'edge fits: {3}'
    ).format(cell_result.processable_mask.sum(), len(records), len(successful),
             sum(record['optimizer2_edge_fit'] for record in successful))
    axes[5].text(0.02, 0.98, text, transform=axes[5].transAxes,
                 va='top', ha='left', fontsize=10,
                 bbox={'facecolor': 'white', 'alpha': 0.8, 'edgecolor': 'none'})

    for axis in axes[:4]:
        axis.set_xlabel('X (µm)')
        axis.set_ylabel('Y (µm)')
    fig.suptitle('CellProcessor → POIExtractor → Optimizer2 replay: {}'.format(scan_id))
    fig.savefig(output_prefix + '_combined.png', dpi=160)
    plt.close(fig)

    metadata = {
        'scan_id': scan_id,
        'scan_shape': [int(value) for value in counts.shape],
        'scan_extent_m': [float(x_coords[0]), float(x_coords[-1]),
                          float(y_coords[0]), float(y_coords[-1])],
        'cell_processor': {
            'zone_stats': cell_result.zone_stats,
            'nucleus_stats': cell_result.nucleus_stats,
            'bright_cluster_count': len(cell_result.bright_cluster_stats),
        },
        'poi_extractor_stats': extraction.stats,
        'optimizer2_records': records,
    }
    with open(output_prefix + '_results.json', 'w') as result_file:
        json.dump(metadata, result_file, indent=2, sort_keys=True)

    fieldnames = list(records[0].keys()) if records else [
        'candidate_id', 'classification', 'optimizer2_success']
    with open(output_prefix + '_optimizer2.csv', 'w', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def run_close_scan_pipeline(scan_id):
    """Replay one archived close scan through the complete offline pipeline."""
    image, x_coords, y_coords, _ = (
        CellSegmentationLogic().parse_dat_file(_scan_path(scan_id)))
    cell_result = CellRegionProcessor().process(image)
    extraction = POIExtractor().extract(
        cell_result, image, x_coords=x_coords, y_coords=y_coords,
        z_current=float(image[0, 0, 2]))

    candidates = sorted(
        extraction.strong_candidates + extraction.marginal_candidates +
        extraction.rejected_candidates,
        key=lambda candidate: candidate.overall_score,
        reverse=True)[:MAX_OPTIMIZER2_FITS]

    optimizer2 = Optimizer2D()
    records = []
    for candidate in candidates:
        fit_result = optimizer2.fit_local(
            image[:, :, 3], x_coords, y_coords,
            seed_position_m=(candidate.x, candidate.y),
            window_size_m=LOCAL_FIT_WINDOW_M)
        records.append(_fit_record(scan_id, candidate, fit_result))

    _write_scan_outputs(scan_id, image, x_coords, y_coords, cell_result,
                        extraction, records)
    return cell_result, extraction, records


@pytest.mark.parametrize('scan_id', CLOSE_SCANS)
def test_close_cell_pipeline_replay_and_diagnostics(scan_id):
    """Integration test on each available close-cell scan in Confocal2."""
    if not os.path.exists(_scan_path(scan_id)):
        pytest.skip('Close-cell fixture is unavailable: {}'.format(scan_id))

    cell_result, extraction, records = run_close_scan_pipeline(scan_id)

    assert cell_result.zone_stats['processable']
    assert cell_result.processable_mask.any()
    assert extraction.stats['total_detected'] == len(extraction.candidates)
    assert extraction.stats['total_detected'] > 0
    assert records

    for record in records:
        if record['optimizer2_success']:
            assert record['fit_x_min_m'] <= record['optimizer2_x_m'] <= record['fit_x_max_m']
            assert record['fit_y_min_m'] <= record['optimizer2_y_m'] <= record['fit_y_max_m']

    prefix = os.path.join(OUTPUT_DIR, scan_id)
    assert os.path.exists(prefix + '_combined.png')
    assert os.path.exists(prefix + '_results.json')
    assert os.path.exists(prefix + '_optimizer2.csv')


def test_close_cell_scan_with_strong_candidates_reaches_optimizer2():
    """The 1701 close scan provides positive pipeline coverage today."""
    _, extraction, records = run_close_scan_pipeline('20260706-1701-46')

    assert extraction.strong_candidates
    assert any(record['classification'] == 'strong_candidate' for record in records)
    assert any(record['optimizer2_success'] for record in records)


if __name__ == '__main__':
    print("Running NV Pipeline Integration Replay on all close scans...")
    for s_id in CLOSE_SCANS:
        print(f"\n--- Testing Scan {s_id} ---")
        c_res, ext, recs = run_close_scan_pipeline(s_id)
        strong_count = len(ext.strong_candidates)
        opt_success = sum(r['optimizer2_success'] for r in recs)
        print(f"  Processable zone: {c_res.processable_mask.sum()} px")
        print(f"  POI detected: {ext.stats['total_detected']}, Strong: {strong_count}")
        print(f"  Optimizer2 attempted: {len(recs)}, Success: {opt_success}")
        assert c_res.processable_mask.any()
        assert ext.stats['total_detected'] > 0
        assert strong_count > 0, f"Expected strong candidates for {s_id}"
    print("\nAll close scans processed successfully!")
