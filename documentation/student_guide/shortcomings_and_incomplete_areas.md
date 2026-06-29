# Shortcomings and Incomplete Areas

This document lists visible limitations in the current tree. It is written as a planning document, not as criticism of the original project.

## 1. Documentation gaps

Existing docs explain installation, config, some GUI pages, and pulsed extensions, but several core pages are incomplete or too advanced for beginners.

Notable gap:

- `documentation/how_to_make_modules.md` is mostly headings with no implementation guidance.

Recommended action:

- Expand module creation docs with complete hardware, logic, GUI, interface, and interfuse examples.
- Add beginner-level diagrams and config-to-code walkthroughs.
- Keep API reference separate from concept docs.

## 2. Legacy APIs still present

`core/module.py` still supports deprecated module patterns:

- `getStatusVariables()`
- `setStatusVariables()`
- `getConfiguration()`
- `get_connector()`
- legacy `_connectors`

Recommended action:

- Keep compatibility for now.
- Add deprecation warnings to migration docs.
- Convert active modules to class-level `Connector`, `ConfigOption`, and `StatusVar`.
- Add a linter/check script that flags new use of legacy patterns.

## 3. Prototype and duplicate files

The repository contains files that look like local experiments or accidental copies:

- `logic/pulsed/pulsed_measurement_logic_Changed.py`
- `logic/pulsed/sequence_generator_logic_old.py`
- `hardware/microwave/mw_source_srssg_copy.py`
- `hardware/swabian_instruments/__pycache__/pulse_streamer.py`
- `logic/pulsed/pulse_extraction_methods/new 1.txt`
- copied GUI files such as `gui/pulse_gui/pulse_gui - Copy.ui`

Recommended action:

- Classify each as active, archival, or removable.
- Move archival files to a clearly named `legacy/` area or remove them after tests.
- Never keep source `.py` files under `__pycache__`.

## 4. Hardware capability metadata is inconsistent

Some modules expose constraints, but not consistently. GUIs often assume ranges or controls instead of asking hardware/logic for capability metadata.

Recommended action:

- Introduce a common `get_capabilities()` or `constraints` pattern per interface.
- Include units, min/max, step size, enum options, channel list, safe defaults, and whether a feature is supported.
- Use capability metadata to generate GUI controls and validate config.

## 5. GUI is functional but tightly coupled

Many GUI modules load fixed `.ui` files and then manually bind specific widget names. Several GUIs are large and hard to modify safely.

Recommended action:

- Keep PyQt, but add shared view models or controller classes.
- Standardize common controls for ranges, channels, scan settings, fit settings, file saving, and device status.
- Build dynamic panels from logic/hardware capability metadata.
- Add layout persistence and validation in a consistent way.

## 6. Config is powerful but not schema-validated

Config files are flexible, but errors are mostly found at runtime. A typo in a connector name, module class, or device channel can fail late.

Recommended action:

- Add a config schema validator.
- Validate module categories, `module.Class`, connectors, options, types, and missing required values before startup.
- Provide a `qudi doctor` command to explain config problems.

## 7. Plugin system is only partially formalized

Qudi has extension paths and several add-on method paths, but there is no central plugin manifest or discovery UI.

Recommended action:

- Add plugin manifests with name, version, modules, interfaces, dependencies, and config examples.
- Expose plugins in Manager GUI.
- Support installable external module packages.

## 8. Tests and CI are unclear for this tree

The repository contains old CI files, but no obvious modern test workflow in the root. Hardware-heavy code is hard to test without devices.

Recommended action:

- Add dummy-hardware integration tests.
- Add config validation tests.
- Add interface conformance tests for hardware modules.
- Add smoke tests that start Manager with a dummy config.

## 9. Error handling and safety need a modern pass

Lab-control software must fail safely. Some modules log exceptions, but safety policies are not centralized.

Recommended action:

- Define safe shutdown behavior per hardware interface.
- Add timeout handling and retry policies for device IO.
- Add user-visible error categories: config error, device unavailable, unsafe value, runtime failure.
- Add dry-run mode for workflow planning.

## 10. Python and dependency modernization

The code style reflects an older Python/PyQt ecosystem. Some patterns may be difficult to maintain with modern packaging.

Recommended action:

- Define supported Python and Qt versions.
- Move dependency metadata into modern packaging files.
- Replace ad hoc import paths with package entry points where possible.
- Keep compatibility layers small and documented.
