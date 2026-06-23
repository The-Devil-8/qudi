# Step D: Single-Photon & Spin Validation — Architecture & Roadmap

> **Document 13 of the Automation Series**
> This document covers the **validation steps that are NOT yet implemented**
> in the current Auto NV Finder pipeline, and provides a roadmap for their integration.

---

## Current Pipeline vs. Full Pipeline

```
CURRENT IMPLEMENTATION (Steps A–C):
────────────────────────────────────
[A] Confocal XY Scan → fluorescence image
[B] CIP Detection → candidate NV positions
[C] Position Optimization → refined coordinates + POI registration
    ✅ DONE — pipeline stops here

MISSING (Step D — Validation):
──────────────────────────────
[D.1] Auto-HBT → g²(τ) measurement → single-photon confirmation
[D.2] Auto-ODMR → frequency sweep → NV⁻ charge state confirmation
    ❌ NOT IMPLEMENTED
```

> [!IMPORTANT]
> The current automation only confirms that a bright, circular fluorescence
> spot exists and that its position can be optimized. It does **not** confirm:
> - Whether the emitter is a **single** NV (vs. ensemble/cluster)
> - Whether it is in the **NV⁻** charge state (vs. NV⁰ or structural artifact)

---

## D.1: Auto-HBT (Single-Photon Validation)

### What It Does

The Hanbury Brown and Twiss (HBT) experiment measures the second-order
autocorrelation function g²(τ):

```
g²(τ) = ⟨I(t) · I(t+τ)⟩ / ⟨I(t)⟩²
```

For a single quantum emitter:
- **g²(0) < 0.5** → confirmed single photon source (single NV center)
- **g²(0) ≈ 1** → classical light (multiple emitters / ensemble)
- **g²(0) > 1** → bunched light (thermal source)

### Hardware Required

```
Laser ──→ [Diamond Sample]
                │
            [Objective]
                │
          [50:50 Beam Splitter]
           /              \
        [APD 1]          [APD 2]
           │                │
        [Start]          [Stop]
              \          /
           [Time Correlator]
              (e.g., PicoHarp, TimeHarp, HydraHarp)
```

- Two single-photon avalanche detectors (APDs)
- A 50:50 beam splitter
- A time-correlated single photon counting (TCSPC) module
- Optical path switching mechanism (flip mirror or fiber switch)

### Status in This Codebase

**Not available.** No HBT / autocorrelation logic module exists. The `pulsed/` modules
have some time-tagging capabilities but are not configured for HBT measurement.

### Integration Architecture (Future)

```python
class AutoHBTLogic(GenericLogic):
    """Automated HBT g²(τ) measurement at POI positions."""

    # Connectors
    correlator = Connector(interface='AutocorrelationInterface')
    poimanagerlogic = Connector(interface='PoiManagerLogic')
    confocallogic = Connector(interface='ConfocalLogic')

    # Parameters
    integration_time = StatusVar('integration_time', 60.0)  # seconds
    g2_threshold = StatusVar('g2_threshold', 0.5)

    def measure_g2_at_poi(self, poi_name):
        """Move to POI, run HBT, return g²(0)."""
        pos = self.poimanagerlogic().get_poi_position(poi_name)
        self.confocallogic().set_position(pos)
        histogram = self.correlator().acquire(self.integration_time)
        g2_0 = self._fit_g2(histogram)
        return g2_0

    def validate_all_pois(self):
        """Run HBT on all NV_ POIs and tag results."""
        for poi_name in self.poimanagerlogic().poi_names:
            if poi_name.startswith('NV_'):
                g2 = self.measure_g2_at_poi(poi_name)
                if g2 < self.g2_threshold:
                    # Rename: NV_001 → sNV_001 (single NV confirmed)
                    self.poimanagerlogic().rename_poi(
                        poi_name, 's' + poi_name)
```

### Required New Interface

```python
class AutocorrelationInterface(metaclass=InterfaceMeta):
    """Interface for time-correlated photon counting hardware."""

    @abstract_interface_method
    def acquire(self, integration_time, bin_width=1e-9):
        """Acquire a g²(τ) histogram."""
        pass

    @abstract_interface_method
    def get_histogram(self):
        """Return the current histogram data."""
        pass
```

---

## D.2: Auto-ODMR (Spin Validation)

### What It Does

Optically Detected Magnetic Resonance (ODMR) measures the NV⁻ spin resonance:

1. Apply continuous laser excitation (532 nm)
2. Sweep microwave frequency (2.7–3.0 GHz)
3. Monitor fluorescence counts

For an NV⁻ center:
- **~2.87 GHz**: Resonance dip in fluorescence (10–30% contrast)
- Without external B-field: single dip at ~2.87 GHz
- With external B-field: two dips split by 2γB (γ = 28 MHz/mT)

```
         ┌───────────────────────────────────┐
Counts   │                                   │
(c/s)    │     ────────            ──────────│
         │              \        /            │
         │               \      /             │
         │                ──────              │
         │              2.87 GHz              │
         └───────────────────────────────────┘
                   Frequency (GHz)
```

### Status in This Codebase

**Available!** The `ODMRLogic` module at `logic/odmr_logic.py` provides:
- `start_odmr_scan()` — start a frequency sweep
- `stop_odmr_scan()` — stop the sweep
- `sigOdmrPlotsUpdated` — signal with frequency and count data
- Lorentzian and Gaussian dip fitting via `FitLogic`
- Support for both LIST and SWEEP microwave modes

### Integration Architecture (Future)

The Auto-ODMR validation would be added as a new step in `AutoNVFinderLogic`:

```python
# In auto_nv_finder_logic.py — future extension

class AutoNVFinderLogic(GenericLogic):
    # Add optional ODMR connector
    odmrlogic = Connector(interface='ODMRLogic', optional=True)

    # Validation parameters
    enable_odmr_validation = StatusVar('enable_odmr_validation', False)
    odmr_mw_start = StatusVar('odmr_mw_start', 2.8e9)    # Hz
    odmr_mw_stop = StatusVar('odmr_mw_stop', 2.95e9)      # Hz
    odmr_mw_step = StatusVar('odmr_mw_step', 1e6)         # Hz
    odmr_min_contrast = StatusVar('odmr_min_contrast', 0.05)  # 5%

    def _validate_candidate_odmr(self, candidate):
        """Run a rapid ODMR sweep at the candidate's optimized position.

        @param CandidateNV candidate: accepted candidate to validate
        @return bool: True if ODMR dip confirms NV⁻
        """
        if not self.enable_odmr_validation:
            return True  # Skip validation

        odmr = self.odmrlogic()
        if odmr is None:
            self.log.warning('ODMRLogic not connected — skipping validation')
            return True

        # 1. Configure ODMR for a rapid scan
        odmr.set_sweep_parameters(
            starts=[self.odmr_mw_start],
            stops=[self.odmr_mw_stop],
            steps=[self.odmr_mw_step],
            power=odmr.sweep_mw_power
        )

        # 2. Start ODMR scan
        odmr.start_odmr_scan()

        # 3. Wait for completion (with timeout)
        # ... (signal-based wait) ...

        # 4. Analyze: look for dip
        freq = odmr.odmr_plot_x
        counts = odmr.odmr_plot_y[0]  # First channel
        dip_depth = 1.0 - (counts.min() / counts.max())

        # 5. Accept if dip contrast > threshold
        if dip_depth > self.odmr_min_contrast:
            candidate.odmr_contrast = dip_depth
            candidate.odmr_frequency = freq[np.argmin(counts)]
            return True
        else:
            candidate.rejection_reason = (
                'ODMR: no dip (contrast {:.1%})'.format(dip_depth))
            return False
```

### Pipeline Extension

The full pipeline with validation would be:

```
[1] CIP Detection → candidates
[2] Position Optimization → refined positions
[3] ODMR Validation → confirm NV⁻ charge state      ← NEW
[4] HBT Validation → confirm single emitter          ← NEW (future)
[5] Register as POI
```

In the candidate lifecycle:

```
pending → optimizing → accepted → odmr_validating → confirmed/rejected
                    ↘ rejected
```

### Why Not Implemented Yet

1. **Scanner resource locking**: ODMR requires the microwave source and counter. Running ODMR after each optimization requires careful hardware mutex management between ConfocalLogic and ODMRLogic.

2. **Time cost**: Each ODMR sweep takes 30–120 seconds. For 20 candidates, that's 10–40 minutes of additional validation time.

3. **Hardware coupling**: Moving between confocal scanning and ODMR requires switching the photon counting mode (continuous → gated/triggered).

4. **User preference**: Many users prefer to run ODMR validation manually on selected POIs rather than all candidates.

---

## Manual Validation Workflow (Current Best Practice)

Until Auto-ODMR is integrated, use this workflow:

1. **Run Auto NV Finder** → get POIs (`NV_001`, `NV_002`, ...)
2. For each POI of interest:
   a. In POI Manager: select POI → click **Go to POI**
   b. Open ODMR GUI → set frequency range 2.8–2.95 GHz
   c. Click **Start ODMR Scan**
   d. Look for dip at ~2.87 GHz with >5% contrast
   e. If confirmed: rename POI to `sNV_001` (or add a tag)
   f. If no dip: delete the POI (it's an artifact)

3. For HBT (if hardware available):
   a. Switch optical path to HBT beam splitter
   b. Run g²(τ) acquisition for 60+ seconds
   c. Check g²(0) < 0.5

---

## Summary

| Validation Step | Hardware | Software | Status |
|----------------|----------|----------|--------|
| **Position Optimization** | Scanner + APD | OptimizerLogic | ✅ Implemented |
| **Auto-ODMR** | Scanner + APD + Microwave source | ODMRLogic (exists) | ❌ Not integrated |
| **Auto-HBT** | 2× APD + Beam splitter + TCSPC | New module needed | ❌ Not available |

The most impactful next step is **Auto-ODMR integration**, since the ODMRLogic
module already exists and only requires orchestration code to connect it to the
Auto NV Finder pipeline.
