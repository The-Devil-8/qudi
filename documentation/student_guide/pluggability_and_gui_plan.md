# Pluggability and GUI Improvement Plan

## Goal

Make Qudi more versatile by reducing hardcoded assumptions, formalizing plugins, and improving GUI maintainability while preserving existing module behavior.

## Design principles

- Keep hardware, logic, and GUI separated.
- Make lab-specific values explicit in config or profiles.
- Prefer interfaces and capability metadata over type checks and hardcoded widget layouts.
- Keep dummy devices first-class so students and developers can test without instruments.
- Migrate gradually; avoid breaking existing configs all at once.

## Target architecture

```text
Plugin manifest
    |
    +-- module declarations
    +-- interface implementations
    +-- config templates
    +-- GUI panels
    +-- capability schemas
    +-- docs

Qudi plugin registry
    |
    +-- validates plugin metadata
    +-- exposes plugins to Manager GUI
    +-- resolves module classes
    +-- validates config before startup
```

## Phase 1: Stabilize what exists

Deliverables:

- Mark prototype/copy files as active, legacy, or removable.
- Add a hardcoding audit script.
- Add a dummy-only smoke config.
- Add a config validation command.
- Document module creation properly.

Acceptance checks:

- A new student can run Qudi with dummy modules.
- The audit script lists known hardcoding without manual searching.
- Manager startup gives readable config errors.

## Phase 2: Capability metadata

Add a common capability pattern for interfaces.

Example shape:

```python
{
    "channels": ["d_ch1", "d_ch2"],
    "frequency": {"min": 1e6, "max": 3e9, "step": 1e3, "unit": "Hz"},
    "power": {"min": -120, "max": 20, "step": 0.1, "unit": "dBm"},
    "supports_external_clock": True,
    "safe_shutdown": ["output_off"]
}
```

Deliverables:

- Capability schema docs.
- Default implementation per interface.
- Implement capabilities for dummy devices first.
- Add GUI helper widgets that consume capability metadata.

Acceptance checks:

- GUI controls can be generated or constrained from device capabilities.
- Invalid values are rejected before hardware calls.

## Phase 3: Plugin manifests

Introduce a manifest file for external module packages.

Example:

```yaml
name: qudi-example-ni
version: 0.1.0
modules:
  hardware:
    ni_x_series:
      class: national_instruments_x_series.NationalInstrumentsXSeries
      interfaces:
        - SlowCounterInterface
        - ConfocalScannerInterface
        - ODMRCounterInterface
config_templates:
  - config_templates/ni_confocal_odmr.cfg
docs:
  - docs/ni_x_series.md
```

Deliverables:

- Plugin discovery from configured directories.
- Manifest validation.
- Manager GUI plugin list.
- Config template import from plugin.

Acceptance checks:

- A plugin can be added without editing `hardware/`, `logic/`, or `gui/`.
- Users can see which modules a plugin provides.

## Phase 4: GUI modernization

Keep PyQt, but reduce per-GUI duplication.

Deliverables:

- Shared icon/theme registry.
- Shared settings panels for ranges, channels, files, fits, and device status.
- View-model layer for large GUIs such as confocal, ODMR, and pulsed.
- Dynamic controls from capability metadata.
- Consistent error and validation display.

Recommended GUI structure:

```text
gui/<feature>/
    <feature>gui.py       # Qudi GUI module wrapper
    widgets.py            # reusable feature widgets
    view_model.py         # GUI state and conversion
    ui_*.ui               # only if Designer layout is still useful
```

Acceptance checks:

- A device with different channels or ranges does not require GUI code edits.
- Common controls behave consistently across GUIs.
- GUI modules stay small enough to review safely.

## Phase 5: Config profiles and templates

Add named profiles:

- `dummy`: no hardware required.
- `nv_confocal`: typical NV confocal setup.
- `nv_odmr`: typical ODMR setup.
- `pulsed`: pulsed measurement setup.
- `teaching`: safe simulated setup for students.

Deliverables:

- Profile schema.
- Profile inheritance or includes.
- Local override file convention.
- GUI selector for profiles.

Acceptance checks:

- Lab-specific values live outside source code.
- Students can switch from dummy profile to real hardware by changing config only.

## Phase 6: Testing and quality gates

Deliverables:

- Interface conformance tests.
- Dummy integration tests.
- Config validation tests.
- GUI smoke tests for main windows.
- Static audit for hardcoding and deprecated APIs.

Acceptance checks:

- CI can run without lab hardware.
- New hardware drivers must pass interface tests.
- New GUI code must not introduce direct device address defaults.

## Migration checklist for each module

For every module, answer:

- What interface does it implement or consume?
- Which values are hardcoded?
- Which values should be `ConfigOption`?
- Which values should be `StatusVar`?
- Which constraints should become capability metadata?
- Does it use deprecated `get_connector()` or `getConfiguration()`?
- Does it directly know about another concrete module?
- Can it run against a dummy device?
- Does the GUI validate before calling logic?

## Near-term backlog

| Priority | Task |
| --- | --- |
| P0 | Add dummy startup config and verify Manager starts without hardware. |
| P0 | Replace `dummy_interface` connectors in prototype modules or mark them experimental. |
| P0 | Move copied/prototype files out of active module paths. |
| P1 | Add hardcoding audit script. |
| P1 | Add config schema validation. |
| P1 | Create capability metadata schema and implement it for dummy modules. |
| P1 | Centralize icon and stylesheet lookup. |
| P2 | Add plugin manifest discovery. |
| P2 | Build Manager GUI plugin/config-template view. |
| P2 | Refactor large GUIs into view-model and reusable widget pieces. |

## Risks

- Changing config behavior can break existing lab setups.
- Real hardware is hard to test automatically.
- GUI refactors can create subtle operator workflow regressions.
- Plugins need versioning and dependency rules from the beginning.

Mitigation:

- Keep old configs working during migration.
- Start with dummy modules.
- Add migration warnings before removals.
- Create screenshots and smoke tests for important GUI windows.
