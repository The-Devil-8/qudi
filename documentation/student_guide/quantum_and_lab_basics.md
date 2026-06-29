# Quantum and Lab Basics for CSE Students

## What Qudi controls

Qudi is used for experiments where software must coordinate many instruments at once. Think of it as an operating system for a lab setup:

- A laser shines light on a sample.
- A microscope or scanner moves the light spot or sample position.
- A detector counts photons or reads analog voltages.
- A microwave source, pulse generator, or AWG applies controlled signals.
- The software varies one or more parameters, records measured data, plots it, and saves the result.

The physics may be quantum, but the software problem is familiar: device drivers, interfaces, event loops, state machines, dependency injection, GUIs, files, logs, and data pipelines.

## Minimal physics vocabulary

| Term | Senior-secondary physics analogy | Why it matters in Qudi |
| --- | --- | --- |
| Photon | A packet of light energy. | Many experiments count photons from a sample. |
| Fluorescence | A material absorbs light and emits light of another color. | Brightness becomes the measured signal. |
| Spin | A quantum property that behaves partly like tiny angular momentum. | NV center experiments manipulate spin states. |
| Resonance | A system responds strongly at a particular frequency. | ODMR finds resonance by sweeping microwave frequency. |
| Microwave | Electromagnetic wave with lower frequency than visible light. | Used to drive spin transitions. |
| Pulse | A signal turned on/off for a controlled duration. | Pulsed experiments measure time-dependent behavior. |
| Count rate | Number of detected photons per second. | Common output from counters and confocal scans. |
| Spectrum | Signal as a function of wavelength or frequency. | Used by spectrometer and ODMR workflows. |

## NV center in one paragraph

An NV center is a defect in diamond where one nitrogen atom sits next to a missing carbon atom. It can emit fluorescence when illuminated by a laser. Its fluorescence changes slightly depending on its spin state. By applying microwave frequencies and measuring fluorescence, the experiment can infer magnetic fields, resonance frequencies, and other sample properties.

You do not need advanced quantum mechanics to understand this codebase. For software work, treat the experiment as:

```text
set instrument parameters -> wait or scan -> read signal -> fit/plot/save -> repeat
```

## Example: ODMR as a CSE workflow

ODMR means optically detected magnetic resonance. In simple terms:

1. Turn on laser and photon counter.
2. Sweep microwave frequency across a range.
3. Record brightness at each frequency.
4. Look for a dip or peak in brightness.
5. Fit the dip/peak to estimate the resonance frequency.

In Qudi this maps roughly to:

- `hardware/microwave/*`: set microwave frequency and power.
- `hardware/*counter*` or `hardware/national_instruments_x_series.py`: count photons.
- `logic/odmr_logic.py`: coordinate the sweep and data processing.
- `gui/odmr/odmrgui.py`: expose controls and plots.
- `logic/save_logic.py`: save the measurement.

## Example: confocal scan as a CSE workflow

A confocal scan measures brightness while changing position. The result is an image-like grid.

1. Move scanner to `(x, y)` or `(x, y, z)`.
2. Count photons for a short time.
3. Store the count in a matrix.
4. Repeat over a grid.
5. Display the matrix as an image.

In Qudi this maps roughly to:

- `interface/confocal_scanner_interface.py`: scanner contract.
- `hardware/confocal_scanner_dummy.py` or NI scanner hardware: scanner implementation.
- `logic/confocal_logic.py`: scan orchestration.
- `gui/confocal/confocalgui.py`: scan controls and image display.

## What to focus on as a CSE student

Start with the software shape:

- How modules are discovered and loaded.
- How dependencies are specified in config instead of direct imports.
- How interfaces isolate hardware-specific details.
- How Qt signals carry events from logic to GUI.
- How experiment values become arrays, fits, plots, and saved files.

Then learn the physics names as labels for those workflows.
