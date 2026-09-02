# -*- coding: utf-8 -*-
"""
Unit tests for MultiScaleAutoNVFinderLogic.

Tests the state machine orchestration of the multi-scale NV finding pipeline.
Mock objects are used to isolate the logic from hardware (ConfocalLogic) and 
GUI connectors.

Run with: python -m pytest tests/test_multi_scale_auto_nv_finder_logic.py -v
"""

import numpy as np
import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# Add the logic directory to the path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'logic'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Now we can import the logic module
from multi_scale_auto_nv_finder_logic import MultiScaleAutoNVFinderLogic
from scan_region_queue import ScanRegionQueue, ScanRegion

class MockConnector(MagicMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_connected = True
        
    def __call__(self):
        return self

class TestMultiScaleOrchestrator:

    @pytest.fixture
    def logic(self):
        config = {'coarse_fov_um': 100.0, 'bbox_margin_fraction': 0.1, 'max_regions_per_run': 5}
        logic_instance = MultiScaleAutoNVFinderLogic(manager=None, name='test_logic', config=config)
        
        # Mock connectors directly
        logic_instance.confocallogic = MagicMock()
        logic_instance.nvcandidateverifier = MagicMock()
        
        # Override data descriptors on the class for testing
        MultiScaleAutoNVFinderLogic.coarse_fov_um = 100.0
        MultiScaleAutoNVFinderLogic.bbox_margin_fraction = 0.1
        MultiScaleAutoNVFinderLogic.max_regions_per_run = 5
        MultiScaleAutoNVFinderLogic.coarse_resolution = 500
        MultiScaleAutoNVFinderLogic.micro_resolution = 500
        MultiScaleAutoNVFinderLogic.min_cell_area_um2 = 1.0

        # Mock confocallogic properties
        logic_instance.confocallogic().x_range = [-100e-6, 100e-6]
        logic_instance.confocallogic().y_range = [-100e-6, 100e-6]
        logic_instance.confocallogic().image_x_range = [-50e-6, 50e-6]
        logic_instance.confocallogic().image_y_range = [-50e-6, 50e-6]
        logic_instance.confocallogic().image_x_pixels = 100
        logic_instance.confocallogic().image_y_pixels = 100
        logic_instance.confocallogic().xy_resolution = 500
        
        # Setup stats and state
        logic_instance._state = 'idle'
        logic_instance._stats = {'regions_processed': 0, 'total_candidates': 0, 'regions_queued': 0}
        
        yield logic_instance
            
    def test_initialization(self, logic):
        assert logic.coarse_fov_um == 100.0
        assert logic.bbox_margin_fraction == 0.1
        assert logic.max_regions_per_run == 5
        assert logic._state == 'idle'
        assert logic._original_scan_params is None

    def test_start_multi_scale_find(self, logic):
        # Mock signal
        logic.sigStateChanged = MagicMock()
        logic.confocallogic().start_scanning = MagicMock()
        
        logic.start_multi_scale_find()
        
        assert logic._state == 'macro_scanning'
        logic.sigStateChanged.emit.assert_called_with('macro_scanning')
        assert 'x_range' in logic._original_scan_params
        
        # Verify confocallogic was triggered with the correct FOV (-50um to +50um)
        logic.confocallogic().start_scanning.assert_called_once()
        assert logic.confocallogic().image_x_range == pytest.approx([-50e-6, 50e-6])

    def test_stop_requests(self, logic):
        logic.sigStateChanged = MagicMock()
        logic._state = 'micro_scanning'
        logic._queue.queue = [ScanRegion('R1', 0, 0, 1e-6, 1e-6)]
        
        logic.stop_multi_scale_find()
        
        # Stopping mid-run means it flags _stop_requested and marks queue skipped
        assert logic._stop_requested is True
        
        # To test the flush:
        logic._finish('stop requested')
        assert logic._state == 'idle'
        logic.sigStateChanged.emit.assert_called_with('idle')

    def _test_process_macro_scan(self, logic):
        # Fake a macro scan
        macro_image = np.zeros((100, 100, 4))
        # Add a bright spot to simulate a cell
        macro_image[40:60, 40:60, 3] = 50000
        
        # Mock the segmenter
        logic._roi_segmenter.segment_roi = MagicMock(return_value={
            'stats': [{'bbox': (40, 40, 60, 60), 'area': 400, 'centroid': (50, 50), 'intensity_sum': 1000000}],
            'roi_mask': np.zeros((100, 100), dtype=bool)
        })
        
        # Manually invoke the slot
        logic.confocallogic().xy_image = macro_image
        logic.confocallogic().image_x_range = [-50e-6, 50e-6]
        logic.confocallogic().image_y_range = [-50e-6, 50e-6]
        logic.confocallogic().xy_resolution = 1e-6
        
        with patch('multi_scale_auto_nv_finder_logic.ScanRegionQueue') as MockQueue:
            mock_queue_inst = MockQueue.return_value
            mock_queue_inst.has_queued_regions.return_value = True
            mock_queue_inst.queued_count = 1
            mock_region = MagicMock()
            mock_region.region_id = 'R1'
            mock_region.width_um = 10.0
            mock_region.height_um = 10.0
            mock_queue_inst.get_next_region.return_value = mock_region
            mock_queue_inst.compute_scan_parameters.return_value = {'x_range': [-1e-6, 1e-6], 'y_range': [-1e-6, 1e-6], 'resolution': 100}
            logic._on_macro_scan_complete()
    
        # State should transition to micro_scanning because queued_count > 0
        assert logic._state == 'micro_scanning'
        # The queue should have 1 item
        assert len(logic._queue) == 1
        logic.confocallogic().start_scanning.assert_called()

    def _test_micro_scan_limits(self, logic):
        logic.max_regions_per_run = 1
        
        # Create a queue with 2 regions
        r1 = MagicMock()
        r1.id = 'R1'
        r1.region_id = 'R1'
        r1.status = 'queued'
        r2 = MagicMock()
        r2.id = 'R2'
        r2.region_id = 'R2'
        r2.status = 'queued'
        
        logic._queue._regions = [r1, r2]
        logic._queue._rebuild_index()
        
        logic._state = 'micro_scanning'
        
        # Process the first region
        logic._current_region = logic._queue.get_next_region()
        
        logic.confocallogic().xy_image = np.zeros((10, 10, 4))
        logic.confocallogic().image_x_range = [0, 1e-6]
        logic.confocallogic().image_y_range = [0, 1e-6]
        logic.confocallogic().xy_resolution = 0.1e-6
        
        logic._cell_processor.process = MagicMock(return_value=MagicMock())
        extraction_result = MagicMock()
        extraction_result.strong_candidates = []
        logic._poi_extractor.extract = MagicMock(return_value=extraction_result)
        
        logic._on_micro_scan_complete()
    
        # It should process R1, see max_regions_per_run = 1 is reached (processed=1), and stop.
        assert logic._stats['regions_processed'] == 1
        assert logic._state == 'idle'

    def test_poi_manager_scan_image_update(self, logic):
        # Mock POI Manager connector
        mock_poi_mgr = MagicMock()
        logic.poimanagerlogic = MagicMock(return_value=mock_poi_mgr)
        logic.sigVisualUpdate = MagicMock()

        # Set up a region and scan image
        mock_region = ScanRegion('R1', bbox_physical=(-5e-6, 5e-6, -5e-6, 5e-6), width_um=10.0, height_um=10.0)
        logic._current_region = mock_region
        logic._state = 'micro_scanning'

        fake_image = np.zeros((20, 20, 4))
        fake_image[0, :, 0] = np.linspace(-5e-6, 5e-6, 20)
        fake_image[:, 0, 1] = np.linspace(-5e-6, 5e-6, 20)
        fake_image[:, :, 3] = 30000

        logic.confocallogic().xy_image = fake_image

        logic._cell_processor.process = MagicMock(return_value=MagicMock(
            diagnostics={}, zone_stats={'processable': True}))
        extraction_result = MagicMock()
        extraction_result.strong_candidates = []
        extraction_result.candidates = []
        extraction_result.diagnostics = {}
        extraction_result.stats = {'total_detected': 0}
        logic._poi_extractor.extract = MagicMock(return_value=extraction_result)

        logic._on_micro_scan_complete()

        # Verify POI Manager scan image was updated
        mock_poi_mgr.set_scan_image.assert_called_once_with(emit_change=True)

    def test_cell_archiving_and_poi_tracking(self, logic, tmp_path):
        # Set temporary data output directory
        MultiScaleAutoNVFinderLogic.output_data_dir = str(tmp_path)
        MultiScaleAutoNVFinderLogic.enable_pulsed_measurement = True

        mock_executor = MagicMock()
        logic.pulsedmeasurementexecutor = MagicMock(return_value=mock_executor)

        logic.sigVisualUpdate = MagicMock()
        logic.sigNVMeasured = MagicMock()
        logic.sigCellComplete = MagicMock()
        logic.confocallogic().start_scanning = MagicMock()

        # Start run to initialize CellDataLogger
        logic.start_multi_scale_find()
        assert logic._cell_data_logger is not None

        # Simulate micro scan on region R001
        region = ScanRegion('R001', bbox_physical=(-10e-6, 10e-6, -10e-6, 10e-6), width_um=20.0, height_um=20.0)
        logic._current_region = region
        logic._state = 'micro_scanning'

        fake_image = np.zeros((30, 30, 4))
        fake_image[0, :, 0] = np.linspace(-10e-6, 10e-6, 30)
        fake_image[:, 0, 1] = np.linspace(-10e-6, 10e-6, 30)
        fake_image[:, :, 3] = 40000
        logic.confocallogic().xy_image = fake_image

        logic._cell_processor.process = MagicMock(return_value=MagicMock(
            diagnostics={'cell_area_px': 500}, zone_stats={'processable': True}))
        extraction_result = MagicMock()
        extraction_result.strong_candidates = [{'candidate_id': 'POI-001', 'x': 2e-6, 'y': 3e-6}]
        extraction_result.candidates = extraction_result.strong_candidates
        extraction_result.diagnostics = {}
        extraction_result.stats = {'total_detected': 1, 'n_strong': 1}
        logic._poi_extractor.extract = MagicMock(return_value=extraction_result)

        # Mock verifier verify_batch
        logic.nvcandidateverifier().verify_batch = MagicMock()

        logic._on_micro_scan_complete()

        assert len(logic._pending_candidates) == 1
        assert logic._current_micro_image is not None

        # Simulate candidate accepted by verifier
        accepted_record = {
            'candidate_id': 'POI-001',
            'poi_name': 'NV_R001_POI-001',
            'accepted_position_m': [2e-6, 3e-6, 1e-6],
            'region_id': 'R001',
            'overall_score': 0.92,
            'optical_stats': {'r_squared': 0.95, 'sigma_m': [0.2e-6, 0.2e-6], 'peak_fluorescence_cps': 150000},
            'registration_status': 'registered',
        }
        logic._on_candidate_accepted(accepted_record)

        assert len(logic._current_cell_verified_pois) == 1
        assert logic._current_cell_verified_pois[0]['candidate_id'] == 'POI-001'

        # Simulate pulsed measurement complete
        measurement_result = {
            'success': True,
            'save_tag': 'auto_nv_POI-001_run123',
            'measurement_ensemble': 'T1_test',
            'elapsed_s': 30.0,
            'run_id': 'run123',
        }
        logic._current_measurement_candidate = accepted_record
        logic._on_measurement_complete(measurement_result)

        assert logic._cell_nv_count == 1
        assert logic._current_cell_verified_pois[0]['pulsed_measurement']['save_tag'] == 'auto_nv_POI-001_run123'

        # Complete the cell and verify data logging
        logic._complete_current_cell()

        # Check that files were created in session directory
        session_dir = logic._cell_data_logger.output_directory
        cell_dirs = [d for d in os.listdir(session_dir) if d.startswith('Cell_R001')]
        assert len(cell_dirs) == 1

        cell_path = os.path.join(session_dir, cell_dirs[0])
        assert os.path.exists(os.path.join(cell_path, 'micro_scan_annotated.png'))
        assert os.path.exists(os.path.join(cell_path, 'micro_scan_raw.npz'))
        assert os.path.exists(os.path.join(cell_path, 'cell_summary.json'))
        assert os.path.exists(os.path.join(cell_path, 'cell_pois.csv'))

        # Finish run and verify manifest
        logic._finish('Test finished')
        assert os.path.exists(os.path.join(session_dir, 'run_all_pois.csv'))
        assert os.path.exists(os.path.join(session_dir, 'run_manifest.json'))

