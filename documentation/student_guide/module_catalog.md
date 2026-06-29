# Existing Module Catalog

This catalog groups the existing modules by responsibility. It is not a full API reference; it is a map for navigating the code.

## Core modules

| File | Responsibility |
| --- | --- |
| `core/manager.py` | Loads config, imports modules, connects dependencies, starts/stops modules, saves status. |
| `core/module.py` | Base class and module state machine. |
| `core/connector.py` | Runtime dependency connector with interface checking. |
| `core/configoption.py` | Declares config-backed module attributes. |
| `core/statusvariable.py` | Declares restart-persistent module state. |
| `core/interface.py` | Interface method helpers and scalar constraints. |
| `core/meta.py` | Metaclasses that collect connectors, config options, and status variables. |
| `core/config.py` | YAML config load/save helpers. |
| `core/gui.py` | Application icon/theme setup. |
| `core/remote.py` | Remote module support through RPyC if available. |
| `core/threadmanager.py` | Qt thread management. |

## Interface modules

The `interface/` directory defines contracts for device-like behavior:

| Interface | Typical implementation |
| --- | --- |
| `camera_interface.py` | Camera drivers in `hardware/camera/`. |
| `confocal_scanner_interface.py` | Scanner or NI card modules. |
| `data_instream_interface.py` | Streaming analog/digital input. |
| `fast_counter_interface.py` | Time-resolved photon/event counters. |
| `magnet_interface.py` | Magnet positioning or field control. |
| `microwave_interface.py` | Microwave signal generators. |
| `motor_interface.py` | Motorized stages. |
| `odmr_counter_interface.py` | Counters specialized for ODMR workflows. |
| `process_interface.py` | Read process values such as temperature or power. |
| `process_control_interface.py` | Set process values. |
| `pulser_interface.py` | Pulse sequence hardware. |
| `simple_laser_interface.py` | Laser on/off, power, and status. |
| `slow_counter_interface.py` | Count-rate counters. |
| `spectrometer_interface.py` | Spectrometer acquisition. |
| `switch_interface.py` | Multi-state switches or flip mirrors. |

## Hardware modules

Hardware modules either talk to real instruments or emulate them.

### Dummies and simulation

Dummy modules are essential for learning and testing without lab equipment:

- `hardware/slow_counter_dummy.py`
- `hardware/fast_counter_dummy.py`
- `hardware/confocal_scanner_dummy.py`
- `hardware/camera/camera_dummy.py`
- `hardware/microwave/mw_source_dummy.py`
- `hardware/pulser_dummy.py`
- `hardware/process_dummy.py`
- `hardware/simple_data_dummy.py`
- `hardware/spectrometer/spectrometer_dummy.py`
- `hardware/switches/switch_dummy.py`
- `hardware/motor/motor_dummy.py`
- `logic/measurement_simulator/polarisation_dependence_sim.py`

### Common real-device families

| Device family | Location |
| --- | --- |
| National Instruments cards | `hardware/national_instruments_x_series.py`, `hardware/ni_x_series_in_streamer.py`, `hardware/gated_ni_card.py`, `hardware/NIfastcounter.py` |
| Microwave sources | `hardware/microwave/` |
| AWGs and pulsers | `hardware/awg/`, `hardware/fpga_pulser/`, `hardware/spincore/`, `hardware/swabian_instruments/` |
| Fast counters | `hardware/fastcomtec/`, `hardware/fpga_fastcounter/`, `hardware/picoquant/`, `hardware/swabian_instruments/` |
| Cameras | `hardware/camera/` |
| Lasers | `hardware/laser/` |
| Motors | `hardware/motor/` |
| Spectrometers | `hardware/spectrometer/` |
| Switches | `hardware/switches/` |
| Process and temperature | `hardware/temperature/`, `hardware/CTC100_temperature.py`, `hardware/process_dummy.py` |
| Magnet and power supply | `hardware/magnet/`, `hardware/sc_magnet/`, `hardware/power_supply/` |

## Logic modules

Logic modules contain measurement behavior and data processing.

| Module | Role |
| --- | --- |
| `logic/counter_logic.py` | Count-rate acquisition and saving. |
| `logic/confocal_logic.py` | Confocal scan and optimization behavior. |
| `logic/odmr_logic.py` | ODMR sweeps and fitting. |
| `logic/cwodmr_logic.py` | Prototype continuous-wave ODMR logic using `dummy_interface`. |
| `logic/pulsed/` | Pulse sequence generation, pulsed measurement, extraction, analysis. |
| `logic/fit_logic.py` and `logic/fitmethods/` | Fit containers and mathematical fit models. |
| `logic/save_logic.py` | Data output directories, text files, figures. |
| `logic/optimizer_logic.py` | Position optimization. |
| `logic/poi_manager_logic.py` | Points of interest management. |
| `logic/magnet_logic.py` | Magnet workflow orchestration. |
| `logic/laser_logic.py` | Laser workflow around laser hardware. |
| `logic/camera_logic.py` | Camera acquisition and saving. |
| `logic/spectrum.py` | Spectrometer workflow. |
| `logic/time_series_reader_logic.py` | Streaming data display and recording. |
| `logic/taskrunner.py` and `logic/tasks/` | Task execution framework. |
| `logic/automation.py` | Automation logic. |
| `logic/jupyterkernel/` | Jupyter integration. |

## GUI modules

GUI modules are PyQt windows built from `.ui` files and Python glue code.

| GUI | Paired logic |
| --- | --- |
| `gui/manager/managergui.py` | Manager module control, logs, remote/thread widgets. |
| `gui/counter/countergui.py` | `CounterLogic`. |
| `gui/confocal/confocalgui.py` | `ConfocalLogic`. |
| `gui/odmr/odmrgui.py` | `ODMRLogic`. |
| `gui/pulsed/pulsed_maingui.py` | Pulsed logic modules. |
| `gui/camera/cameragui.py` | `CameraLogic`. |
| `gui/laser/laser.py` | `LaserLogic`. |
| `gui/magnet/magnet_gui.py` | `MagnetLogic`. |
| `gui/spectrometer/spectrometergui.py` | `SpectrumLogic`. |
| `gui/qdplotter/qdplotter_gui.py` | `QDPlotLogic`. |
| `gui/time_series/time_series_gui.py` | `TimeSeriesReaderLogic`. |
| `gui/switch/switch_gui.py` | `SwitchLogic`. |
| `gui/taskrunner/taskgui.py` | `TaskRunner`. |

## Reusable GUI widgets

`qtwidgets/` contains custom reusable components such as scientific spin boxes, scan plot widgets, checkboxes, toggle switches, and loading indicators. These should be preferred over duplicate per-GUI widget code.

## Existing extension mechanisms

Qudi already has several pluggable areas:

- config-selected module classes,
- `global.extensions` paths,
- interface-based connectors,
- `logic/interfuse/` adapters,
- additional fit methods through `additional_fit_methods_path`,
- additional pulsed predefined methods through `additional_predefined_methods_path`,
- additional pulsed sampling functions through `additional_sampling_functions_path`,
- additional extraction and analysis methods through pulsed config options.

The modernization plan should formalize these into a consistent plugin system.
