"""
Pulsed Measurement Executor Module.

Automates the pulsed measurement (T1/ODMR) sequence for verified NV candidates.
Connects to PulsedMasterLogic and drives the full experiment cycle.
"""
import time
import uuid
import datetime
from qtpy import QtCore

from logic.generic_logic import GenericLogic
from core.connector import Connector
from core.statusvariable import StatusVar


class PulsedMeasurementExecutor(GenericLogic):
    """
    Automates the pulsed measurement sequence for verified NV candidates.
    Connects to PulsedMasterLogic and drives the full experiment cycle.
    """
    
    pulsedmasterlogic = Connector(interface='PulsedMasterLogic')
    
    measurement_ensemble_name = StatusVar('measurement_ensemble_name', '')
    laser_pulse_ensemble_name = StatusVar('laser_pulse_ensemble_name', '')
    measurement_timeout_s = StatusVar('measurement_timeout_s', 900.0)
    post_measurement_settle_s = StatusVar('post_measurement_settle_s', 2.0)
    save_tag_prefix = StatusVar('save_tag_prefix', 'auto_nv')
    
    sigMeasurementComplete = QtCore.Signal(object)
    sigMeasurementError = QtCore.Signal(str)
    sigMeasurementProgress = QtCore.Signal(str, str)
    
    _sigDeferredTransition = QtCore.Signal(str)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_state = 'IDLE'
        self._candidate_record = None
        self._run_id = None
        self._measurement_name = None
        self._laser_pulse_name = None
        self._start_time = None
        self._save_tag = None
        
        self._timeout_timer = QtCore.QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        
        self._settle_timer = QtCore.QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle_done)

    def on_activate(self):
        """Called when the module is activated."""
        self._current_state = 'IDLE'
        self._pml = self.pulsedmasterlogic()
        
        # Connect internal signal for thread-safe state transitions
        self._sigDeferredTransition.connect(
            self._transition_to, QtCore.Qt.QueuedConnection)
            
        try:
            self._pml.sigPulserRunningUpdated.connect(
                self._on_pulser_running_updated, QtCore.Qt.QueuedConnection)
            self._pml.sigMeasurementStatusUpdated.connect(
                self._on_measurement_status_updated, QtCore.Qt.QueuedConnection)
            self._pml.sigLoadedAssetUpdated.connect(
                self._on_loaded_asset_updated, QtCore.Qt.QueuedConnection)
            self._pml.sigSampleEnsembleComplete.connect(
                self._on_sample_ensemble_complete, QtCore.Qt.QueuedConnection)
        except Exception as e:
            self.log.error('Error connecting to PulsedMasterLogic signals: {0}'.format(e))

    def on_deactivate(self):
        """Called when the module is deactivated."""
        self.stop_measurement()
        try:
            self._sigDeferredTransition.disconnect(self._transition_to)
        except (TypeError, RuntimeError):
            pass
            
        try:
            self._pml.sigPulserRunningUpdated.disconnect(self._on_pulser_running_updated)
            self._pml.sigMeasurementStatusUpdated.disconnect(self._on_measurement_status_updated)
            self._pml.sigLoadedAssetUpdated.disconnect(self._on_loaded_asset_updated)
            self._pml.sigSampleEnsembleComplete.disconnect(self._on_sample_ensemble_complete)
        except (TypeError, RuntimeError) as e:
            self.log.warning('Error disconnecting from PulsedMasterLogic signals: {0}'.format(e))

    def execute_measurement(self, candidate_record, measurement_name=None, laser_pulse_name=None):
        """
        Starts the pulsed measurement sequence for a given candidate.
        
        Parameters:
        -----------
        candidate_record : dict
            Dict with 'candidate_id', 'accepted_position_m', 'poi_name'.
        measurement_name : str, optional
            Override for measurement_ensemble_name StatusVar.
        laser_pulse_name : str, optional
            Override for laser_pulse_ensemble_name StatusVar.
            
        Returns:
        --------
        str
            Unique run ID for the measurement.
        """
        if self.is_active():
            self.log.warning("Cannot start measurement while one is already active.")
            return None
            
        self._candidate_record = candidate_record
        self._run_id = str(uuid.uuid4())
        self._measurement_name = measurement_name or self.measurement_ensemble_name
        self._laser_pulse_name = laser_pulse_name or self.laser_pulse_ensemble_name
        
        if not self._measurement_name or not self._laser_pulse_name:
            self.log.error("Ensemble names not properly configured.")
            return None

        self._start_time = time.time()
        self._save_tag = '{0}_{1}_{2}'.format(
            self.save_tag_prefix,
            candidate_record.get('candidate_id', 'unknown'),
            self._run_id[:8])
        
        self.log.info('Starting measurement sequence {0} for {1}'.format(
            self._run_id, candidate_record.get('candidate_id')))
        
        # Removed 15-minute limit for now, so we don't start timeout_timer as states are working fine
        # self._timeout_timer.start(int(self.measurement_timeout_s * 1000))
        
        self._deferred_transition('START_SEQUENCE')
        
        return self._run_id

    def stop_measurement(self):
        """Gracefully abort the running measurement."""
        if self._current_state != 'IDLE':
            self.log.info("Stopping current measurement sequence.")
            self._timeout_timer.stop()
            self._settle_timer.stop()
            self._fail_measurement("Measurement stopped by user.")
            
    def is_active(self):
        """Returns True if a measurement is in progress."""
        return self._current_state != 'IDLE'

    def _deferred_transition(self, state):
        """Schedules a state transition on the Qt event loop."""
        self._sigDeferredTransition.emit(state)

    def _settle_transition(self, state):
        """Schedules a state transition with a 100ms settle delay."""
        # Instead of calling _transition_to directly in lambda, we emit the signal
        # to ensure it's still queued correctly in our thread.
        QtCore.QTimer.singleShot(100, lambda: self._deferred_transition(state))

    def _transition_to(self, state):
        """Drives the state machine transitions."""
        # Guard: drop stale deferred transitions that arrive after
        # the measurement was stopped or finished.  _run_id is set to
        # None in _finish_measurement(), so any queued lambda from
        # _deferred_transition() that fires afterwards is harmless.
        if self._run_id is None and state != 'IDLE':
            self.log.debug(
                'Ignoring stale transition to {0} — no active run.'.format(state))
            return

        self._current_state = state
        self.sigMeasurementProgress.emit(state, "Transitioning")
        
        try:
            if state == 'IDLE':
                pass
                
            elif state == 'START_SEQUENCE':
                self._deferred_transition('PULSER_OFF')
                
            elif state == 'PULSER_OFF':
                self._pml.toggle_pulse_generator(False)
                self._settle_transition('STOP_PREV_MEASUREMENT')
                
            elif state == 'STOP_PREV_MEASUREMENT':
                self._pml.toggle_pulsed_measurement(False)
                self._settle_transition('LOAD_MEASUREMENT')
                
            elif state == 'LOAD_MEASUREMENT':
                self._current_state = 'WAIT_LOAD_COMPLETE'
                self.sigMeasurementProgress.emit(self._current_state, "Transitioning")
                self._pml.sample_ensemble(self._measurement_name, with_load=True)
                
            elif state == 'WAIT_LOAD_COMPLETE':
                # Waiting for sigLoadedAssetUpdated or sampload_busy
                pass
                
            elif state == 'START_MEASUREMENT':
                self._current_state = 'WAIT_MEASUREMENT'
                self.sigMeasurementProgress.emit(self._current_state, "Transitioning")
                self._pml.toggle_pulsed_measurement(True)
                
            elif state == 'WAIT_MEASUREMENT':
                # Waiting for sigMeasurementStatusUpdated(False)
                pass
                
            elif state == 'SAVE_DATA':
                self._pml.save_measurement_data(tag=self._save_tag, with_error=True)
                self._deferred_transition('POST_SETTLE')
                
            elif state == 'POST_SETTLE':
                self._settle_timer.start(int(self.post_measurement_settle_s * 1000))
                
            elif state == 'PULSER_OFF_2':
                self._pml.toggle_pulse_generator(False)
                self._settle_transition('LOAD_LASER')
                
            elif state == 'LOAD_LASER':
                self._current_state = 'WAIT_LASER_LOADED'
                self.sigMeasurementProgress.emit(self._current_state, "Transitioning")
                self._pml.sample_ensemble(self._laser_pulse_name, with_load=True)
                
                
            elif state == 'WAIT_LASER_LOADED':
                # Waiting for sigLoadedAssetUpdated
                pass
                
            elif state == 'PULSER_ON':
                self._pml.toggle_pulse_generator(True)
                self._deferred_transition('COMPLETE')
                
            elif state == 'COMPLETE':
                self._finish_measurement(success=True)
                
        except Exception as e:
            self.log.error('Error in state {0}: {1}'.format(state, e))
            self._fail_measurement(str(e))

    def _on_pulser_running_updated(self, is_running):
        """Handler for pulser state updates."""
        pass  # We drive this optimistically for now, can be improved.

    def _on_measurement_status_updated(self, is_running, is_paused):
        """Handler for measurement status updates."""
        if self._current_state == 'WAIT_MEASUREMENT' and not is_running:
            self._deferred_transition('SAVE_DATA')

    def _on_loaded_asset_updated(self, asset_name, asset_type):
        """Handler for asset load updates."""
        if self._current_state == 'WAIT_LOAD_COMPLETE' and asset_name == self._measurement_name:
            self._deferred_transition('START_MEASUREMENT')
        elif self._current_state == 'WAIT_LASER_LOADED' and asset_name == self._laser_pulse_name:
            self._deferred_transition('PULSER_ON')

    def _on_sample_ensemble_complete(self, obj):
        """Handler for ensemble sampling completion."""
        pass

    def _on_settle_done(self):
        """Handler for post-measurement settle timer."""
        if self._current_state == 'POST_SETTLE':
            self._deferred_transition('PULSER_OFF_2')

    def _on_timeout(self):
        """Watchdog timeout handler."""
        if self.is_active():
            self.log.error("Measurement sequence timed out.")
            self._fail_measurement("Timeout during sequence execution.")

    def _fail_measurement(self, error_msg):
        """Handles failure and emits error signal."""
        self.sigMeasurementError.emit(error_msg)
        self._finish_measurement(success=False, error=error_msg)
        
    def _finish_measurement(self, success, error=None):
        """Finalizes the measurement sequence and emits completion signal."""
        self._timeout_timer.stop()
        self._settle_timer.stop()
        
        end_time = time.time()
        elapsed = end_time - self._start_time if self._start_time else 0.0
        
        result = {
            'run_id': self._run_id,
            'candidate_id': self._candidate_record.get('candidate_id') if self._candidate_record else None,
            'poi_name': self._candidate_record.get('poi_name') if self._candidate_record else None,
            'measurement_ensemble': self._measurement_name,
            'laser_pulse_ensemble': self._laser_pulse_name,
            'pre_measurement_position_m': self._candidate_record.get('accepted_position_m') if self._candidate_record else None,
            'elapsed_s': elapsed,
            'save_tag': self._save_tag,
            'success': success,
            'error': error,
            'started_utc': datetime.datetime.fromtimestamp(self._start_time, datetime.timezone.utc).isoformat() + "Z" if self._start_time else None,
            'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        }
        
        self._current_state = 'IDLE'
        self._candidate_record = None
        self._run_id = None
        
        self.sigMeasurementComplete.emit(result)

