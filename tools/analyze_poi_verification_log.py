"""Analyze POI verification logs for optimizer drift and threshold tuning."""

from __future__ import print_function

import argparse
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logic.poi_verification_logger import (  # noqa: E402
    attempt_rows,
    candidate_summary_rows,
    load_manifest,
    summarize_manifest,
    write_csv,
)


def find_run_directories(path):
    """Return run directories containing a POI verification manifest."""
    path = os.path.abspath(path)
    if os.path.isfile(os.path.join(path, 'manifest.json')):
        return [path]
    runs = []
    if os.path.isdir(path):
        for entry in sorted(os.listdir(path)):
            candidate = os.path.join(path, entry)
            if os.path.isfile(os.path.join(candidate, 'manifest.json')):
                runs.append(candidate)
    return runs


def _fmt(value, suffix=''):
    if value is None:
        return 'N/A'
    if isinstance(value, float):
        return '{0:.3f}{1}'.format(value, suffix)
    return '{0}{1}'.format(value, suffix)


def analyze(path, output_directory=None):
    """Analyze one run or a parent directory of runs."""
    run_directories = find_run_directories(path)
    if not run_directories:
        raise ValueError('no manifest.json found in {0}'.format(path))

    manifests = [load_manifest(run_directory) for run_directory in run_directories]
    candidate_rows = []
    attempts = []
    for manifest in manifests:
        candidate_rows.extend(candidate_summary_rows(manifest))
        attempts.extend(attempt_rows(manifest))

    if output_directory is None:
        output_directory = run_directories[0] if len(run_directories) == 1 else os.path.abspath(path)
    os.makedirs(output_directory, exist_ok=True)
    candidate_csv = os.path.join(output_directory, 'poi_verification_summary.csv')
    attempt_csv = os.path.join(output_directory, 'poi_verification_attempts.csv')
    write_csv(candidate_csv, candidate_rows)
    write_csv(attempt_csv, attempts)

    summaries = [summarize_manifest(manifest) for manifest in manifests]
    status_counts = {}
    for summary in summaries:
        for status, count in summary.get('status_counts', {}).items():
            status_counts[status] = status_counts.get(status, 0) + count

    shifts = [
        row.get('initial_to_final_shift_nm') for row in candidate_rows
        if row.get('initial_to_final_shift_nm') is not None
    ]
    final_distances = [
        row.get('last_poi_gaussian_distance_nm') for row in candidate_rows
        if row.get('last_poi_gaussian_distance_nm') is not None
    ]
    r2_values = [
        row.get('r_squared_xy') for row in attempts
        if row.get('r_squared_xy') is not None
    ]

    print('--- POI Verification Log Analysis ---')
    print('Runs analyzed: {0}'.format(len(manifests)))
    print('Candidates: {0}'.format(len(candidate_rows)))
    print('Attempts: {0}'.format(len(attempts)))
    print('Status counts: {0}'.format(status_counts or {}))
    if shifts:
        print('Initial -> final/last Gaussian shift: median {0}, max {1}'.format(
            _fmt(_median(shifts), ' nm'), _fmt(max(shifts), ' nm')))
    else:
        print('Initial -> final/last Gaussian shift: N/A')
    if final_distances:
        print('Final POI/Gaussian distance: median {0}, max {1}'.format(
            _fmt(_median(final_distances), ' nm'), _fmt(max(final_distances), ' nm')))
    else:
        print('Final POI/Gaussian distance: N/A')
    if r2_values:
        print('R2: median {0}, best {1}'.format(
            _fmt(_median(r2_values)), _fmt(max(r2_values))))
    else:
        print('R2: N/A')
    print('Wrote: {0}'.format(candidate_csv))
    print('Wrote: {0}'.format(attempt_csv))
    return candidate_csv, attempt_csv


def _median(values):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Analyze POI verification logs and export CSV summaries.')
    parser.add_argument(
        'path',
        help='Run directory containing manifest.json, or parent directory of runs.')
    parser.add_argument(
        '--output-directory',
        help='Directory for CSV outputs. Defaults to the run or parent directory.')
    args = parser.parse_args(argv)
    analyze(args.path, output_directory=args.output_directory)


if __name__ == '__main__':
    main()
