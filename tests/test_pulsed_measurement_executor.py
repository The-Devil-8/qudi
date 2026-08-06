import pytest
from unittest.mock import MagicMock, PropertyMock, patch

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
    # Set status variables on class level or config
    PulsedMeasurementExecutor.measurement_ensemble_name = 'test_measurement'
    PulsedMeasurementExecutor.laser_pulse_ensemble_name = 'test_laser'
    PulsedMeasurementExecutor.measurement_timeout_s = 900.0
    PulsedMeasurementExecutor.post_measurement_settle_s = 2.0
    PulsedMeasurementExecutor.save_tag_prefix = 'auto_nv'
    
    executor = PulsedMeasurementExecutor(manager=None, name='test_executor', config={})
    
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
    executor.execute_measurement({'candidate_id': 'c1'})
    
    # After start, it should sequence through to WAIT_LOAD_COMPLETE
    assert executor._current_state == 'WAIT_LOAD_COMPLETE'
    mock_pml.sample_ensemble.assert_called_with('test_measurement', with_load=True)
    
    # Simulate ensemble loaded -> START_MEASUREMENT -> WAIT_MEASUREMENT
    executor._on_loaded_asset_updated('test_measurement', 'Ensemble')
    assert executor._current_state == 'WAIT_MEASUREMENT'
    mock_pml.toggle_pulsed_measurement.assert_called_with(True)
    
    # Simulate measurement finished -> SAVE_DATA -> POST_SETTLE
    executor._on_measurement_status_updated(is_running=False, is_paused=False)
    assert executor._current_state == 'POST_SETTLE'
    mock_pml.save_measurement_data.assert_called_once()
    
    # Simulate settle timer done -> PULSER_OFF_2 -> LOAD_LASER -> WAIT_LASER_LOADED
    executor._on_settle_done()
    assert executor._current_state == 'WAIT_LASER_LOADED'
    mock_pml.sample_ensemble.assert_called_with('test_laser', with_load=True)
    
    # Simulate laser repump loaded -> PULSER_ON -> COMPLETE -> IDLE
    executor._on_loaded_asset_updated('test_laser', 'Ensemble')
    assert executor._current_state == 'IDLE'
    mock_pml.toggle_pulse_generator.assert_called_with(True)


def test_timeout_handling(executor):
    mock_fail = MagicMock()
    executor._fail_measurement = mock_fail
    
    executor.execute_measurement({'candidate_id': 'c1'})
    assert executor._current_state == 'WAIT_LOAD_COMPLETE'
    
    # Trigger timeout
    executor._on_timeout()
    mock_fail.assert_called_once_with("Timeout during sequence execution.")


def test_result_dict_structure(executor):
    record = {
        'candidate_id': 'cand_123',
        'poi_name': 'poi_1',
        'accepted_position_m': [1.0, 2.0, 3.0]
    }
    
    mock_slot = MagicMock()
    executor.sigMeasurementComplete.connect(mock_slot)
    
    executor.execute_measurement(record)
    executor._finish_measurement(success=True, error=None)
    
    mock_slot.assert_called_once()
    result_dict = mock_slot.call_args[0][0]
    
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
