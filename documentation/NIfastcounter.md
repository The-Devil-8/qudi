# NIFastCounter

`hardware\NIfastcounter.py` implements a National Instruments based fast counter that can operate in two modes:

- **Analog mode** (`Count_Type = '0'`): reads an analog sync channel and an analog photodetector channel.
- **Digital mode** (`Count_Type = '1'`): uses NI counter tasks and PFI lines for photon counting and sync timing.

The module inherits from both `FastCounterInterface` and `SlowCounterInterface`, so it can be plugged into Qudi logic that expects either interface.

## How it works

1. **Configuration**  
   The module reads channel names from the Qudi config:
   - `PD_Channel`
   - `Clock_Channel`, `Clock_Channel2`, `Clock_Channel3`
   - `Digital_Channel`
   - `Sync_Channel`
   - `AI_Channel`
   - `Count_Type`

2. **Activation**  
   `on_activate()` connects Qt signals used to start acquisition and to run the readout loop.

3. **Fast-counter setup**  
   `configure(bin_width_s, record_length_s, number_of_gates)` stores the requested settings, allocates the histogram buffer, and computes NI timing values from the requested bin width and record length.

4. **Acquisition**  
   - `start_measure()` starts the measurement and emits the readout signal.
   - `start()` creates NI tasks for the selected mode.
   - `get_data_trace()` reads the current NI buffers, extracts the pulse regions, accumulates them into `data_trace`, and returns `(data_trace, info_dict)`.
   - `stop_measure()` stops and clears the NI tasks.

5. **Status and helpers**  
   - `get_binwidth()` returns the effective bin width in seconds.
   - `is_gated()` currently reports `False`.
   - The slow-counter compatibility methods are mostly stubs.

## Linked modules

| Module | Role |
| --- | --- |
| `interface\fast_counter_interface.py` | Defines the fast-counter API this class must provide. |
| `interface\slow_counter_interface.py` | Defines the slow-counter compatibility methods. |
| `logic\pulsed\pulsed_measurement_logic.py` | Main consumer; it queries hardware constraints, configures the counter, and pulls traces. |
| `logic\pulsed\pulsed_master_logic.py` | Relays fast-counter settings to pulsed measurement logic through queued Qt signals. |
| `logic\singleshot_logic.py` | Generic consumer of `FastCounterInterface` devices for raw trace processing. |

In practice, `PulsedMeasurementLogic` is the primary module that drives this hardware. It uses:

- `get_constraints()['hardware_binwidth_list']` to validate bin width
- `is_gated()` to decide whether gated acquisition is possible
- `configure()` before a measurement
- `get_data_trace()` to retrieve histogram data

## Typical use

This module is useful when an experiment already has NI DAQ hardware available and the acquisition can be handled through NI counters or analog inputs instead of a dedicated time-tagging device.

Example config style:

```yaml
NIfastcounter:
    module.Class: 'NIfastcounter.NIFastCounter'
    PD_Channel: '/Dev1/PFI8'
    Clock_Channel: '/Dev1/Ctr2'
    Clock_Channel2: '/Dev1/Ctr1'
    Clock_Channel3: '/Dev1/Ctr0'
    Digital_Channel: '/Dev1/PFI0'
    Count_Type: '1'
    Sync_Channel: 'Dev1/ai3'
    AI_Channel: 'Dev1/ai2'
```

Use `Count_Type = '0'` for the analog workflow and `Count_Type = '1'` for the digital counter workflow.

## Improvements

- Split the analog and digital code paths into smaller helpers.
- Replace broad `except:` blocks with explicit error handling and logging.
- Remove `print()` debugging and use the module logger.
- Make the units consistent in comments and variable names (`s` vs `ns`).
- Implement a real `get_status()` state machine.
- Fill in the slow-counter compatibility methods or drop the unused interface if it is not needed.
- Add tests or simulated hardware coverage for both acquisition modes.

## Shortcomings

- The file header/docstring still refers to PicoHarp300, which is misleading.
- `get_counter()` returns `0`, so slow-counter usage is only nominal.
- `is_gated()` always returns `False`.
- `elapsed_time` in the returned `info_dict` is always `None`.
- The module uses broad exception handling in several places, which can hide real NI/DAQ errors.
- The analog and digital acquisition paths duplicate logic and share mutable state.
- Some comments and variable names suggest nanoseconds, while the interface and `configure()` effectively work in seconds.

## DAQmx call map

`hardware\NIfastcounter.py` imports `PyDAQmx as daq`. These are the direct DAQ-linked calls used by the module:

### Methods that call DAQmx APIs

| Class method | DAQmx calls | Role |
| --- | --- | --- |
| `start(acq_time)` | `daq.Task()`, `daq.int32()`, `daq.c_ulong()`, `daq.c_uint64()` | Creates DAQ tasks and read buffers. |
| `start(acq_time)` analog branch | `CreateAIVoltageChan()`, `CfgAnlgEdgeStartTrig()`, `CfgSampClkTiming()`, `StartTask()` | Configures analog sync/PD acquisition and starts the task. |
| `start(acq_time)` digital branch | `CreateCOPulseChanFreq()`, `CfgImplicitTiming()`, `CreateCISemiPeriodChan()`, `SetCISemiPeriodTerm()`, `SetCICtrTimebaseSrc()`, `StartTask()` | Configures the pulse clock and counter tasks for digital counting. |
| `get_data_trace()` analog branch | `ReadAnalogF64()` | Reads the analog sync and detector waveforms. |
| `get_data_trace()` digital branch | `ReadCounterU32()` | Reads the counter buffers for sync and photon counting. |
| `stop_device()` | `StopTask()`, `ClearTask()` | Stops and releases all active DAQ tasks. |
| `stop_measure()` | `StopTask()`, `ClearTask()` | Same cleanup path used when a measurement stops. |

### DAQmx constants used

| Constant | Used for |
| --- | --- |
| `daq.DAQmx_Val_Diff` | Differential analog input mode. |
| `daq.DAQmx_Val_Volts` | Analog input units. |
| `daq.DAQmx_Val_RisingSlope` | Analog start trigger edge. |
| `daq.DAQmx_Val_Falling` | Sample clock edge. |
| `daq.DAQmx_Val_FiniteSamps` | Finite analog sampling. |
| `daq.DAQmx_Val_Hz` | Counter output frequency units. |
| `daq.DAQmx_Val_Low` | Counter output idle state. |
| `daq.DAQmx_Val_ContSamps` | Continuous counter timing. |
| `daq.DAQmx_Val_Ticks` | Counter semi-period return units. |
| `daq.DAQmx_Val_GroupByChannel` | Analog buffer read layout. |

## Read call examples

The module uses **PyDAQmx** low-level read calls, not `nidaqmx.stream_readers`. The closest equivalent idea is the same: configure the task first, then read a buffer of samples from the task input stream.

### `ReadAnalogF64()`

```python
self.analog_input2.ReadAnalogF64(
    self.numSampsPerChan,
    timeout,
    daq.DAQmx_Val_GroupByChannel,
    self.myNIdata,
    self.NumberofSamples * self.Nchannel,
    ctypes.byref(self.read2),
    None,
)
```

**Desc**
- Reads floating-point analog samples from the NI analog input task.
- Used here in the analog fast-counter path to read the sync channel and the photodetector channel together.
- The task was previously configured with `CreateAIVoltageChan()`, `CfgAnlgEdgeStartTrig()`, and `CfgSampClkTiming()`.

**Arguments in this module**
| Argument | Value in code | Meaning |
| --- | --- | --- |
| `numSampsPerChan` | `self.numSampsPerChan` | Number of samples to read per channel. |
| `timeout` | `10.0` | Maximum wait time in seconds before DAQmx raises a timeout error. |
| `fillMode` | `daq.DAQmx_Val_GroupByChannel` | Returned data is grouped by channel: all samples from channel 1, then channel 2. |
| `readArray` | `self.myNIdata` | Destination NumPy buffer that receives the samples. |
| `arraySizeInSamps` | `self.NumberofSamples * self.Nchannel` | Total capacity of the destination buffer. |
| `sampsPerChanRead` | `ctypes.byref(self.read2)` | Output parameter that receives the actual number of samples read per channel. |
| `reserved` | `None` | Unused reserved parameter. |

**Data layout in this module**
- `self.myNIdata[0:self.NumberofSamples]` contains the sync channel.
- `self.myNIdata[self.NumberofSamples:self.NumberofSamples * 2]` contains the photodetector channel.
- The code then detects sync pulses with `np.argwhere(Sync > 1.5)` and accumulates the laser signal between consecutive sync edges.

**Failure behavior**
- If the task does not produce enough samples before `timeout`, DAQmx raises a timeout error.
- If the trigger or sampling configuration is wrong, the read may fail even if the task starts successfully.

### `ReadCounterU32()`

```python
self.Counter1.ReadCounterU32(
    2 * samples,
    _RWTimeout,
    self.count_data[0],
    2 * samples,
    byref(n_read_samples),
    None,
)
```

```python
self.Counter2.ReadCounterU32(
    2 * samples,
    _RWTimeout,
    self.count_data2[0],
    2 * samples,
    byref(n_read_samples),
    None,
)
```

**Desc**
- Reads unsigned 32-bit counter samples from a counter task.
- Used here in the digital fast-counter path for the sync counter and the photon counter.
- The tasks were previously configured with `CreateCOPulseChanFreq()`, `CreateCISemiPeriodChan()`, `SetCISemiPeriodTerm()`, `SetCICtrTimebaseSrc()`, and `CfgImplicitTiming()`.

**Arguments in this module**
| Argument | Value in code | Meaning |
| --- | --- | --- |
| `numSampsPerChan` | `2 * samples` | Number of counter samples requested. |
| `timeout` | `_RWTimeout` (`2` seconds) | Maximum wait time before DAQmx raises a timeout error. |
| `readArray` | `self.count_data[0]` / `self.count_data2[0]` | Destination NumPy buffer for the counter values. |
| `arraySizeInSamps` | `2 * samples` | Capacity of the destination buffer. |
| `sampsPerChanRead` | `byref(n_read_samples)` | Output parameter that receives the number of samples actually read. |
| `reserved` | `None` | Unused reserved parameter. |

**Using the result**
- `self.count_data` is treated as the sync stream.
- `self.count_data2` is treated as the photon-count stream.
- The code finds sync pulse locations with `np.argwhere(Sync > 0.5)`.
- It then sums the `Laser` values between successive sync indices to build `self.data_trace`.

**Failure behavior**
- If the counter buffer does not contain enough samples before `_RWTimeout`, DAQmx raises a timeout error.
- If the counter task is misrouted or the timebase/source terminals are wrong, the read may succeed with invalid data or fail during task setup.

