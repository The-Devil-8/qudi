# Q-Diamond vs Qudi Comparison

This folder compares the lightweight [Q-Diamond](https://github.com/The-Devil-8/Q-Diamond) repository with this full Qudi tree.

Q-Diamond is a lab-focused NV centre application. It targets exactly three workflows:

- Confocal microscopy: XY and Z scanning to locate individual defects.
- Microwave control: frequency sweeps for ODMR.
- Pulse generation: Rabi, T1, T2/Hahn echo, and XY8 timing.

This Qudi tree is broader: it contains many hardware drivers, interfaces, GUI modules, pulsed-measurement tools, fitting tools, remote modules, task automation, and legacy compatibility layers. The right target is not to copy all of Qudi into Q-Diamond. The target is to bring Q-Diamond up to date while preserving its lightweight, lab-specific shape.

## Documents

1. [Architecture comparison](architecture_comparison.md)
2. [Three-pillar gap analysis](three_pillar_gap_analysis.md)
3. [Q-Diamond upgrade file plan](q_diamond_upgrade_file_plan.md)

## Source basis

Q-Diamond was inspected from GitHub on 2026-06-15. The repository root currently contains `core`, `drivers`, `interface`, `logic`, `gui_env`, `config`, `docs`, `main.py`, and `requirements.txt`.

Important observed Q-Diamond traits:

- Python 3.10+ target, recommended Python 3.11 or 3.12.
- PySide6 and pyqtgraph GUI stack.
- YAML config files: `config/hardware.yaml` and `config/scan_config.yaml`.
- Simulation mode through `use_dummy: true`.
- HDF5 data storage through `logic/data_manager.py`.
- Real hardware support currently centered on NI scanner output/input and an R&S SMIQ microwave source.
- Lightweight manual module wiring in `main.py`.

## Decision summary

Use Q-Diamond as the operational lab app, but borrow these Qudi ideas:

- explicit module boundaries,
- config-driven hardware selection,
- interface/capability contracts,
- dummy-first testing,
- pluggable drivers and sequence definitions,
- safer lifecycle and shutdown rules,
- systematic docs and config validation.

Avoid importing Qudi complexity that is not needed for the lab:

- full remote module system,
- broad unrelated hardware catalog,
- old Qt compatibility layers,
- legacy connector APIs,
- large monolithic GUI modules.
