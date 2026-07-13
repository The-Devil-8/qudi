# Architecture and Runtime Model

## Startup path

The program starts from `start.py`, enters `core/__main__.py`, creates a `core.manager.Manager`, loads a config file, then starts configured modules.

Default config lookup is handled by `Manager._getConfigFile()`:

1. `config/load.cfg`, if present.
2. `config/example/custom.cfg`, if present.
3. `config/example/default.cfg`.

The active config defines module instances under `hardware`, `logic`, and `gui`. The key `module.Class` tells Qudi which Python class to import.

## Module lifecycle

All loadable modules inherit from `core.module.Base` or a subclass such as `logic.generic_logic.GenericLogic` or `gui.guibase.GUIBase`.

Every module has a state machine:

```text
deactivated -> idle -> running
             -> locked
```

Important lifecycle methods:

- `on_activate()`: allocate devices, create timers, connect signals, initialize runtime state.
- `on_deactivate()`: stop timers, disconnect signals, close devices, release resources.

Status variables are restored before `on_activate()` and saved after `on_deactivate()`.

## Configuration

Qudi config files are YAML-like `.cfg` files. A minimal pattern is:

```yaml
hardware:
    counter:
        module.Class: 'slow_counter_dummy.SlowCounterDummy'
        clock_frequency: 100

logic:
    counterlogic:
        module.Class: 'counter_logic.CounterLogic'
        connect:
            counter1: 'counter'

gui:
    countergui:
        module.Class: 'counter.countergui.CounterGui'
        connect:
            counterlogic1: 'counterlogic'
```

## Connectors

A `Connector` declares that one module needs another module with a specific interface.

```python
counter1 = Connector(interface='SlowCounterInterface')
```

At runtime, the config maps `counter1` to an instance name. The Manager verifies that the target module implements the required interface.

This is the main mechanism that makes Qudi modular.

## ConfigOption

`ConfigOption` moves setup values into config:

```python
_clock_frequency = ConfigOption('clock_frequency', 100, missing='warn')
```

Good uses:

- device addresses,
- channel names,
- default frequencies or scan ranges,
- timeouts,
- file directories,
- feature toggles.

Bad pattern:

```python
self.channel = '/Dev1/Ctr0'
self.ip = '192.168.1.100'
```

Those values should usually be `ConfigOption`s.

## StatusVar

`StatusVar` is for user/runtime state that should survive restarts:

```python
_count_frequency = StatusVar('count_frequency', 50)
```

Use it for GUI preferences, selected channels, last-used scan ranges, fit choices, or plot layouts. Avoid using it for fixed hardware identity such as a serial port.

## Interfaces

Interfaces in `interface/` define what a class must provide. Example categories:

- `SlowCounterInterface`: count photons or events.
- `FastCounterInterface`: time-resolved counting.
- `PulserInterface`: pulse generation.
- `MicrowaveInterface`: microwave source control.
- `ConfocalScannerInterface`: scanner positioning and counting.
- `CameraInterface`: camera acquisition.
- `MotorInterface`: motion control.
- `ProcessInterface` and `ProcessControlInterface`: read/control process values.

For a new instrument, implement the relevant interface rather than changing logic code.

## Hardware, logic, GUI separation

The intended dependency direction is:

```text
GUI -> Logic -> Hardware
```

Hardware should not know about GUI. GUI should not talk directly to hardware unless there is a strong reason. Logic modules should hold experiment workflow and data rules.

## Interfuses

Interfuse modules live in `logic/interfuse/`. They are adapters. They can:

- combine multiple hardware modules into one interface,
- expose a modified interface,
- add calibration or coordinate transforms,
- bridge a logic module to a hardware module with a different shape.

Examples:

- `scanner_tilt_interfuse.py`: scanner coordinate correction.
- `odmr_counter_microwave_interfuse.py`: combines counter and microwave behavior for ODMR.
- `aom_laser_interfuse.py`: exposes laser-like behavior through scanner control.

Interfuses are a good pluggability point because they keep old modules working while new abstractions are introduced.

## Threading and Qt signals

Qudi uses Qt objects, signals, and slots. Some modules can run in their own Qt thread by setting `_threaded = True`. The Manager moves those modules into a module thread during activation.

GUI updates should happen via signals, not direct cross-thread UI calls.

## Extension paths

The config supports `global.extensions`, which adds directories to `sys.path`. This allows external modules without editing the main tree. It is useful, but currently it is path-based rather than a modern plugin registry.
