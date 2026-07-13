# Three-Pillar Gap Analysis

This document compares Q-Diamond's current implementation against the three NV-lab pillars: confocal microscopy, microwave/ODMR control, and pulse generation.

## Pillar 1: Confocal microscopy

### What Q-Diamond already has

- `logic/scan_logic.py` runs 2D raster scans in a `QThread` worker.
- `logic/zscan_logic.py` supports Z scans.
- `logic/optimizer_logic.py` provides optimization around a point.
- `drivers/dummy_scanner.py` simulates Gaussian fluorescent spots with Poisson noise.
- `drivers/ni_scanner.py` supports NI-DAQ analog output for XYZ and analog input for APD-like signal.
- `interface/scanner_interface.py` defines the basic scanner contract.
- `config/scan_config.yaml` stores default scan ranges, resolution, and clock rate.
- `config/hardware.yaml` stores NI device/channel configuration and voltage limits.

### Gaps

| Gap | Impact | Upgrade |
| --- | --- | --- |
| Scanner units are primarily volts. | Users think in micrometres; voltage-to-distance calibration may be inconsistently applied. | Add a `Position`/`ScanArea` model with both volts and physical units. |
| Scan config is not schema-validated. | Bad ranges or unsafe voltage values can fail late. | Add `core/config_schema.py` with scan and hardware validation. |
| NI scanner reads analog voltage, not a true photon-counting abstraction. | APD/counter hardware may differ by lab. | Split scanner motion from counter acquisition; add `CounterInterface` drivers for NI counter, TimeTagger, or APD gate modes. |
| No bidirectional scan or serpentine scan mode is documented in logic. | Slow scans may waste time returning to the start of each line. | Add scan modes: unidirectional, bidirectional, serpentine, point list. |
| No explicit drift correction workflow. | Long scans can lose the NV center. | Add periodic optimize/refocus hooks. |
| No scan metadata versioning. | Saved files can become hard to compare after upgrades. | Add data schema version and calibration snapshot to every HDF5 file. |

### Minimal up-to-date target

Confocal should support:

- XY raster scan,
- Z scan,
- live line updates,
- abort and safe return,
- optimizer/refocus,
- hardware limits visible in GUI,
- HDF5 output with calibration and schema version,
- dummy and NI backends tested by the same logic.

## Pillar 2: MW control and ODMR

### What Q-Diamond already has

- `logic/odmr_logic.py` runs frequency sweeps in a `QThread` worker.
- `drivers/mw_smiq.py` controls an R&S SMIQ source through pyvisa/SCPI.
- `drivers/dummy_scanner.py` includes `DummyMWSource`.
- `interface/mw_interface.py` defines CW, sweep, output on/off, and status methods.
- `logic/fit_logic.py` includes Lorentzian and double-Lorentzian fitting.
- `logic/data_manager.py` saves ODMR data to HDF5.

### Gaps

| Gap | Impact | Upgrade |
| --- | --- | --- |
| ODMR worker steps frequency in software with `time.sleep`. | Timing is simple but may be slow or inaccurate for high-throughput sweeps. | Support hardware sweep mode where the MW source and counter are synchronized. |
| Counter abstraction is thin and dummy-biased. | Real photon counting for ODMR may need gated, continuous, or buffered modes. | Add `CounterInterface` implementations and capability metadata. |
| MW source limits are not formalized. | GUI can request unsafe or unsupported frequency/power values. | Add `MWCapabilities`: frequency range, power range, step size, settling time, sweep support. |
| No microwave source registry. | Adding Anritsu, SMBV, Windfreak, etc. requires code edits in `main.py`. | Add driver registry and config-selected driver type. |
| Background/reference normalization is mentioned but not fully generalized. | Real ODMR may need reference channels, lock-in, or pulsed ODMR variants. | Add acquisition modes: raw, normalized, reference channel, differential. |
| Fit model choice is manual/simple. | Double dips and poor initial guesses can fail. | Add fit presets, bounds, automatic model suggestion, and fit quality flags. |

### Minimal up-to-date target

ODMR should support:

- CW frequency sweep,
- configurable averaging,
- real counter backends,
- MW capability limits,
- safe RF-off shutdown,
- Lorentzian and double-Lorentzian fits,
- HDF5 output with full sweep metadata,
- optional hardware-triggered sweep path.

## Pillar 3: Pulse generation

### What Q-Diamond already has

- `logic/pulsed_logic.py` orchestrates Rabi, T1, Hahn echo, and XY8 measurements.
- `logic/sequence_generator.py` generates boolean pulse arrays.
- `interface/pulse_interface.py` defines load, arm, trigger, stop, and sample-rate methods.
- `drivers/dummy_scanner.py` includes `DummyPulseGen` and `DummyCounter`.
- `logic/fit_logic.py` includes Rabi and exponential/stretched exponential models.
- `logic/data_manager.py` saves pulsed data to HDF5.

### Gaps

| Gap | Impact | Upgrade |
| --- | --- | --- |
| No real pulse generator driver. | Pulse pillar cannot run real Rabi/T1/T2/XY8 yet. | Add at least one real backend: NI digital output, Pulse Streamer, PulseBlaster, or AWG. |
| Sequence channels are fixed constants. | Different labs wire laser, MW, gate, and reference channels differently. | Move channel map to config and validate it. |
| XY8 phase is modeled as one MW channel. | True XY8 needs X/Y phase control or IQ/MW switch handling. | Add pulse channels for MW_X, MW_Y, I/Q, or phase-tagged instructions. |
| Timing constraints are not checked. | Hardware may reject too-short pulses or unsupported sample rates. | Add `PulseGenCapabilities`: sample rates, min pulse width, channel count, max samples, trigger modes. |
| Counter gating is dummy/simple. | Real gated detection needs synchronization with pulse output. | Add hardware-timed counter driver and trigger wiring model. |
| Sequence definitions are hardcoded methods. | Adding Ramsey, CPMG, XY4, XY16, DEER, or custom lab sequences requires editing core logic. | Add `logic/sequences/` plugin-style sequence registry. |

### Minimal up-to-date target

Pulse generation should support:

- configurable channel map,
- real pulse generator backend,
- real gated counter backend,
- Rabi, T1, Hahn echo/T2, XY8,
- hardware timing validation before upload,
- phase-aware sequence representation,
- HDF5 output with pulse program and channel map snapshot,
- simulation backend that uses the same interface as real hardware.

## Cross-pillar improvements

These upgrades benefit all three pillars:

- Replace global `use_dummy` with per-device backend selection.
- Add config profiles: `simulation`, `lab_ni_smiq`, `teaching`, `hardware_debug`.
- Add `core/capability.py` for ranges, units, channels, timing, and safety limits.
- Add `core/config_schema.py` and fail before GUI startup when config is invalid.
- Add `tests/integration/test_simulated_workflows.py` covering scan, ODMR, and pulsed dummy workflows.
- Add `tools/doctor.py` to check drivers, NI-DAQmx, VISA resources, config, write paths, and sample hardware availability.
