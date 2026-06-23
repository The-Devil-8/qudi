# 09 — GUI Integration

## Overview

The Auto NV Finder integrates into the existing POI Manager GUI as a new dock widget. This keeps the workflow unified — the user finds NVs and manages POIs from the same window.

## GUI Layout

```
┌─────────────────────────────────────────────────────────────┐
│  POI Manager                                        [_][□][X]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────┐  ┌──────────────────┐  │
│  │                                 │  │  Color Bar       │  │
│  │     Confocal Color Image        │  │  ┌──┐            │  │
│  │     (Inferno colormap)          │  │  │██│ 150 kc/s   │  │
│  │                                 │  │  │██│            │  │
│  │   🟢 NV_001   🟡 candidate_3    │  │  │██│            │  │
│  │         🔵 optimizing...        │  │  │██│            │  │
│  │   🟢 NV_002                     │  │  │██│            │  │
│  │              🔴 rejected_5      │  │  │██│ 0 kc/s     │  │
│  │                                 │  │  └──┘            │  │
│  └─────────────────────────────────┘  └──────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ Auto NV Finder                                          ││
│  │                                                         ││
│  │  [▶ Start Auto Find]  [⏹ Stop]    Progress: ████░░ 4/7  ││
│  │                                                         ││
│  │  Detection Parameters:                                  ││
│  │  Threshold σ: [5.0 ▼]  Min Intensity: [1000  ]  c/s    ││
│  │  Spot diameter: [1.5 ] μm                               ││
│  │                                                         ││
│  │  ┌─────────────────────────────────────────────────────┐││
│  │  │ # │ Name     │ Position         │ Intensity │Status │││
│  │  ├───┼──────────┼──────────────────┼───────────┼───────┤││
│  │  │ 1 │ NV_001   │ (5.2, 3.1) μm   │ 125,000   │  ✅   │││
│  │  │ 2 │ NV_002   │ (8.7, 6.4) μm   │ 98,000    │  ✅   │││
│  │  │ 3 │ cand_003 │ (2.1, 7.8) μm   │ 45,000    │  🔵   │││
│  │  │ 4 │ cand_004 │ (4.4, 1.2) μm   │ 38,000    │  🟡   │││
│  │  │ 5 │ rej_005  │ (9.1, 2.3) μm   │ 12,000    │  ❌   │││
│  │  └─────────────────────────────────────────────────────┘││
│  │                                                         ││
│  │  Log:                                                   ││
│  │  [12:05:31] CIP detection found 7 candidates            ││
│  │  [12:05:33] Optimizing candidate 1 at (5.2, 3.1) μm    ││
│  │  [12:05:41] ✅ NV_001 accepted (R²=0.94, 125 kc/s)     ││
│  │  [12:05:43] Optimizing candidate 2 at (8.7, 6.4) μm    ││
│  │  [12:05:50] ✅ NV_002 accepted (R²=0.89, 98 kc/s)      ││
│  │  [12:05:52] Optimizing candidate 3 at (2.1, 7.8) μm    ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ┌──────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  POI Editor      │  │ POI Tracker  │  │ Sample Shift  │  │
│  │  (existing)      │  │ (existing)   │  │ (existing)    │  │
│  └──────────────────┘  └──────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Dock Widget Components

### 1. Control Panel

| Widget | Type | Function |
|--------|------|----------|
| Start Auto Find | QPushButton | Begins the full CIP pipeline |
| Stop | QPushButton | Gracefully stops the automation |
| Progress Bar | QProgressBar | Shows current/total candidates |
| State Label | QLabel | Shows current state (Scanning/Detecting/Optimizing/Done) |

### 2. Detection Parameters Panel

| Widget | Type | Default | Description |
|--------|------|---------|-------------|
| Threshold σ | QDoubleSpinBox | 5.0 | Detection threshold in noise sigma units |
| Min Intensity | QSpinBox | 1000 | Minimum absolute counts/s |
| Spot Diameter | QDoubleSpinBox | 1.5 | Expected NV spot diameter (μm) |
| Auto Color Range | QCheckBox | ✓ | Auto-adjust color scale for detection |

### 3. Candidate Table

A `QTableWidget` showing all detected candidates:

| Column | Data | Description |
|--------|------|-------------|
| # | Integer | Candidate number (ordered by intensity) |
| Name | String | Auto-generated or assigned POI name |
| Position | String | (x, y) in μm with 2 decimal places |
| Intensity | Integer | Peak counts/s |
| Status | Icon + Text | 🟡 Pending / 🔵 Optimizing / ✅ Accepted / ❌ Rejected |

Row colors match the marker colors on the image.

### 4. Color Image Overlay

Candidate markers are rendered as circle ROIs on top of the existing confocal color image:

```python
class CandidateMarker(pg.EllipseROI):
    """Marker for an NV candidate on the color image."""
    
    STATUS_COLORS = {
        'pending':    {'color': 'FF0', 'width': 2},   # Yellow
        'optimizing': {'color': '00F', 'width': 3},   # Blue (thicker)
        'accepted':   {'color': '0F0', 'width': 2},   # Green
        'rejected':   {'color': 'F00', 'width': 1},   # Red (thinner)
    }
```

Markers are added to the same `roi_map_ViewWidget` that holds the POI markers, ensuring correct coordinate alignment.

### 5. Log Panel

A `QTextEdit` (read-only) showing timestamped log entries:
- Detection start/end and candidate count
- Each optimization attempt with position
- Accept/reject decisions with reason and quality metrics
- Errors and warnings

## Signal Connections

### GUI → Logic

```python
# Start/Stop controls
start_button.clicked → auto_nv_finder.start_auto_find
stop_button.clicked  → auto_nv_finder.stop_auto_find

# Parameter changes
threshold_spinbox.valueChanged → auto_nv_finder.set_threshold
intensity_spinbox.valueChanged → auto_nv_finder.set_min_intensity
diameter_spinbox.valueChanged  → auto_nv_finder.set_spot_diameter
```

### Logic → GUI

```python
# Pipeline progress
auto_nv_finder.sigStateChanged     → update_state_label
auto_nv_finder.sigCandidatesFound  → populate_candidate_table, add_markers
auto_nv_finder.sigCandidateUpdate  → update_table_row, update_marker_color
auto_nv_finder.sigProgressUpdate   → update_progress_bar
auto_nv_finder.sigAutoFindComplete → show_summary, enable_start_button
```

All connections use `QtCore.Qt.QueuedConnection` for thread safety.

## User Interaction Flow

### Starting Auto Find

1. User adjusts detection parameters (optional — defaults are reasonable)
2. User clicks **Start Auto Find**
3. Start button becomes disabled, Stop button enables
4. State label shows "Scanning..."
5. Confocal scan begins (color image updates line by line)
6. State → "Detecting..." — CIP pipeline runs
7. Candidate markers appear on color image (all yellow)
8. Candidate table populates
9. State → "Optimizing..." — first candidate turns blue
10. As each candidate is processed, marker changes color (green/red)
11. Table rows update with status
12. Progress bar fills up
13. When done, state → "Complete", Start button re-enables

### Stopping Mid-Run

1. User clicks **Stop**
2. Current optimization completes (not interrupted mid-scan)
3. All remaining pending candidates marked as "skipped"
4. Results so far are kept — accepted POIs remain registered
5. Log shows "Stopped by user after N/M candidates"

### Interacting with Results

- **Click on a candidate row** → selects it as active POI, centers the image on it
- **Double-click on a rejected candidate** → re-runs optimization (single attempt)
- **Right-click on candidate** → context menu: Go to, Optimize, Delete, Add as POI

## Styling

The dock widget follows Qudi's existing style:
- Same font, spacing, and widget sizes as other POI Manager docks
- `QGroupBox` with title for each section (Detection Parameters, Candidates, Log)
- `QProgressBar` uses the system theme
- Table alternating row colors for readability
- Log text uses monospace font with color-coded lines

## Implementation Notes

### Adding to POI Manager GUI

The dock widget is added in `PoiManagerGui.on_activate()`:

```python
# In poimangui.py
auto_nv_finder = Connector(interface='AutoNVFinderLogic', optional=True)

def on_activate(self):
    # ... existing setup ...
    
    # Add Auto NV Finder dock (only if the logic module is available)
    if self.auto_nv_finder.is_connected:
        self.__init_auto_nv_finder_dock()
```

Making the connector `optional=True` ensures backward compatibility — the POI Manager works fine without the AutoNVFinderLogic loaded.

### Marker Z-Order

Candidate markers are drawn behind POI markers so accepted POIs are always visible:

```python
candidate_marker.setZValue(-1)  # Behind POI markers (default Z=0)
```
