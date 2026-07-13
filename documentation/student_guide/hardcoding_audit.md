# Hardcoding Audit

This audit identifies where Qudi currently depends on fixed values, lab-specific assumptions, copied files, or legacy patterns. The aim is to move those assumptions into configuration, capability metadata, or plugins.

## Severity legend

| Severity | Meaning |
| --- | --- |
| High | Blocks reuse across labs or devices, or risks wrong hardware behavior. |
| Medium | Makes maintenance or GUI improvement harder. |
| Low | Cleanup or documentation issue. |

## High-priority hardcoding hotspots

| Area | Examples found | Why it matters | Recommended fix |
| --- | --- | --- | --- |
| Device addresses and channels | `ASRL1::INSTR`, `COM3`, `C:/Data`, `/Dev1/...`, `192.168...`, `169.254...`, `TCPIP0::localhost...` | These values are lab-specific and fail on other machines. | Require explicit `ConfigOption`s for real hardware; keep dummy defaults only in dummy modules; add config templates per lab/device. |
| Experiment defaults | ODMR-like defaults such as `2.87e9`, `2.85e9`, `2.89e9`, powers, timings, scan sizes | These are NV-center-specific and may be unsafe or irrelevant for other samples. | Move to experiment profiles loaded from config or plugin manifests. |
| Interface names | `dummy_interface` in prototype modules | Bypasses real interface contracts. | Replace with proper interface classes or mark modules as experimental and exclude from production configs. |
| GUI icons and asset paths | Direct paths like `artwork/icons/...` in GUI code | Breaks themes and makes UI assets hard to swap. | Use a centralized icon registry/helper. |
| Save paths | `logic/save_logic.py` default `C:/Data/` and configs repeating it | Windows-specific default leaks into examples. | Use platform-aware default under user data, with explicit lab override in local config. |

## Files that need attention

### Core and config

| File | Issue | Priority |
| --- | --- | --- |
| `core/manager.py` | Default config search is fixed to `config/load.cfg`, `config/example/custom.cfg`, then `config/example/default.cfg`. Extension paths are raw `sys.path` inserts. | Medium |
| `core/gui.py` | Icon and stylesheet handling is path-based. | Medium |
| `config/example/*.cfg` | Examples include NI `Dev1` channels, Windows paths, localhost server defaults, and commented custom paths. | Medium |

### Logic

| File | Issue | Priority |
| --- | --- | --- |
| `logic/cwodmr_logic.py` | Uses `dummy_interface` and NV-specific default frequency range/status values. | High |
| `logic/confocal_oscope/confocal_logic.py` | Uses `dummy_interface` and fixed scan/frequency defaults. | High |
| `logic/pulsed_oscope/pulse_logic.py` | Uses `dummy_interface` and fixed pulse/threshold defaults. | High |
| `logic/save_logic.py` | Uses Windows data directory default `C:/Data/`. | High |
| `logic/pulsed/sequence_generator_logic.py` | Contains default generation parameters such as `laser_channel`, `sync_channel`, `gate_channel`, and `microwave_frequency`. | Medium |
| `logic/pulsed/sequence_generator_logic_old.py` | Old implementation still present with similar defaults. | Medium |

### Hardware

| File | Issue | Priority |
| --- | --- | --- |
| `hardware/wheels/thorlabs_motorized_filter_wheel.py` | Default `COM3`. | High |
| `hardware/switches/osw12.py`, `hardware/switches/hbridge.py`, `hardware/switches/flipmirror.py` | Default serial interface `ASRL1::INSTR`. | High |
| `hardware/switches/digital_switch_ni.py` | Default NI line `/Dev1/port0/line31`. | High |
| `hardware/laser/*` | Several serial defaults and max power defaults are embedded. | High |
| `hardware/motor/*` | Several serial or DLL path assumptions. | High |
| `hardware/awg/tektronix_awg*.py` | Windows FTP root `C:\\inetpub\\ftproot`, anonymous FTP credentials, temp directories. | High |
| `hardware/awg/keysight_m819x.py` | Default VISA address, user-home storage directories, mode defaults. | Medium |
| `hardware/swabian_instruments/pulse_streamer.py` | Fixed default IP and channel names. | High |
| `hardware/keysight_Oscope.py` | Fixed instrument VISA address. | High |
| `hardware/fpga_fastcounter/*.py` | Default channel numbers and thresholds. | Medium |
| `hardware/camera/*` | Default camera IDs, exposure, gain, temperature, trigger modes. | Medium |

### GUI

| File | Issue | Priority |
| --- | --- | --- |
| `gui/confocal/confocalgui.py` | Direct icon paths, large monolithic GUI logic, fixed widget assumptions. | Medium |
| `gui/switch/switch_gui.py` | Direct icon paths and text/state rendering choices in code. | Medium |
| `gui/odmr/odmrgui.py` | Dynamic range widgets exist but are tied to specific UI names and QSettings key. | Medium |
| `gui/pulsed/pulsed_maingui.py` | Very large GUI module with dynamic controls, device-specific behavior, and many direct widget operations. | Medium |
| `gui/fitsettings.py` | Direct icon path and hand-built UI strings. | Low |
| copied `.ui` files | `pulse_gui - Copy.ui`, `pulse_gui - Copy (2).ui`, `cwodmr_gui - Copy.ui`. | Low |

## What should become configurable

Move these from code to config or profiles:

- serial ports,
- VISA/GPIB/TCPIP addresses,
- IP addresses,
- NI channel names,
- file directories,
- default microwave frequencies and powers,
- pulse channel names,
- scan ranges and resolutions,
- timeouts,
- safe limits,
- units,
- GUI color/icon/theme choices,
- plugin/module search paths.

## What should become capability metadata

Move these from GUI assumptions to interface-provided metadata:

- channel lists,
- min/max/step/default for numeric controls,
- supported trigger modes,
- supported acquisition modes,
- supported file formats,
- whether a device supports live acquisition,
- whether a pulser supports analog, digital, markers, sequence mode, streaming mode,
- safe shutdown actions.

## Suggested audit script

Add a future tool, for example `tools/audit_hardcoding.py`, that scans for:

- IP literals,
- Windows drive paths,
- NI `Dev` channel strings,
- serial defaults,
- direct `artwork/icons` paths,
- `dummy_interface`,
- files with `Copy`, `Changed`, `old`, or source files under `__pycache__`,
- deprecated `get_connector()` and `getConfiguration()` usage.

The script should output Markdown so this audit can be regenerated.
