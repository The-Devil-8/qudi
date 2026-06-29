# Common Experiment Workflows

## Workflow shape

Most Qudi workflows follow this pattern:

```text
GUI action
  -> logic slot/method
  -> hardware interface call
  -> measurement data
  -> logic processing/fitting
  -> GUI signal update
  -> save logic writes files
```

The GUI should be a control surface. The logic should be the source of behavior. Hardware modules should only know how to operate devices.

## Counter workflow

Purpose: measure event or photon count rate over time.

Typical modules:

- `hardware/slow_counter_dummy.py` or a real counter hardware module.
- `logic/counter_logic.py`.
- `gui/counter/countergui.py`.
- `logic/save_logic.py`.

Data model:

- count frequency,
- number of samples,
- count length/history,
- count data arrays,
- saving state.

Main improvement area:

- `logic/counter_logic.py` still has a FIXME about counting mode details that should come from hardware.

## Confocal scan workflow

Purpose: scan position and build a brightness image.

Typical modules:

- `interface/confocal_scanner_interface.py`.
- scanner hardware or `hardware/confocal_scanner_dummy.py`.
- `logic/confocal_logic.py`.
- `gui/confocal/confocalgui.py`.
- optional optimizer and POI manager.

Data model:

- scan axes,
- scan ranges,
- scan matrix,
- position history,
- image display state.

Main improvement area:

- The GUI is large and tightly tied to specific window elements and icons.
- Some scan-direction assumptions are marked TODO in logic.
- Hardware channel examples are strongly NI `Dev1` specific.

## ODMR workflow

Purpose: sweep microwave frequency and detect resonance through fluorescence changes.

Typical modules:

- microwave hardware from `hardware/microwave/`.
- counter hardware or ODMR counter interface.
- `logic/odmr_logic.py`.
- `gui/odmr/odmrgui.py`.
- `logic/fit_logic.py`.
- `logic/save_logic.py`.

Data model:

- frequency range or multiple ranges,
- microwave power,
- runtime and average count data,
- fit results.

Main improvement area:

- Hardware and logic should expose capabilities and constraints so the GUI can build controls dynamically instead of assuming specific ranges or layouts.

## Pulsed measurement workflow

Purpose: generate timed pulse sequences, upload them to a pulser/AWG, measure the response, extract signal windows, analyze and fit.

Important modules:

- `logic/pulsed/sequence_generator_logic.py`
- `logic/pulsed/pulsed_master_logic.py`
- `logic/pulsed/pulsed_measurement_logic.py`
- `logic/pulsed/pulse_extractor.py`
- `logic/pulsed/pulse_analyzer.py`
- `gui/pulsed/pulsed_maingui.py`
- pulser hardware from `hardware/awg/`, `hardware/fpga_pulser/`, `hardware/spincore/`, or dummy modules.

Existing pluggability:

- predefined pulse methods,
- sampling functions,
- extraction methods,
- analysis methods.

Main improvement area:

- There is both `sequence_generator_logic.py` and `sequence_generator_logic_old.py`.
- There is a `pulsed_measurement_logic_Changed.py`, which looks like a local experiment fork.
- Upload and device capability handling should be normalized.

## Camera workflow

Purpose: acquire images and save them.

Typical modules:

- camera hardware in `hardware/camera/`.
- `logic/camera_logic.py`.
- `gui/camera/cameragui.py`.

Main improvement area:

- Camera capabilities should be expressed as metadata: supported exposure ranges, gain ranges, live mode, trigger mode, binning, and frame shape.

## Process control workflow

Purpose: read and control slow physical variables such as temperature, power, pressure, or PID-controlled values.

Typical modules:

- `ProcessInterface`,
- `ProcessControlInterface`,
- `logic/software_pid_controller.py`,
- `logic/pid_logic.py`,
- `gui/pidgui/pidgui.py`,
- temperature or process hardware.

Main improvement area:

- Constraints, units, and safe ranges should be explicit and reused by GUI controls.

## Automation workflow

Purpose: run multi-step operations automatically.

Important modules:

- `logic/automation.py`
- `gui/automation/automationgui.py`
- `logic/taskrunner.py`
- `logic/tasks/`

Main improvement area:

- Task descriptions should become declarative and discoverable so users can assemble workflows without editing Python.
