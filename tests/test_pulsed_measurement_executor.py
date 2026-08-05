import pytest
from unittest.mock import MagicMock, patch

# Skip all tests if qtpy is not available
QtCore = pytest.importorskip('qtpy.QtCore')

from logic.pulsed_measurement_executor import PulsedMeasurementExecutor

class MockPulsedMasterLogic(QtCore.QObject):
    sigPulserRunningUpdated = QtCore.Signal(bool)
    sigMeasurementStatusUpdated = QtCore.Signal(bool, bool)
    sigLoadedAssetUpdated = QtCore.Signal(str, str)
    sigSampleEnsembleComplete = QtCore.Signal(object)
    
    def __init__(self):
        super().__init__()
        self.toggle_pulse_generator = MagicMock()
        self.toggle_pulsed_measurement = MagicMock()
        self.sample_ensemble = MagicMock()
        self.save_measurement_data = MagicMock()

@pytest.fixture
def mock_pml():
    return MockPulsedMasterLogic()

@pytest.fixture
def executor(mock_pml):
    with patch('logic.pulsed_measurement_executor.Connector') as MockConnector:
        # Prevent GenericLogic from trying to connect to real manager
        with patch('logic.generic_logic.GenericLogic.__init__', return_value=None):
            executor = PulsedMeasurementExecutor()
            # Set up the signals that GenericLogic would normally have
            executor.sigMeasurementComplete = QtCore.Signal(object)
            executor.sigMeasurementError = QtCore.Signal(str)
            executor.sigMeasurementProgress = QtCore.Signal(str, str)
            
            # Recreate the init logic that was skipped
            executor._current_state = 'IDLE'
            executor._candidate_record = None
            executor._run_id = None
            executor._measurement_name = None
            executor._laser_pulse_name = None
            executor._start_time = None
            executor._save_tag = None
            
            executor._timeout_timer = MagicMock()
            executor._settle_timer = MagicMock()
            
            executor.log = MagicMock()
            
            # Setup StatusVars manually
            executor.measurement_ensemble_name = 'test_measurement'
            executor.laser_pulse_ensemble_name = 'test_laser'
            executor.measurement_timeout_s = 900.0
            executor.post_measurement_settle_s = 2.0
            executor.save_tag_prefix = 'auto_nv'
            
            # Mock the connector to return our mock PML
            executor.pulsedmasterlogic = MagicMock(return_value=mock_pml)
            
            # Override _deferred_transition to happen synchronously for testing
            executor._deferred_transition = lambda state: executor._transition_to(state)
            
            executor.on_activate()
            yield executor
            executor.on_deactivate()

def test_initialization(executor):
    assert executor._current_state == 'IDLE'
    assert hasattr(executor, 'sigMeasurementComplete')
    assert hasattr(executor, 'sigMeasurementError')
    assert hasattr(executor, 'sigMeasurementProgress')
    assert not executor.is_active()

def test_execute_measurement_validation(executor):
    # Test rejection with empty ensemble names
    executor.measurement_ensemble_name = ''
    run_id = executor.execute_measurement({'candidate_id': 'c1'})
    assert run_id is None
    assert not executor.is_active()
    
    # Restore valid names
    executor.measurement_ensemble_name = 'test_meas'
    executor.laser_pulse_ensemble_name = 'test_laser'
    
    # Start measurement
    run_id = executor.execute_measurement({'candidate_id': 'c1'})
    assert run_id is not None
    assert executor.is_active()
    
    # Test rejection when already active
    run_id2 = executor.execute_measurement({'candidate_id': 'c2'})
    assert run_id2 is None

def test_state_machine_transitions(executor, mock_pml):
    # Stop deferred transitions from chaining completely automatically for some states
    # Actually, in our synchronous mock, it will zip through states until it hits a WAIT state
    executor.execute_measurement({'candidate_id': 'c1'})
    
    # After start, it should sequence through to WAIT_LOAD_COMPLETE
    assert executor._current_state == 'WAIT_LOAD_COMPLETE'
    mock_pml.toggle_pulse_generator.assert_called_with(False)
    mock_pml.sample_ensemble.assert_called_with('test_measurement', with_load=True)
    
    # Trigger asset load completion for measurement
    executor._on_loaded_asset_updated('test_measurement', 'Ensemble')
    
    # It should transition to START_MEASUREMENT and then WAIT_MEASUREMENT
    assert executor._current_state == 'WAIT_MEASUREMENT'
    mock_pml.toggle_pulsed_measurement.assert_called_with(True)
    
    # Trigger measurement completion
    executor._on_measurement_status_updated(False, False)
    
    # It should transition to SAVE_DATA, then POST_SETTLE
    assert executor._current_state == 'POST_SETTLE'
    mock_pml.save_measurement_data.assert_called_once()
    executor._settle_timer.start.assert_called_once()

def test_timeout_handling(executor):
    executor.execute_measurement({'candidate_id': 'c1'})
    
    # Verify timeout timer was started with correct duration (900s = 900000ms)
    executor._timeout_timer.start.assert_called_with(900000)
    
    # Trigger timeout
    mock_fail = MagicMock()
    executor._fail_measurement = mock_fail
    executor._on_timeout()
    
    mock_fail.assert_called_once_with("Timeout during sequence execution.")

def test_result_dict_structure(executor):
    record = {
        'candidate_id': 'cand_123',
        'poi_name': 'poi_1',
        'accepted_position_m': [1.0, 2.0, 3.0]
    }
    
    mock_slot = MagicMock()
    # Mocking the signal emit doesn't work easily with Qt Signals,
    # so we mock _finish_measurement directly to check the dict, 
    # but instead we can patch sigMeasurementComplete.emit
    
    with patch.object(executor.sigMeasurementComplete, 'emit') as mock_emit:
        executor.execute_measurement(record)
        executor._finish_measurement(success=True, error=None)
        
        mock_emit.assert_called_once()
        result_dict = mock_emit.call_args[0][0]
        
        assert 'run_id' in result_dict
        assert result_dict['candidate_id'] == 'cand_123'
        assert result_dict['poi_name'] == 'poi_1'
        assert result_dict['measurement_ensemble'] == 'test_measurement'
        assert result_dict['laser_pulse_ensemble'] == 'test_laser'
        assert result_dict['pre_measurement_position_m'] == [1.0, 2.0, 3.0]
        assert 'elapsed_s' in result_dict
        assert 'save_tag' in result_dict
        assert result_dict['success'] is True
        assert result_dict['error'] is None
        assert 'started_utc' in result_dict
        assert 'finished_utc' in result_dict

def test_is_active(executor):
    assert not executor.is_active()
    executor.execute_measurement({'candidate_id': 'c1'})
    assert executor.is_active()
    executor._finish_measurement(True)
    assert not executor.is_active()
