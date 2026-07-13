# Q-Diamond Upgrade File Plan

This is a concrete file-level plan for bringing Q-Diamond up to date while keeping it lightweight.

## Immediate cleanup

| File or area | Change |
| --- | --- |
| `.gitignore` | Add Python, Qt, cache, virtual environment, data, log, and IDE ignores. |
| `**/__pycache__/` | Remove tracked `.pyc` files from the repository. |
| Source comments | Fix encoding artifacts in comments/docstrings. |
| `requirements.txt` | Split runtime and dev dependencies into `requirements.txt` and `requirements-dev.txt`. |
| Root docs | Keep README short; move long details into `docs/`. |

Suggested `.gitignore` entries:

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
.venv/
venv/
*.log
data/
*.h5
.idea/
.vscode/
```

## Files to add

### Core

| New file | Purpose |
| --- | --- |
| `core/capability.py` | Dataclasses for numeric ranges, channel maps, device limits, units, safe defaults. |
| `core/config_schema.py` | Validate YAML config before any hardware opens. |
| `core/module_registry.py` | Map config driver names to classes; remove hardcoded driver imports from `main.py`. |
| `core/exceptions.py` | Shared exception types: config error, hardware unavailable, unsafe value, acquisition error. |
| `core/app_context.py` | Holds loaded config, module manager, registries, and shared app paths. |

### Drivers

Restructure from flat `drivers/` into capability-based groups:

```text
drivers/
  scanner/
    dummy.py
    ni_daq.py
  microwave/
    dummy.py
    smiq.py
    generic_scpi.py
  pulsegen/
    dummy.py
    ni_digital.py
    pulse_streamer.py
  counter/
    dummy.py
    ni_counter.py
```

Minimum new real drivers:

| New file | Purpose |
| --- | --- |
| `drivers/counter/ni_counter.py` | Real NI counter/gated photon counting backend. |
| `drivers/pulsegen/ni_digital.py` | Basic NI digital pulse output backend, if hardware timing allows. |
| `drivers/microwave/generic_scpi.py` | Shared SCPI base for SMIQ, Anritsu, SMBV-like sources. |

### Logic

| New file | Purpose |
| --- | --- |
| `logic/models.py` | Typed dataclasses for scan, ODMR, pulsed params/results. |
| `logic/sequences/base.py` | Sequence interface and registry. |
| `logic/sequences/nv_basic.py` | Rabi, T1, Hahn echo, XY8 sequence definitions. |
| `logic/sequences/channel_map.py` | Configurable laser/MW/gate/reference channel mapping. |
| `logic/workers.py` | Shared worker/thread cleanup helpers for scans, ODMR, pulsed jobs. |
| `logic/safety.py` | Validates requested values against capabilities before hardware calls. |

### GUI

| New file | Purpose |
| --- | --- |
| `gui_env/widgets/range_controls.py` | Reusable controls for start/stop/points, units, and validation. |
| `gui_env/widgets/device_status.py` | Common hardware state and error display. |
| `gui_env/widgets/save_panel.py` | Shared save path, metadata, and data-type panel. |
| `gui_env/view_models.py` | GUI-facing state models independent from Qt widget layout. |

### Tools and tests

| New file | Purpose |
| --- | --- |
| `tools/doctor.py` | Checks Python version, packages, config, VISA, NI-DAQmx, writable data path. |
| `tools/list_devices.py` | Lists VISA resources and NI devices/channels when drivers are available. |
| `tests/unit/test_sequence_generator.py` | Validates pulse timing arrays and channel maps. |
| `tests/unit/test_config_schema.py` | Ensures bad configs fail clearly. |
| `tests/unit/test_fit_logic.py` | Tests Lorentzian, Rabi, exponential fits on synthetic data. |
| `tests/integration/test_dummy_workflows.py` | Runs dummy scan, dummy ODMR, and dummy pulsed measurement. |
| `tests/gui/test_main_window_smoke.py` | Opens main GUI in offscreen mode and checks tabs/widgets load. |

## Files to change

### `main.py`

Current issue: module selection and wiring are hardcoded.

Target:

```python
config = load_and_validate_config(...)
registry = build_default_registry()
modules = registry.instantiate_from_config(config)
wire_dependencies(modules, config)
window = MainWindow(modules, ...)
```

Required changes:

- Remove global `use_dummy` branching.
- Use per-device backend keys:

```yaml
devices:
  scanner:
    driver: dummy_scanner
  microwave:
    driver: smiq
  pulsegen:
    driver: dummy_pulsegen
  counter:
    driver: dummy_counter
```

- Fail before opening GUI if required devices are missing.

### `config/hardware.yaml`

Current issue: global `use_dummy` and real/dummy config mixed together.

Target:

```yaml
profile: simulation

devices:
  scanner:
    driver: dummy_scanner
    config:
      background: 20.0
      sample_delay_ms: 0.0

  microwave:
    driver: dummy_mw
    config:
      frequency_limits_hz: [2.0e9, 4.0e9]
      power_limits_dbm: [-60, 10]

  pulsegen:
    driver: dummy_pulsegen
    config:
      sample_rate_hz: 100e6
      channels:
        laser: 0
        mw_x: 1
        mw_y: 2
        gate: 3
        reference: 4

  counter:
    driver: dummy_counter
    config:
      mode: gated
```

### `logic/sequence_generator.py`

Current issue: channels are fixed constants and XY8 lacks phase distinction.

Target changes:

- Accept a `ChannelMap`.
- Support logical phase channels: `mw_x`, `mw_y`, optional `mw_i`, `mw_q`.
- Return a `PulseProgram` object with sequence data, sample rate, metadata, and validation result.
- Keep convenience methods for Rabi, T1, Hahn echo, XY8.

### `logic/pulsed_logic.py`

Current issue: assumes pulse generator and counter behavior without checking capabilities.

Target changes:

- Validate sequence length, sample rate, min pulse width, channel count, trigger mode.
- Store the generated pulse program in the saved HDF5 file.
- Add acquisition modes: signal/reference, normalized, raw gates.
- Stop pulse generator and counter in `finally` blocks.

### `logic/odmr_logic.py`

Current issue: software-stepped sweep only and no MW/counter capability validation.

Target changes:

- Validate frequency and power range.
- Add optional hardware-sweep path.
- Ensure RF output is turned off in `finally`.
- Save full sweep settings and fit result metadata.

### `logic/scan_logic.py`

Current issue: good base, but scan modes and metadata should be richer.

Target changes:

- Validate ranges against scanner limits.
- Add serpentine/bidirectional scan option.
- Save full coordinate arrays or enough metadata to reconstruct them.
- Add drift/refocus callback hooks.

### `logic/data_manager.py`

Current issue: HDF5 is good, but schema needs versioning.

Target changes:

- Add `schema_version`.
- Add `measurement_type`.
- Store raw params, capabilities snapshot, channel map, calibration, software version.
- Add atomic save: write temp file then rename.

## Suggested new config files

```text
config/profiles/simulation.yaml
config/profiles/lab_ni_smiq.yaml
config/profiles/teaching.yaml
config/profiles/hardware_debug.yaml
```

Each profile should select drivers and include only values relevant to that profile.

## Roadmap

### Week 1: Clean and protect

- Remove tracked caches.
- Fix `.gitignore`.
- Add config schema.
- Add dummy workflow tests.
- Add `tools/doctor.py`.

### Week 2: Make hardware pluggable

- Add module registry.
- Replace global `use_dummy`.
- Add device capability metadata.
- Move dummy classes into separate driver files.

### Week 3: Strengthen three pillars

- Confocal: scan validation and serpentine mode.
- ODMR: MW/counter capability validation and safe RF shutdown.
- Pulsed: channel map, pulse timing validation, sequence metadata in HDF5.

### Week 4: Real lab readiness

- Add real counter backend.
- Add first real pulse-generator backend.
- Add GUI validation feedback.
- Add HDF5 schema documentation and examples.

## Definition of up to date

Q-Diamond can be considered up to date for the lab when:

- It runs all three workflows in simulation from a clean install.
- Hardware is selected from config, not by editing `main.py`.
- Bad config fails before hardware opens.
- Scanner, MW, pulse generator, and counter expose capabilities.
- Rabi, T1, Hahn echo/T2, and XY8 sequences validate timing and channel maps.
- HDF5 files include schema version, metadata, calibration, and enough information to reproduce analysis.
- Tests cover dummy scan, ODMR, pulsed measurement, fitting, config validation, and GUI startup.
- No generated cache files are tracked in Git.
