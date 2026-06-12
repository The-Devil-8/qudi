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

