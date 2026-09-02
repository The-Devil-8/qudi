# -*- coding: utf-8 -*-
"""
Unit tests for CellDataLogger and coordinate interpolation functions.

Run with: python -m pytest tests/test_cell_data_logger.py -v
"""

import sys
import os
import json
import csv
import shutil
import tempfile
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'logic'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from logic.cell_data_logger import (
    CellDataLogger,
    interpolate_physical_to_pixel,
    interpolate_pixel_to_physical,
)
from logic.scan_region_queue import ScanRegion


class TestCoordinateInterpolation:
    """Test physical-to-pixel and pixel-to-physical coordinate transformations."""

    def test_linear_increasing_grid(self):
        # 101 points from -50e-6 to +50e-6 (step 1e-6)
        x_coords = np.linspace(-50e-6, 50e-6, 101)
        y_coords = np.linspace(-20e-6, 20e-6, 41)

        # Center (0, 0) should be at pixel (50, 20)
        col, row = interpolate_physical_to_pixel(0.0, 0.0, x_coords, y_coords)
        assert col == pytest.approx(50.0)
        assert row == pytest.approx(20.0)

        # Reconstructed physical coordinates
        x_rec, y_rec = interpolate_pixel_to_physical(col, row, x_coords, y_coords)
        assert x_rec == pytest.approx(0.0)
        assert y_rec == pytest.approx(0.0)

    def test_subpixel_interpolation(self):
        x_coords = np.array([0.0, 10e-6, 20e-6])
        y_coords = np.array([0.0, 10e-6, 20e-6])

        # Point at (5e-6, 15e-6) -> pixel (0.5, 1.5)
        col, row = interpolate_physical_to_pixel(5e-6, 15e-6, x_coords, y_coords)
        assert col == pytest.approx(0.5)
        assert row == pytest.approx(1.5)

    def test_decreasing_grid(self):
        # Decreasing coordinates (e.g. top-to-bottom scan)
        x_coords = np.linspace(50e-6, -50e-6, 101)
        y_coords = np.linspace(20e-6, -20e-6, 41)

        col, row = interpolate_physical_to_pixel(0.0, 0.0, x_coords, y_coords)
        assert col == pytest.approx(50.0)
        assert row == pytest.approx(20.0)


class TestCellDataLogger:
    """Test session directory creation, figure rendering, and structured data archiving."""

    @pytest.fixture
    def temp_dir(self):
        dir_path = tempfile.mkdtemp(prefix='test_auto_nv_')
        yield dir_path
        shutil.rmtree(dir_path, ignore_errors=True)

    @pytest.fixture
    def synthetic_scan_data(self):
        # 50x50 micro scan
        nx, ny = 50, 50
        x_coords = np.linspace(-15e-6, 15e-6, nx)
        y_coords = np.linspace(-15e-6, 15e-6, ny)
        z_current = 2.5e-6

        image = np.zeros((ny, nx, 4), dtype=float)
        for i in range(ny):
            for j in range(nx):
                image[i, j, 0] = x_coords[j]
                image[i, j, 1] = y_coords[i]
                image[i, j, 2] = z_current

        # Background + two Gaussian NV spots
        xx, yy = np.meshgrid(x_coords, y_coords)
        fluor = np.random.normal(25000, 1000, (ny, nx))

        # NV 1 at (-5um, 3um)
        fluor += 150000 * np.exp(-((xx - (-5e-6))**2 + (yy - 3e-6)**2) / (2 * (0.3e-6)**2))
        # NV 2 at (4um, -2um)
        fluor += 200000 * np.exp(-((xx - 4e-6)**2 + (yy - (-2e-6))**2) / (2 * (0.35e-6)**2))

        image[:, :, 3] = fluor
        return image, x_coords, y_coords, z_current

    def test_logger_initialization_and_manifest(self, temp_dir):
        logger = CellDataLogger(
            base_data_dir=temp_dir,
            run_id='test_run_001',
            config_metadata={'target_cells': 3, 'target_nvs': 2})

        assert os.path.exists(logger.output_directory)
        manifest_path = os.path.join(logger.output_directory, 'run_manifest.json')
        assert os.path.exists(manifest_path)

        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        assert manifest['run_id'] == 'test_run_001'
        assert manifest['status'] == 'running'
        assert manifest['config_metadata']['target_cells'] == 3

    def test_save_cell_data_with_verified_pois(self, temp_dir, synthetic_scan_data):
        image, x_coords, y_coords, z_current = synthetic_scan_data
        logger = CellDataLogger(base_data_dir=temp_dir, run_id='test_run_002')

        region = ScanRegion(region_id='R001', bbox_physical=(-15e-6, 15e-6, -15e-6, 15e-6),
                            width_um=30.0, height_um=30.0)

        verified_pois = [
            {
                'candidate_id': 'POI-001',
                'poi_name': 'NV_R001_POI-001',
                'accepted_position_m': [-5e-6, 3e-6, z_current],
                'seed_position_m': [-5.1e-6, 2.9e-6, z_current],
                'optical_stats': {
                    'r_squared': 0.945,
                    'sigma_m': [0.18e-6, 0.19e-6],
                    'peak_fluorescence_cps': 175000.0,
                },
                'pulsed_measurement': {
                    'success': True,
                    'save_tag': 'auto_nv_POI-001_a1b2c3',
                    'measurement_ensemble': 'T1_4us_100us',
                    'laser_pulse_ensemble': 'Laser_532nm_50us',
                    'elapsed_s': 42.5,
                    'run_id': 'meas_001',
                }
            },
            {
                'candidate_id': 'POI-002',
                'poi_name': 'NV_R001_POI-002',
                'accepted_position_m': [4e-6, -2e-6, z_current],
                'seed_position_m': [4.05e-6, -1.95e-6, z_current],
                'optical_stats': {
                    'r_squared': 0.912,
                    'sigma_m': [0.21e-6, 0.20e-6],
                    'peak_fluorescence_cps': 225000.0,
                },
                'pulsed_measurement': {
                    'success': True,
                    'save_tag': 'auto_nv_POI-002_d4e5f6',
                    'measurement_ensemble': 'T1_4us_100us',
                    'laser_pulse_ensemble': 'Laser_532nm_50us',
                    'elapsed_s': 39.8,
                    'run_id': 'meas_002',
                }
            }
        ]

        summary = logger.save_cell_data(
            scan_region=region,
            image_data=image,
            x_coords_m=x_coords,
            y_coords_m=y_coords,
            z_current_m=z_current,
            verified_pois=verified_pois,
            save_pdf=True)

        assert summary['region_id'] == 'R001'
        assert summary['nvs_verified_count'] == 2

        # Check generated files
        cell_dir = summary['cell_directory']
        assert os.path.exists(os.path.join(cell_dir, 'micro_scan_annotated.png'))
        assert os.path.exists(os.path.join(cell_dir, 'micro_scan_annotated.pdf'))
        assert os.path.exists(os.path.join(cell_dir, 'micro_scan_raw.npz'))
        assert os.path.exists(os.path.join(cell_dir, 'cell_summary.json'))
        assert os.path.exists(os.path.join(cell_dir, 'cell_pois.csv'))

        # Check raw NPZ contents
        npz = np.load(os.path.join(cell_dir, 'micro_scan_raw.npz'))
        assert 'image_xy' in npz
        assert 'fluorescence' in npz
        assert 'x_coords_m' in npz
        assert npz['fluorescence'].shape == (50, 50)

        # Check CSV content
        with open(os.path.join(cell_dir, 'cell_pois.csv'), 'r') as f_csv:
            rows = list(csv.reader(f_csv))
        assert len(rows) == 3  # 1 header + 2 data rows
        assert rows[1][1] == 'POI-001'
        assert rows[1][2] == 'NV_R001_POI-001'
        assert rows[1][12] == 'SUCCESS'
        assert rows[1][13] == 'auto_nv_POI-001_a1b2c3'

        # Check JSON content
        with open(os.path.join(cell_dir, 'cell_summary.json'), 'r') as f_json:
            cell_json = json.load(f_json)
        assert len(cell_json['verified_pois']) == 2
        assert 'pixel_col_interpolated' in cell_json['verified_pois'][0]
        assert 'pixel_row_interpolated' in cell_json['verified_pois'][0]

    def test_finalize_run(self, temp_dir, synthetic_scan_data):
        image, x_coords, y_coords, z_current = synthetic_scan_data
        logger = CellDataLogger(base_data_dir=temp_dir, run_id='test_run_003')

        region = ScanRegion(region_id='R001', bbox_physical=(-15e-6, 15e-6, -15e-6, 15e-6),
                            width_um=30.0, height_um=30.0)

        logger.save_cell_data(
            scan_region=region,
            image_data=image,
            x_coords_m=x_coords,
            y_coords_m=y_coords,
            z_current_m=z_current,
            verified_pois=[{
                'candidate_id': 'POI-101',
                'poi_name': 'NV_R001_POI-101',
                'accepted_position_m': [1e-6, 2e-6, z_current],
            }],
            save_pdf=False)

        report = logger.finalize_run(run_stats={'cells_completed': 1, 'total_nvs': 1})

        assert report['total_cells_processed'] == 1
        assert report['total_verified_nvs'] == 1

        # Check master CSV
        master_csv = report['master_csv_path']
        assert os.path.exists(master_csv)
        with open(master_csv, 'r') as f:
            rows = list(csv.reader(f))
        assert len(rows) == 2  # 1 header + 1 NV

        # Check finalized manifest
        with open(report['manifest_path'], 'r') as f:
            manifest = json.load(f)
        assert manifest['status'] == 'completed'
        assert manifest['run_stats']['total_nvs'] == 1
