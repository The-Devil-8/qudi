# Qudi Student Guide

This guide explains this Qudi codebase for a CSE student who knows senior-secondary physics but has not studied quantum experiments yet.

Qudi is not mainly a quantum simulator. It is a lab-control framework: it connects Python code to real instruments, runs measurement workflows, saves data, and exposes GUI windows for operators. Many modules are written for experiments on color centers in diamond, especially nitrogen-vacancy centers, but the software pattern is general enough for lasers, counters, cameras, motors, magnets, spectrometers, pulse generators, and process controllers.

## Reading order

1. [Quantum and lab basics for CSE students](quantum_and_lab_basics.md)
2. [Architecture and runtime model](architecture_and_runtime.md)
3. [Existing module catalog](module_catalog.md)
4. [Common experiment workflows](experiment_workflows.md)
5. [Shortcomings and incomplete areas](shortcomings_and_incomplete_areas.md)
6. [Hardcoding audit](hardcoding_audit.md)
7. [Pluggability and GUI improvement plan](pluggability_and_gui_plan.md)

## One-screen mental model

```text
config/*.cfg
    |
    v
core.manager.Manager
    |
    +-- hardware modules: talk to devices or dummy devices
    +-- logic modules: measurement algorithms and data processing
    +-- gui modules: PyQt windows that call logic modules
    +-- interface modules: contracts that hardware modules must implement
    +-- interfuse modules: adapters that combine or reshape modules
```

The most important design idea is dependency injection through configuration. A module should say what interface it needs, and the config file should decide which concrete device or dummy implementation is connected.

## Main source directories

| Directory | Purpose |
| --- | --- |
| `core/` | Manager, module lifecycle, config loading, connectors, logging, remote modules, threads. |
| `interface/` | Abstract contracts such as counters, cameras, lasers, motors, microwave sources, and pulsers. |
| `hardware/` | Concrete device drivers and dummy devices. |
| `logic/` | Experiment workflows, fitting, saving, scanning, pulsed measurements, automation, PID, ODMR, confocal, etc. |
| `gui/` | PyQt GUI modules and `.ui` files. |
| `qtwidgets/` | Reusable custom widgets. |
| `config/` | Example and local YAML-style configuration files. |
| `documentation/` | Existing docs plus this student guide. |

## Terms used in these docs

| Term | Simple meaning |
| --- | --- |
| Module | A loadable Python class managed by Qudi. |
| Hardware module | Code that talks to an instrument or emulates one. |
| Logic module | Code that coordinates a measurement or analysis. |
| GUI module | A window or panel that presents controls and plots. |
| Interface | A required method contract, like a Java interface or Python ABC. |
| Connector | A declared dependency from one module to another. |
| ConfigOption | A value read from a config file during module creation. |
| StatusVar | A value saved when a module closes and restored later. |
| Interfuse | Adapter module that connects incompatible pieces or combines devices. |
