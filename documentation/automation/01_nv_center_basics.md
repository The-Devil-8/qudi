# 01 — NV Center Basics

## What Is an NV Center?

A **Nitrogen-Vacancy (NV) center** is a point defect in the diamond crystal lattice. It consists of:

- A **nitrogen atom** substituting for a carbon atom in the diamond lattice
- An adjacent **vacant lattice site** (missing carbon atom)

```
    C — C — C — C
    |   |   |   |
    C — N — V — C       N = Nitrogen atom
    |   |   |   |       V = Vacancy (missing carbon)
    C — C — C — C       C = Carbon atoms
```

NV centers exist in two charge states:
- **NV⁻** (negatively charged) — the useful one for quantum applications
- **NV⁰** (neutral) — emits at a different wavelength, generally undesired

## Why NV Centers Matter

NV centers are the workhorse of diamond quantum technology because they are:

1. **Single quantum systems** at room temperature — no cryogenics needed
2. **Optically addressable** — can be excited with green laser (532 nm) and emit red fluorescence (637–800 nm)
3. **Spin-dependent fluorescence** — the brightness depends on the electron spin state, enabling optical readout of quantum states
4. **Sensitive to magnetic fields** — the spin transition frequencies shift with applied magnetic field (2.8 MHz/Gauss)
5. **Long coherence times** — can maintain quantum states for milliseconds at room temperature

## Fluorescence Properties

When a green laser (532 nm) excites an NV⁻ center:

```
                    ³E (excited state)
                   / |
    Green 532nm → /  | Non-radiative (spin-dependent)
                 /   ↓
                /   Singlet states
    Red 637nm ← \   ↑
  (fluorescence) \  | Non-radiative  
                  \ |
                    ³A₂ (ground state)
```

Key fluorescence characteristics:
- **Excitation**: 532 nm (green laser)
- **Zero Phonon Line (ZPL)**: 637 nm
- **Emission band**: 637–800 nm (broad red/near-IR emission)
- **Typical count rate**: 50,000 – 300,000 counts/s for a single NV center
- **Background**: Diamond autofluorescence + detector dark counts, typically 5,000 – 20,000 counts/s

## Why We Need to "Find" NV Centers

NV centers are:
- **Randomly distributed** in the diamond (unless implanted in a pattern)
- **Submicron in size** — the defect is a single atomic-scale point
- **Diffraction-limited** — the optical spot size is ~300 nm, so NV centers appear as small bright dots

To perform quantum measurements on a specific NV center, we must:

1. **Locate it** — find its (x, y, z) coordinates in the diamond
2. **Distinguish it** from background fluorescence and other defects
3. **Optimize the focus** — position the confocal spot exactly on the NV center to maximize fluorescence signal
4. **Track it** — compensate for sample drift over time

## How NV Centers Appear in a Confocal Scan

In a confocal fluorescence image:
- **Background**: Relatively uniform, low-intensity fluorescence (dark/cold colors in the Inferno colormap)
- **NV centers**: Bright, localized spots (hot/bright colors in Inferno) with a Gaussian-like intensity profile
- **Typical spot FWHM**: ~300–500 nm (diffraction-limited)

```
  Confocal XY scan image (Inferno colormap):
  
  ┌────────────────────────────┐
  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │  ▓ = low counts (dark purple/black)
  │ ▓▓▓▓▓▓▓▓▓░▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │  ░ = medium counts (orange/red)  
  │ ▓▓▓▓▓▓▓░█░▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │  █ = high counts (yellow/white) — NV center!
  │ ▓▓▓▓▓▓▓▓▓░▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░▓▓▓▓▓ │  Another NV →  ░█░
  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░█░▓▓▓▓ │
  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░▓▓▓▓▓ │
  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
  └────────────────────────────┘
  
  Color bar:  ████████████████████
              0    50k   100k  150k  counts/s
           (black → purple → red → yellow → white)
```

## Confirming an NV Center

After finding a bright spot, it can be confirmed as a single NV⁻ center by:

1. **ODMR (Optically Detected Magnetic Resonance)**: Characteristic dip at ~2.87 GHz in the fluorescence vs. microwave frequency sweep
2. **Antibunching (g² measurement)**: Photon autocorrelation showing g²(0) < 0.5 proves it's a single emitter
3. **Fluorescence spectrum**: Checking for the 637 nm ZPL and the characteristic phonon sideband
4. **Saturation curve**: Fluorescence vs. laser power shows single-emitter saturation behavior

## Related Qudi Modules

| Module | Role |
|--------|------|
| `logic/confocal_logic.py` | Acquires the fluorescence scan image |
| `logic/optimizer_logic.py` | Refines the position on a known bright spot |
| `logic/odmr_logic.py` | Runs ODMR to confirm NV identity |
| `logic/nv_calculator_logic.py` | Calculates magnetic field from ODMR frequencies |
