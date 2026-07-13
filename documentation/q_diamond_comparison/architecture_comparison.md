# Architecture Comparison: Q-Diamond and Qudi

## Executive summary

Q-Diamond is a small, direct NV-lab application. Qudi is a general laboratory-control framework. Q-Diamond should remain lightweight, but it needs some framework features from Qudi: better dependency injection, capability metadata, config validation, formal driver interfaces, and tests.

## Side-by-side architecture

| Area | Q-Diamond lightweight version | Full Qudi tree | What Q-Diamond should adopt |
| --- | --- | --- | --- |
| Scope | NV centre workflows: confocal, ODMR, pulsed. | Multi-instrument, multi-experiment lab framework. | Keep the narrow NV scope. |
| Runtime entry | `main.py` loads config, picks dummy/real hardware, wires modules manually. | `start.py` and `core.manager.Manager` load configured modules dynamically. | Replace hardcoded wiring with a small registry, not full Qudi manager complexity. |
| Module base | `core/base_module.py` with config, status, error signal, activate/deactivate hooks. | `core/module.py` with state machine, config options, status variables, connectors, threading support. | Add a simple state enum and stronger lifecycle guarantees. |
| Dependency injection | Manual setter calls such as `scan_logic.set_scanner(scanner)`. | Config `connect` sections and `Connector` interface checks. | Add config-defined connections or a typed dependency map. |
| Interfaces | Small ABCs: scanner, MW, pulse, counter. | Many interface modules and connector enforcement. | Keep small ABCs, add capability metadata and conformance tests. |
| Config | Two YAML files. | YAML-style config with module categories, startup, extensions, and status persistence. | Add schemas, profiles, and environment/local overrides. |
| GUI | PySide6 widgets in `gui_env`, direct module dictionary injection. | PyQt/qtpy GUI modules loaded by manager. | Keep PySide6, split large widgets into panels/view-models, add capability-driven controls. |
| Data | HDF5 through `logic/data_manager.py`. | Save logic writes text/figures and other experiment outputs. | Keep HDF5 as primary; add versioned data schema. |
| Simulation | Built-in dummy scanner, MW source, pulse generator, counter. | Many dummy hardware modules. | Preserve dummy-first development and test it. |
| Pulse sequences | `logic/sequence_generator.py` produces boolean arrays for Rabi, T1, Hahn echo, XY8. | Rich pulsed package with assets, sampling functions, predefined methods, extraction and analysis methods. | Make Q-Diamond sequence definitions pluggable without copying the whole Qudi pulsed framework. |
| Tests | No obvious test suite in root. | Old CI files; test workflow unclear in this tree. | Add pytest, pytest-qt, dummy integration tests, and config validation tests. |

## Current Q-Diamond strengths

- The codebase is understandable for a student or new lab developer.
- The application maps directly to the lab workflows.
- Simulation mode is a first-class path, not an afterthought.
- HDF5 is a good modern scientific data format.
- PySide6 is a modern Qt binding.
- The GUI and logic are already separated more clearly than a single-script lab tool.

## Current Q-Diamond risks

- `main.py` is the dependency graph. Adding one real device requires editing code.
- The `use_dummy` switch is global. A mixed setup, such as real scanner plus dummy pulse generator, is not cleanly modeled.
- Driver selection is hardcoded to `DummyScanner` vs `NIScanner`, and SMIQ for microwave.
- Real pulse-generator and real counter drivers are not present; dummy implementations stand in for core lab hardware.
- Capability limits are inconsistent. Scanner limits exist, but MW ranges, pulse channels, counter modes, and sample-rate limits are not formalized.
- Config has no schema validation before startup.
- `__pycache__` files are tracked in the public repository.
- Comments and some source text show encoding artifacts, which should be cleaned.

## Recommended architectural target

Keep this shape:

```text
config profiles
  -> small module registry
  -> drivers
  -> logic workers
  -> PySide6 GUI
  -> HDF5 data manager
```

Add this structure:

```text
core/
  module_registry.py       # maps config driver names to classes
  capability.py            # common range/channel metadata
  config_schema.py         # validates YAML before app startup
  exceptions.py            # clear user-facing error categories

drivers/
  scanner/
  microwave/
  pulsegen/
  counter/

logic/
  sequences/
  workers/
  analysis/

tests/
  unit/
  integration/
  gui/
```

## What not to import from Qudi

Q-Diamond does not need the full Qudi module manager, remote module server, every hardware family, or old GUI/module compatibility machinery. Those would make the lightweight app harder to maintain.

Instead, use Qudi as a pattern library:

- Qudi's `Connector` idea becomes a small typed dependency map.
- Qudi's `ConfigOption` idea becomes schema-validated YAML.
- Qudi's interfaces become smaller Q-Diamond ABCs with capability metadata.
- Qudi's dummy devices become Q-Diamond's mandatory test backend.
- Qudi's pulsed extension mechanism becomes a simple sequence plugin folder.

## Migration rule

Any change that makes Q-Diamond harder to explain in ten minutes should be questioned. The lab needs reliability and pluggability, not framework bulk.
