# -*- coding: utf-8 -*-
"""
Interactive GUI-based Cell Processor Algorithm Tuner.

Allows interactive parameter tuning, visualization, and algorithm comparison
for close-scan confocal cell boundary segmentation and NV candidate extraction.

Features:
- Robust multi-format confocal parser (supports 4-column .dat, direct 2D matrix .dat, .png).
- Multi-algorithm benchmarking (Seeded Hysteresis, Legacy Otsu, Gated Local Adaptive, Watershed, Macro-Constrained).
- Real-time parameter controls with clean 2-row layout (never truncates slider numbers).
- QSplitter resizable layout between controls and graphics.
- Outside-NV detection & false-positive boundary protrusion risk monitor.
- pyqtgraph visualization with colormaps, histogram LUT, split/unified views, and vector contours.
- Presets and one-click configuration export (Python dict / YAML / JSON).
"""

import os
import sys
import json
import time
import numpy as np
from PIL import Image

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from qtpy import QtWidgets, QtCore, QtGui
except ImportError:
    try:
        from pyqtgraph.Qt import QtWidgets, QtCore, QtGui
    except ImportError:
        try:
            from PyQt5 import QtWidgets, QtCore, QtGui
        except ImportError:
            from PySide2 import QtWidgets, QtCore, QtGui

import pyqtgraph as pg

from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    binary_fill_holes,
    binary_opening,
    binary_closing,
    binary_erosion,
    binary_dilation,
    binary_propagation,
    label,
    find_objects,
    grey_opening,
    distance_transform_edt,
)

try:
    from skimage.filters import threshold_otsu, threshold_local
    from skimage.measure import find_contours
    from skimage.segmentation import watershed
    from skimage.feature import peak_local_max
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.cell_region_processor import (
    CellRegionProcessor as LegacyCellRegionProcessor,
    CellProcessingResult,
)
from upgrade.cell_region_processor import (
    CellRegionProcessor as UpgradedCellRegionProcessor,
)
from logic.poi_extractor import POIExtractor, POICandidate


# ======================================================================
# Robust File Parser for all Confocal Dat & Image Formats
# ======================================================================

def parse_any_confocal_file(filepath):
    """
    Parse any Qudi confocal data file (.dat or .png) into a unified (ny, nx, 4) array.
    Handles both 4-column point lists and 2D matrix count tables.
    """
    if filepath.endswith('.png'):
        img = Image.open(filepath).convert('L')
        raw_arr = np.array(img, dtype=float)
        ny, nx = raw_arr.shape
        x_coords = np.linspace(0, 40e-6, nx)
        y_coords = np.linspace(0, 40e-6, ny)
        image = np.zeros((ny, nx, 4), dtype=float)
        XX, YY = np.meshgrid(x_coords, y_coords)
        image[:, :, 0] = XX
        image[:, :, 1] = YY
        image[:, :, 3] = raw_arr
        return image, x_coords, y_coords, []

    with open(filepath, 'r') as f:
        lines = f.readlines()

    header = []
    data_lines = []
    x_min, x_max, y_min, y_max, z_pos = 0.0, 40e-6, 0.0, 40e-6, 0.0

    for line in lines:
        if line.startswith('#'):
            header.append(line)
            l_lower = line.lower()
            if 'x image min' in l_lower:
                try: x_min = float(line.split(':')[-1].strip().split()[0])
                except Exception: pass
            elif 'x image max' in l_lower:
                try: x_max = float(line.split(':')[-1].strip().split()[0])
                except Exception: pass
            elif 'y image min' in l_lower:
                try: y_min = float(line.split(':')[-1].strip().split()[0])
                except Exception: pass
            elif 'y image max' in l_lower:
                try: y_max = float(line.split(':')[-1].strip().split()[0])
                except Exception: pass
            elif 'z position' in l_lower:
                try: z_pos = float(line.split(':')[-1].strip().split()[0])
                except Exception: pass
        else:
            if line.strip():
                data_lines.append(line)

    if not data_lines:
        raise ValueError("No data lines found in file.")

    data = np.loadtxt(data_lines)

    if data.ndim == 2 and data.shape[1] in (3, 4):
        # 4-column coordinate list: x, y, z, counts
        x = data[:, 0]
        y = data[:, 1]
        z = data[:, 2] if data.shape[1] >= 4 else np.zeros_like(x)
        counts = data[:, 3] if data.shape[1] >= 4 else data[:, 2]

        ux = np.unique(x)
        uy = np.unique(y)
        nx = len(ux)
        ny = len(uy)

        if len(x) == nx * ny and nx > 1 and ny > 1:
            image = np.zeros((ny, nx, 4), dtype=float)
            image[:, :, 0] = x.reshape(ny, nx)
            image[:, :, 1] = y.reshape(ny, nx)
            image[:, :, 2] = z.reshape(ny, nx)
            image[:, :, 3] = counts.reshape(ny, nx)
            x_coords = ux
            y_coords = uy
        else:
            ny = int(np.sqrt(len(x)))
            nx = len(x) // ny
            image = np.zeros((ny, nx, 4), dtype=float)
            image[:, :, 0] = x[:ny*nx].reshape(ny, nx)
            image[:, :, 1] = y[:ny*nx].reshape(ny, nx)
            image[:, :, 2] = z[:ny*nx].reshape(ny, nx)
            image[:, :, 3] = counts[:ny*nx].reshape(ny, nx)
            x_coords = np.linspace(x.min(), x.max(), nx)
            y_coords = np.linspace(y.min(), y.max(), ny)

    elif data.ndim == 2 and data.shape[1] > 4:
        # Direct 2D matrix of counts
        ny, nx = data.shape
        x_coords = np.linspace(x_min, x_max, nx)
        y_coords = np.linspace(y_min, y_max, ny)
        image = np.zeros((ny, nx, 4), dtype=float)
        XX, YY = np.meshgrid(x_coords, y_coords)
        image[:, :, 0] = XX
        image[:, :, 1] = YY
        image[:, :, 2] = z_pos
        image[:, :, 3] = data
    else:
        raise ValueError(f"Unrecognized data shape {data.shape}")

    return image, x_coords, y_coords, header


def get_lut(name='inferno'):
    """Return 256-color lookup table from pyqtgraph."""
    try:
        cmap = pg.colormap.get(name)
        return cmap.getLookupTable(0.0, 1.0, 256)
    except Exception:
        lut = np.zeros((256, 3), dtype=np.uint8)
        lut[:, 0] = np.linspace(0, 255, 256)
        lut[:, 1] = np.linspace(0, 200, 256)
        lut[:, 2] = np.linspace(0, 50, 256)
        return lut


# ======================================================================
# Main Interactive Tuner Application
# ======================================================================

class InteractiveCellTuner(QtWidgets.QMainWindow):
    """
    Interactive GUI application for tuning cell processing & boundary detection algorithms.
    """

    def __init__(self, image_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Interactive Cell Processor Algorithm Tuner — Qudi NV Automation")
        self.resize(1500, 950)

        # Core logic instances
        self.roi_logic = ROISegmentationLogic()
        self.legacy_processor = LegacyCellRegionProcessor()
        self.upgraded_processor = UpgradedCellRegionProcessor()
        self.poi_extractor = POIExtractor()

        # Image state
        self.image = None           # (ny, nx, 4) Qudi image (x, y, z, counts)
        self.fluor = None           # (ny, nx) float fluorescence counts
        self.x_coords = None        # 1D array of x coordinates in meters
        self.y_coords = None        # 1D array of y coordinates in meters
        self.pixel_size_um = 0.33   # Pixel size in um
        self.current_filepath = ""

        # Optional paired macro reference
        self.macro_image = None
        self.macro_mask = None
        self.macro_x = None
        self.macro_y = None

        # Processing results
        self.current_result = None
        self.detected_candidates = []
        self.outside_candidates = []
        self.inside_candidates = []
        self.contour_items = []
        self.candidate_items = []

        # Presets library
        self.presets = self._init_presets()

        # UI Setup
        self._init_ui()

        # Load initial image if provided, or search for a good default
        if image_path and os.path.exists(image_path):
            self.load_image(image_path)
        else:
            self._load_default_sample()

    # ------------------------------------------------------------------
    # Presets Definition
    # ------------------------------------------------------------------

    def _init_presets(self):
        return {
            "Standard Confocal (Default Upgraded)": {
                "algo": "1. Seeded Hysteresis (Upgraded)",
                "bg_kernel": 31,
                "cap_percentile": 92.0,
                "smooth_sigma": 2.5,
                "seed_factor": 1.0,
                "noise_factor": 2.5,
                "min_cell_area_um2": 25.0,
                "closing_iter": 3,
                "opening_iter": 2,
                "zone_edge_erosion_px": 2,
                "enable_nucleus": True,
                "nucleus_dark_sigma": 1.0,
                "nucleus_smooth_sigma": 2.0,
                "min_nuc_frac": 0.03,
                "max_nuc_frac": 0.45,
                "nuc_compactness": 0.15,
                "nuc_centrality": 0.70,
                "enable_clusters": True,
                "mask_clusters": False,
                "cluster_sigma": 4.0,
                "cluster_dilate_px": 2,
                "poi_threshold_sigma": 5.0,
                "poi_min_intensity": 1000.0,
                "poi_min_snr": 3.0,
                "poi_min_circ": 0.4,
            },
            "Faint / Low-Light Cell (High Sensitivity)": {
                "algo": "1. Seeded Hysteresis (Upgraded)",
                "bg_kernel": 45,
                "cap_percentile": 90.0,
                "smooth_sigma": 3.0,
                "seed_factor": 0.7,
                "noise_factor": 1.8,
                "min_cell_area_um2": 15.0,
                "closing_iter": 4,
                "opening_iter": 1,
                "zone_edge_erosion_px": 2,
                "enable_nucleus": True,
                "nucleus_dark_sigma": 0.8,
                "nucleus_smooth_sigma": 2.5,
                "min_nuc_frac": 0.02,
                "max_nuc_frac": 0.50,
                "nuc_compactness": 0.10,
                "nuc_centrality": 0.65,
                "enable_clusters": True,
                "mask_clusters": False,
                "cluster_sigma": 3.5,
                "cluster_dilate_px": 2,
                "poi_threshold_sigma": 4.0,
                "poi_min_intensity": 500.0,
                "poi_min_snr": 2.5,
                "poi_min_circ": 0.35,
            },
            "Dense Clusters / High Glare Diamond": {
                "algo": "1. Seeded Hysteresis (Upgraded)",
                "bg_kernel": 25,
                "cap_percentile": 85.0,
                "smooth_sigma": 2.0,
                "seed_factor": 1.2,
                "noise_factor": 3.5,
                "min_cell_area_um2": 30.0,
                "closing_iter": 2,
                "opening_iter": 3,
                "zone_edge_erosion_px": 4,
                "enable_nucleus": True,
                "nucleus_dark_sigma": 1.2,
                "nucleus_smooth_sigma": 2.0,
                "min_nuc_frac": 0.03,
                "max_nuc_frac": 0.40,
                "nuc_compactness": 0.20,
                "nuc_centrality": 0.75,
                "enable_clusters": True,
                "mask_clusters": True,
                "cluster_sigma": 5.0,
                "cluster_dilate_px": 3,
                "poi_threshold_sigma": 6.0,
                "poi_min_intensity": 2000.0,
                "poi_min_snr": 4.0,
                "poi_min_circ": 0.5,
            },
            "Legacy Otsu Mode": {
                "algo": "2. Legacy Cell Region Processor (Otsu)",
                "bg_kernel": 31,
                "cap_percentile": 92.0,
                "smooth_sigma": 3.0,
                "seed_factor": 1.0,
                "noise_factor": 2.5,
                "min_cell_area_um2": 25.0,
                "closing_iter": 3,
                "opening_iter": 2,
                "zone_edge_erosion_px": 2,
                "enable_nucleus": True,
                "nucleus_dark_sigma": 1.0,
                "nucleus_smooth_sigma": 2.0,
                "min_nuc_frac": 0.03,
                "max_nuc_frac": 0.45,
                "nuc_compactness": 0.15,
                "nuc_centrality": 0.70,
                "enable_clusters": True,
                "mask_clusters": False,
                "cluster_sigma": 4.0,
                "cluster_dilate_px": 2,
                "poi_threshold_sigma": 5.0,
                "poi_min_intensity": 1000.0,
                "poi_min_snr": 3.0,
                "poi_min_circ": 0.4,
            },
            "Dual-Path Gated Local Adaptive": {
                "algo": "3. Dual-Path Gated Local Adaptive",
                "bg_kernel": 51,
                "cap_percentile": 92.0,
                "smooth_sigma": 1.5,
                "seed_factor": 1.0,
                "noise_factor": 2.5,
                "min_cell_area_um2": 25.0,
                "closing_iter": 2,
                "opening_iter": 1,
                "zone_edge_erosion_px": 2,
                "enable_nucleus": True,
                "nucleus_dark_sigma": 1.0,
                "nucleus_smooth_sigma": 2.0,
                "min_nuc_frac": 0.03,
                "max_nuc_frac": 0.45,
                "nuc_compactness": 0.15,
                "nuc_centrality": 0.70,
                "enable_clusters": True,
                "mask_clusters": False,
                "cluster_sigma": 4.0,
                "cluster_dilate_px": 2,
                "poi_threshold_sigma": 5.0,
                "poi_min_intensity": 1000.0,
                "poi_min_snr": 3.0,
                "poi_min_circ": 0.4,
            },
            "Distance-Transform Watershed": {
                "algo": "4. Distance-Transform Watershed",
                "bg_kernel": 35,
                "cap_percentile": 90.0,
                "smooth_sigma": 2.0,
                "seed_factor": 1.0,
                "noise_factor": 2.5,
                "min_cell_area_um2": 25.0,
                "closing_iter": 3,
                "opening_iter": 2,
                "zone_edge_erosion_px": 2,
                "enable_nucleus": True,
                "nucleus_dark_sigma": 1.0,
                "nucleus_smooth_sigma": 2.0,
                "min_nuc_frac": 0.03,
                "max_nuc_frac": 0.45,
                "nuc_compactness": 0.15,
                "nuc_centrality": 0.70,
                "enable_clusters": True,
                "mask_clusters": False,
                "cluster_sigma": 4.0,
                "cluster_dilate_px": 2,
                "poi_threshold_sigma": 5.0,
                "poi_min_intensity": 1000.0,
                "poi_min_snr": 3.0,
                "poi_min_circ": 0.4,
            }
        }

    # ------------------------------------------------------------------
    # UI Initialization with QSplitter for full responsiveness
    # ------------------------------------------------------------------

    def _init_ui(self):
        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QtWidgets.QHBoxLayout(main_widget)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # Resizable horizontal splitter
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_layout.addWidget(self.splitter)

        # Left Panel: Controls & Sliders (Scrollable)
        control_panel = self._build_control_panel()
        self.splitter.addWidget(control_panel)

        # Right Panel: Visualizer & Diagnostics
        right_panel = self._build_right_panel()
        self.splitter.addWidget(right_panel)

        # Set initial splitter proportions (520px left, rest right)
        self.splitter.setSizes([520, 980])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)

        # Status bar
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Load a confocal scan to begin algorithm tuning.")

    def _build_control_panel(self):
        scroll = QtWidgets.QScrollArea()
        scroll.setMinimumWidth(480)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # --- 1. File & Sample Selection Group ---
        file_grp = QtWidgets.QGroupBox("1. Scan Dataset & Preset")
        file_lay = QtWidgets.QVBoxLayout(file_grp)

        btn_row = QtWidgets.QHBoxLayout()
        self.load_btn = QtWidgets.QPushButton("📁 Open File (.dat / .png)")
        self.load_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        self.load_btn.clicked.connect(self.prompt_load_image)
        btn_row.addWidget(self.load_btn)

        self.load_macro_btn = QtWidgets.QPushButton("📐 Paired Macro (.dat)")
        self.load_macro_btn.setToolTip("Optional: Load wide-field macro scan for boundary intersection")
        self.load_macro_btn.clicked.connect(self.prompt_load_macro)
        btn_row.addWidget(self.load_macro_btn)
        file_lay.addLayout(btn_row)

        sample_row = QtWidgets.QHBoxLayout()
        sample_row.addWidget(QtWidgets.QLabel("Sample Scans:"))
        self.sample_combo = QtWidgets.QComboBox()
        self.sample_combo.addItem("Select sample scan...")
        self._populate_sample_scans()
        self.sample_combo.currentIndexChanged.connect(self._on_sample_selected)
        sample_row.addWidget(self.sample_combo)
        file_lay.addLayout(sample_row)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("Preset:"))
        self.preset_combo = QtWidgets.QComboBox()
        for p_name in self.presets.keys():
            self.preset_combo.addItem(p_name)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self.preset_combo)
        file_lay.addLayout(preset_row)

        algo_row = QtWidgets.QHBoxLayout()
        algo_row.addWidget(QtWidgets.QLabel("<b>Algorithm:</b>"))
        self.algo_combo = QtWidgets.QComboBox()
        self.algo_combo.addItems([
            "1. Seeded Hysteresis (Upgraded)",
            "2. Legacy Cell Region Processor (Otsu)",
            "3. Dual-Path Gated Local Adaptive",
            "4. Distance-Transform Watershed",
            "5. Macro-Constrained Micro",
        ])
        self.algo_combo.currentIndexChanged.connect(self.run_processing)
        algo_row.addWidget(self.algo_combo)
        file_lay.addLayout(algo_row)

        layout.addWidget(file_grp)

        # --- 2. Tabbed Parameters Controls (2-Row Layout, Never Truncated) ---
        self.tab_widget = QtWidgets.QTabWidget()

        # Tab A: Cell Boundary
        tab_cell = QtWidgets.QWidget()
        tab_cell_lay = QtWidgets.QVBoxLayout(tab_cell)
        self.bg_kernel_slider, self.bg_kernel_spin = self._create_control_card(
            "BG Filter Kernel (odd px)", 3, 201, 31, 2, tab_cell_lay,
            tooltip="Kernel size for substrate baseline subtraction."
        )
        self.cap_percentile_slider, self.cap_percentile_spin = self._create_control_card(
            "Log Winsorize Cap (%)", 50.0, 100.0, 92.0, 0.5, tab_cell_lay, is_float=True,
            tooltip="Percentile capping on log-fluorescence to neutralize extreme NV spikes."
        )
        self.smooth_sigma_slider, self.smooth_sigma_spin = self._create_control_card(
            "Smooth Sigma (px)", 0.1, 10.0, 2.5, 0.1, tab_cell_lay, is_float=True,
            tooltip="Gaussian smoothing sigma for noise suppression."
        )
        self.seed_factor_slider, self.seed_factor_spin = self._create_control_card(
            "Seed Threshold Multiplier", 0.2, 3.0, 1.0, 0.05, tab_cell_lay, is_float=True,
            tooltip="Multiplier for the strong seed threshold (t_otsu factor)."
        )
        self.noise_factor_slider, self.noise_factor_spin = self._create_control_card(
            "Noise Floor Multiplier", 0.5, 10.0, 2.5, 0.1, tab_cell_lay, is_float=True,
            tooltip="MAD noise-floor boundary cutoff multiplier (prevents leaking to substrate)."
        )
        self.min_cell_area_slider, self.min_cell_area_spin = self._create_control_card(
            "Min Cell Area (um^2)", 1.0, 500.0, 25.0, 1.0, tab_cell_lay, is_float=True,
            tooltip="Minimum cell area in square micrometers."
        )
        self.closing_iter_slider, self.closing_iter_spin = self._create_control_card(
            "Closing Iterations", 0, 10, 3, 1, tab_cell_lay,
            tooltip="Morphological closing to seal boundary holes."
        )
        self.opening_iter_slider, self.opening_iter_spin = self._create_control_card(
            "Opening Iterations", 0, 10, 2, 1, tab_cell_lay,
            tooltip="Morphological opening to eliminate thin detached noise bridges."
        )
        self.zone_erosion_slider, self.zone_erosion_spin = self._create_control_card(
            "Zone Boundary Erosion (px)", 0, 20, 2, 1, tab_cell_lay,
            tooltip="Erosion depth of cell boundary to eliminate false-positive edge artifacts."
        )
        tab_cell_lay.addStretch()
        self.tab_widget.addTab(tab_cell, "Cell Boundary")

        # Tab B: Nucleus Detection
        tab_nuc = QtWidgets.QWidget()
        tab_nuc_lay = QtWidgets.QVBoxLayout(tab_nuc)
        self.enable_nucleus_cb = QtWidgets.QCheckBox("Enable Nucleus Detection & Masking")
        self.enable_nucleus_cb.setChecked(True)
        self.enable_nucleus_cb.stateChanged.connect(self.run_processing)
        tab_nuc_lay.addWidget(self.enable_nucleus_cb)

        self.nuc_dark_sigma_slider, self.nuc_dark_sigma_spin = self._create_control_card(
            "Nucleus Dark Sigma (MAD)", 0.1, 5.0, 1.0, 0.1, tab_nuc_lay, is_float=True,
            tooltip="How many MAD-sigmas below cell median to classify dark nucleus void."
        )
        self.nuc_smooth_slider, self.nuc_smooth_spin = self._create_control_card(
            "Nucleus Smoothing Sigma", 0.5, 8.0, 2.0, 0.1, tab_nuc_lay, is_float=True,
            tooltip="Gaussian smoothing before dark thresholding."
        )
        self.min_nuc_frac_slider, self.min_nuc_frac_spin = self._create_control_card(
            "Min Nucleus Fraction (% of cell)", 1.0, 50.0, 3.0, 0.5, tab_nuc_lay, is_float=True
        )
        self.max_nuc_frac_slider, self.max_nuc_frac_spin = self._create_control_card(
            "Max Nucleus Fraction (% of cell)", 10.0, 80.0, 45.0, 1.0, tab_nuc_lay, is_float=True
        )
        self.nuc_compact_slider, self.nuc_compact_spin = self._create_control_card(
            "Min Compactness (4piA/P^2)", 0.05, 0.95, 0.15, 0.05, tab_nuc_lay, is_float=True
        )
        self.nuc_centrality_slider, self.nuc_centrality_spin = self._create_control_card(
            "Centrality Constraint", 0.10, 1.00, 0.70, 0.05, tab_nuc_lay, is_float=True
        )
        tab_nuc_lay.addStretch()
        self.tab_widget.addTab(tab_nuc, "Nucleus")

        # Tab C: Bright Clusters
        tab_clus = QtWidgets.QWidget()
        tab_clus_lay = QtWidgets.QVBoxLayout(tab_clus)
        self.enable_clusters_cb = QtWidgets.QCheckBox("Enable Cluster Detection")
        self.enable_clusters_cb.setChecked(True)
        self.enable_clusters_cb.stateChanged.connect(self.run_processing)
        tab_clus_lay.addWidget(self.enable_clusters_cb)

        self.mask_clusters_cb = QtWidgets.QCheckBox("Subtract Bright Clusters from Processable Zone")
        self.mask_clusters_cb.setChecked(False)
        self.mask_clusters_cb.setToolTip("If unchecked, clusters remain visible but are not subtracted from POI search.")
        self.mask_clusters_cb.stateChanged.connect(self.run_processing)
        tab_clus_lay.addWidget(self.mask_clusters_cb)

        self.clus_sigma_slider, self.clus_sigma_spin = self._create_control_card(
            "Cluster Sigma (MAD above median)", 1.0, 15.0, 4.0, 0.2, tab_clus_lay, is_float=True
        )
        self.clus_dilate_slider, self.clus_dilate_spin = self._create_control_card(
            "Cluster Dilation (px halo)", 0, 10, 2, 1, tab_clus_lay
        )
        self.clus_min_area_slider, self.clus_min_area_spin = self._create_control_card(
            "Min Cluster Area (px)", 1, 50, 4, 1, tab_clus_lay
        )
        tab_clus_lay.addStretch()
        self.tab_widget.addTab(tab_clus, "Bright Clusters")

        # Tab D: POI Candidates & Outside NV Inspection
        tab_poi = QtWidgets.QWidget()
        tab_poi_lay = QtWidgets.QVBoxLayout(tab_poi)
        self.enable_poi_cb = QtWidgets.QCheckBox("Run Live POI Detection & NV Inspection")
        self.enable_poi_cb.setChecked(True)
        self.enable_poi_cb.stateChanged.connect(self.run_processing)
        tab_poi_lay.addWidget(self.enable_poi_cb)

        self.poi_thresh_slider, self.poi_thresh_spin = self._create_control_card(
            "Spot Threshold Sigma", 1.0, 15.0, 5.0, 0.2, tab_poi_lay, is_float=True,
            tooltip="CIP spot detection sigma multiplier relative to zone noise."
        )
        self.poi_intensity_slider, self.poi_intensity_spin = self._create_control_card(
            "Min Spot Intensity (counts/s)", 0, 50000, 1000, 200, tab_poi_lay
        )
        self.poi_snr_slider, self.poi_snr_spin = self._create_control_card(
            "Min POI SNR Gate", 1.0, 10.0, 3.0, 0.2, tab_poi_lay, is_float=True
        )
        self.poi_circ_slider, self.poi_circ_spin = self._create_control_card(
            "Min Spot Circularity Gate", 0.1, 0.9, 0.4, 0.05, tab_poi_lay, is_float=True
        )
        tab_poi_lay.addStretch()
        self.tab_widget.addTab(tab_poi, "POI & NV Inspection")

        layout.addWidget(self.tab_widget)

        # --- 3. Layer Visibility Controls ---
        vis_grp = QtWidgets.QGroupBox("3. Visualization Layers")
        vis_lay = QtWidgets.QGridLayout(vis_grp)
        vis_lay.setContentsMargins(6, 6, 6, 6)

        self.show_raw_cb = QtWidgets.QCheckBox("Raw Scan")
        self.show_raw_cb.setChecked(True)
        self.show_raw_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_raw_cb, 0, 0)

        self.show_zone_cb = QtWidgets.QCheckBox("🟩 Processable Zone")
        self.show_zone_cb.setChecked(True)
        self.show_zone_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_zone_cb, 0, 1)

        self.show_nuc_cb = QtWidgets.QCheckBox("🟦 Nucleus Void")
        self.show_nuc_cb.setChecked(True)
        self.show_nuc_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_nuc_cb, 1, 0)

        self.show_clus_cb = QtWidgets.QCheckBox("🟥 Bright Clusters")
        self.show_clus_cb.setChecked(True)
        self.show_clus_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_clus_cb, 1, 1)

        self.show_contour_cb = QtWidgets.QCheckBox("🟨 Cell Boundary")
        self.show_contour_cb.setChecked(True)
        self.show_contour_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_contour_cb, 2, 0)

        self.show_margin_cb = QtWidgets.QCheckBox("🟧 Eroded Margin")
        self.show_margin_cb.setChecked(True)
        self.show_margin_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_margin_cb, 2, 1)

        self.show_inside_pois_cb = QtWidgets.QCheckBox("⭕ Inside POIs")
        self.show_inside_pois_cb.setChecked(True)
        self.show_inside_pois_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_inside_pois_cb, 3, 0)

        self.show_outside_nvs_cb = QtWidgets.QCheckBox("❌ Outside NVs")
        self.show_outside_nvs_cb.setChecked(True)
        self.show_outside_nvs_cb.stateChanged.connect(self._update_display)
        vis_lay.addWidget(self.show_outside_nvs_cb, 3, 1)

        layout.addWidget(vis_grp)

        # --- 4. Export & Actions ---
        action_grp = QtWidgets.QGroupBox("4. Export & Benchmarking")
        action_lay = QtWidgets.QVBoxLayout(action_grp)

        btn_copy_cfg = QtWidgets.QPushButton("📋 Copy Tuned Settings (Python / YAML)")
        btn_copy_cfg.clicked.connect(self.copy_config_to_clipboard)
        action_lay.addWidget(btn_copy_cfg)

        btn_export_mask = QtWidgets.QPushButton("💾 Export Filtered Mask & PNG")
        btn_export_mask.clicked.connect(self.export_mask_and_image)
        action_lay.addWidget(btn_export_mask)

        layout.addWidget(action_grp)
        layout.addStretch()

        scroll.setWidget(panel)
        return scroll

    def _build_right_panel(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Top Bar: View Mode, Colormap, Split View
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.addWidget(QtWidgets.QLabel("<b>View Mode:</b>"))
        self.view_mode_combo = QtWidgets.QComboBox()
        self.view_mode_combo.addItems(["Single Unified View", "Side-by-Side (Raw vs Mask)"])
        self.view_mode_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        top_bar.addWidget(self.view_mode_combo)

        top_bar.addSpacing(15)
        top_bar.addWidget(QtWidgets.QLabel("<b>Colormap:</b>"))
        self.cmap_combo = QtWidgets.QComboBox()
        self.cmap_combo.addItems(["inferno", "viridis", "plasma", "magma", "gray"])
        self.cmap_combo.currentIndexChanged.connect(self._on_colormap_changed)
        top_bar.addWidget(self.cmap_combo)

        top_bar.addStretch()
        self.auto_levels_btn = QtWidgets.QPushButton("Auto Contrast")
        self.auto_levels_btn.clicked.connect(self._auto_contrast)
        top_bar.addWidget(self.auto_levels_btn)

        layout.addLayout(top_bar)

        # Graphics Layout (pyqtgraph)
        self.gl_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.gl_widget, stretch=1)

        # Main ViewBox
        self.plot_main = self.gl_widget.addPlot(row=0, col=0, title="Confocal Scan & Boundary Segmentation")
        self.plot_main.setAspectLocked(True)
        self.plot_main.invertY(False)
        self.plot_main.showGrid(x=True, y=True, alpha=0.3)
        self.plot_main.setLabel('bottom', "X Position", units='µm')
        self.plot_main.setLabel('left', "Y Position", units='µm')

        self.img_item_raw = pg.ImageItem()
        self.plot_main.addItem(self.img_item_raw)

        self.img_item_overlay = pg.ImageItem()
        self.plot_main.addItem(self.img_item_overlay)

        # Secondary ViewBox (for Side-by-Side)
        self.plot_side = self.gl_widget.addPlot(row=0, col=1, title="Raw Confocal Image")
        self.plot_side.setAspectLocked(True)
        self.plot_side.invertY(False)
        self.plot_side.showGrid(x=True, y=True, alpha=0.3)
        self.plot_side.setLabel('bottom', "X Position", units='µm')
        self.plot_side.setLabel('left', "Y Position", units='µm')
        self.plot_side.setXLink(self.plot_main)
        self.plot_side.setYLink(self.plot_main)

        self.img_item_side = pg.ImageItem()
        self.plot_side.addItem(self.img_item_side)
        self.plot_side.hide()

        # Histogram LUT for contrast adjustment
        self.hist_lut = pg.HistogramLUTItem()
        self.hist_lut.setImageItem(self.img_item_raw)
        self.gl_widget.addItem(self.hist_lut, row=0, col=2)

        # Connect mouse move event for hover coordinates & intensity
        self.plot_main.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # Bottom Panel: Diagnostics & Warning Dashboard
        diag_box = QtWidgets.QGroupBox("Live Metrics & False-Positive Boundary Risk")
        diag_lay = QtWidgets.QGridLayout(diag_box)
        diag_lay.setContentsMargins(6, 6, 6, 6)

        self.lbl_cell_area = QtWidgets.QLabel("Cell Area: <b>--</b>")
        self.lbl_proc_area = QtWidgets.QLabel("Processable Area: <b>--</b>")
        self.lbl_nuc_area = QtWidgets.QLabel("Nucleus Void: <b>--</b>")
        self.lbl_clus_count = QtWidgets.QLabel("Bright Clusters: <b>--</b>")
        self.lbl_inside_nv = QtWidgets.QLabel("Inside Zone POIs: <b>--</b>")
        self.lbl_outside_nv = QtWidgets.QLabel("Outside Substrate NVs: <b>--</b>")
        self.lbl_risk_score = QtWidgets.QLabel("Boundary Risk: <b>--</b>")
        self.lbl_timing = QtWidgets.QLabel("Compute Time: <b>-- ms</b>")

        diag_lay.addWidget(self.lbl_cell_area, 0, 0)
        diag_lay.addWidget(self.lbl_proc_area, 0, 1)
        diag_lay.addWidget(self.lbl_nuc_area, 0, 2)
        diag_lay.addWidget(self.lbl_clus_count, 0, 3)
        diag_lay.addWidget(self.lbl_inside_nv, 1, 0)
        diag_lay.addWidget(self.lbl_outside_nv, 1, 1)
        diag_lay.addWidget(self.lbl_risk_score, 1, 2)
        diag_lay.addWidget(self.lbl_timing, 1, 3)

        layout.addWidget(diag_box)
        return panel

    def _create_control_card(self, label_text, min_v, max_v, default_v, step, parent_layout, is_float=False, tooltip=None):
        """
        Creates a clean 2-row control widget:
        Row 1: [ Parameter Label ]            [ Value SpinBox ]
        Row 2: [ ==================●========================= ]
        Guarantees numbers are never cut off or squished.
        """
        card = QtWidgets.QWidget()
        card_lay = QtWidgets.QVBoxLayout(card)
        card_lay.setContentsMargins(2, 2, 2, 2)
        card_lay.setSpacing(2)

        header_row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(label_text)
        lbl.setStyleSheet("font-weight: 500;")
        if tooltip:
            lbl.setToolTip(tooltip)
        header_row.addWidget(lbl, stretch=1)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)

        if is_float:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(float(min_v), float(max_v))
            spin.setSingleStep(float(step))
            spin.setValue(float(default_v))
            spin.setDecimals(2 if step < 0.1 else 1)
            spin.setMinimumWidth(75)

            scale = 100.0 if step < 0.1 else 10.0
            slider.setRange(int(min_v * scale), int(max_v * scale))
            slider.setValue(int(default_v * scale))

            def on_slider(val):
                spin.blockSignals(True)
                spin.setValue(val / scale)
                spin.blockSignals(False)
                self.run_processing()

            def on_spin(val):
                slider.blockSignals(True)
                slider.setValue(int(val * scale))
                slider.blockSignals(False)
                self.run_processing()

            slider.valueChanged.connect(on_slider)
            spin.valueChanged.connect(on_spin)
        else:
            spin = QtWidgets.QSpinBox()
            spin.setRange(int(min_v), int(max_v))
            spin.setSingleStep(int(step))
            spin.setValue(int(default_v))
            spin.setMinimumWidth(75)

            slider.setRange(int(min_v), int(max_v))
            slider.setValue(int(default_v))

            def on_slider(val):
                if "Kernel" in label_text and val % 2 == 0:
                    val += 1
                spin.blockSignals(True)
                spin.setValue(val)
                spin.blockSignals(False)
                self.run_processing()

            def on_spin(val):
                if "Kernel" in label_text and val % 2 == 0:
                    val += 1
                    spin.setValue(val)
                slider.blockSignals(True)
                slider.setValue(val)
                slider.blockSignals(False)
                self.run_processing()

            slider.valueChanged.connect(on_slider)
            spin.valueChanged.connect(on_spin)

        header_row.addWidget(spin, stretch=0)
        card_lay.addLayout(header_row)
        card_lay.addWidget(slider)

        parent_layout.addWidget(card)
        return slider, spin

    # ------------------------------------------------------------------
    # Sample Scans Discovery
    # ------------------------------------------------------------------

    def _populate_sample_scans(self):
        scan_dirs = ['Confocal', 'Confocal2', 'Confocal3', 'Confocal4']
        found = []
        for d in scan_dirs:
            full_d = os.path.join(PROJECT_ROOT, d)
            if os.path.exists(full_d):
                for f in os.listdir(full_d):
                    if f.endswith('_confocal_xy_data.dat') or f.endswith('_image_Dev1Ctr3.dat') or f.endswith('.png'):
                        if not f.endswith('_fig.png'):
                            found.append((f"{d}/{f}", os.path.join(full_d, f)))

        for label_text, path in sorted(found):
            self.sample_combo.addItem(label_text, path)

    def _on_sample_selected(self, idx):
        if idx > 0:
            path = self.sample_combo.itemData(idx)
            if path and os.path.exists(path):
                self.load_image(path)

    def _load_default_sample(self):
        default_candidates = [
            os.path.join(PROJECT_ROOT, 'Confocal2', '20260706-1701-46_confocal_xy_data.dat'),
            os.path.join(PROJECT_ROOT, 'Confocal3', '20260805-0001-21_confocal_xy_data.dat'),
            os.path.join(PROJECT_ROOT, 'Confocal4', '20260812-1610-05_confocal_xy_scan_raw_pixel_image_raw.png'),
        ]
        for c in default_candidates:
            if os.path.exists(c):
                self.load_image(c)
                break

    # ------------------------------------------------------------------
    # Image Loading & Coordinate Parsing
    # ------------------------------------------------------------------

    def prompt_load_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Confocal Scan", PROJECT_ROOT, "Confocal Scans (*.dat *.png)"
        )
        if path:
            self.load_image(path)

    def prompt_load_macro(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Paired Macro Scan", PROJECT_ROOT, "Confocal Scans (*.dat *.png)"
        )
        if path:
            self.load_macro_image(path)

    def load_image(self, path):
        """Load scan data from .dat or .png using unified multi-format parser."""
        self.current_filepath = path
        try:
            self.image, self.x_coords, self.y_coords, _ = parse_any_confocal_file(path)
            self.fluor = self.image[:, :, 3].astype(float)

            if self.fluor is None:
                raise ValueError("Could not extract fluorescence data.")

            ny, nx = self.fluor.shape
            if len(self.x_coords) > 1 and len(self.y_coords) > 1:
                dx = abs(self.x_coords[-1] - self.x_coords[0]) / max(nx - 1, 1)
                self.pixel_size_um = float(dx * 1e6)
            else:
                self.pixel_size_um = 0.33

            data_t = self.fluor.T
            self.img_item_raw.setImage(data_t, autoLevels=False)
            self.img_item_side.setImage(data_t, autoLevels=False)

            x_min_um = float(self.x_coords[0] * 1e6)
            x_max_um = float(self.x_coords[-1] * 1e6)
            y_min_um = float(self.y_coords[0] * 1e6)
            y_max_um = float(self.y_coords[-1] * 1e6)
            w_um = x_max_um - x_min_um
            h_um = y_max_um - y_min_um

            rect = QtCore.QRectF(x_min_um, y_min_um, w_um, h_um)
            self.img_item_raw.setRect(rect)
            self.img_item_side.setRect(rect)
            self.img_item_overlay.setRect(rect)

            self._auto_contrast()
            self._on_colormap_changed()

            self.status_bar.showMessage(f"Loaded {os.path.basename(path)} ({nx}x{ny} px, {w_um:.1f}x{h_um:.1f} µm)")
            self.run_processing()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load Error", f"Failed to load image:\n{e}")

    def load_macro_image(self, path):
        """Load paired macro scan for boundary intersection."""
        try:
            self.macro_image, self.macro_x, self.macro_y, _ = parse_any_confocal_file(path)
            res = self.roi_logic.segment_roi(self.macro_image)
            self.macro_mask = res.get('roi_mask')
            self.status_bar.showMessage(f"Loaded Paired Macro: {os.path.basename(path)}")
            self.run_processing()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Macro Load Error", f"Could not load macro scan: {e}")

    # ------------------------------------------------------------------
    # Preset Application
    # ------------------------------------------------------------------

    def _on_preset_selected(self):
        p_name = self.preset_combo.currentText()
        if p_name in self.presets:
            p = self.presets[p_name]
            algo_idx = self.algo_combo.findText(p["algo"])
            if algo_idx >= 0:
                self.algo_combo.setCurrentIndex(algo_idx)

            self.bg_kernel_spin.setValue(p["bg_kernel"])
            self.cap_percentile_spin.setValue(p["cap_percentile"])
            self.smooth_sigma_spin.setValue(p["smooth_sigma"])
            self.seed_factor_spin.setValue(p["seed_factor"])
            self.noise_factor_spin.setValue(p["noise_factor"])
            self.min_cell_area_spin.setValue(p["min_cell_area_um2"])
            self.closing_iter_spin.setValue(p["closing_iter"])
            self.opening_iter_spin.setValue(p["opening_iter"])
            self.zone_erosion_spin.setValue(p["zone_edge_erosion_px"])

            self.enable_nucleus_cb.setChecked(p["enable_nucleus"])
            self.nuc_dark_sigma_spin.setValue(p["nucleus_dark_sigma"])
            self.nuc_smooth_spin.setValue(p["nucleus_smooth_sigma"])
            self.min_nuc_frac_spin.setValue(p["min_nuc_frac"] * 100)
            self.max_nuc_frac_spin.setValue(p["max_nuc_frac"] * 100)
            self.nuc_compact_spin.setValue(p["nuc_compactness"])
            self.nuc_centrality_spin.setValue(p["nuc_centrality"])

            self.enable_clusters_cb.setChecked(p["enable_clusters"])
            self.mask_clusters_cb.setChecked(p["mask_clusters"])
            self.clus_sigma_spin.setValue(p["cluster_sigma"])
            self.clus_dilate_spin.setValue(p["cluster_dilate_px"])

            self.poi_thresh_spin.setValue(p["poi_threshold_sigma"])
            self.poi_intensity_spin.setValue(int(p["poi_min_intensity"]))
            self.poi_snr_spin.setValue(p["poi_min_snr"])
            self.poi_circ_spin.setValue(p["poi_min_circ"])

            self.run_processing()

    # ------------------------------------------------------------------
    # Core Processing Pipeline Execution
    # ------------------------------------------------------------------

    def run_processing(self):
        """Execute selected algorithm with current slider values."""
        if self.image is None or self.fluor is None:
            return

        t0 = time.time()
        algo = self.algo_combo.currentText()

        bg_k = int(self.bg_kernel_spin.value())
        cap_p = float(self.cap_percentile_spin.value())
        sigma = float(self.smooth_sigma_spin.value())
        seed_f = float(self.seed_factor_spin.value())
        noise_f = float(self.noise_factor_spin.value())
        min_area_um2 = float(self.min_cell_area_spin.value())
        close_iter = int(self.closing_iter_spin.value())
        open_iter = int(self.opening_iter_spin.value())
        erosion_px = int(self.zone_erosion_spin.value())

        enable_nuc = self.enable_nucleus_cb.isChecked()
        nuc_dark_s = float(self.nuc_dark_sigma_spin.value())
        nuc_smooth_s = float(self.nuc_smooth_spin.value())
        min_nuc_f = float(self.min_nuc_frac_spin.value()) / 100.0
        max_nuc_f = float(self.max_nuc_frac_spin.value()) / 100.0
        nuc_compact = float(self.nuc_compact_spin.value())
        nuc_central = float(self.nuc_centrality_spin.value())

        enable_clus = self.enable_clusters_cb.isChecked()
        mask_clus = self.mask_clusters_cb.isChecked()
        clus_s = float(self.clus_sigma_spin.value())
        clus_dilate = int(self.clus_dilate_spin.value())
        clus_min_area = int(self.clus_min_area_spin.value())

        enable_poi = self.enable_poi_cb.isChecked()
        poi_thresh_s = float(self.poi_thresh_spin.value())
        poi_min_int = float(self.poi_intensity_spin.value())
        poi_min_snr = float(self.poi_snr_spin.value())
        poi_min_circ = float(self.poi_circ_spin.value())

        ny, nx = self.fluor.shape
        pixel_area_um2 = max(self.pixel_size_um ** 2, 1e-6)
        min_area_px = max(10, int(min_area_um2 / pixel_area_um2))

        cell_mask = None
        eroded_margin_mask = np.zeros((ny, nx), dtype=bool)

        try:
            if "1. Seeded Hysteresis" in algo:
                cell_mask = self._algo_seeded_hysteresis(
                    self.fluor, bg_k, cap_p, sigma, seed_f, noise_f,
                    min_area_px, close_iter, open_iter
                )
            elif "2. Legacy Cell Region Processor" in algo:
                cell_mask = self.legacy_processor._detect_cell_interior(
                    self.fluor, bg_k, sigma, 'otsu', min_area_fraction=0.05
                )
            elif "3. Dual-Path Gated Local Adaptive" in algo:
                cell_mask = self._algo_gated_local_adaptive(
                    self.fluor, bg_k, cap_p, sigma, min_area_px, close_iter, open_iter
                )
            elif "4. Distance-Transform Watershed" in algo:
                cell_mask = self._algo_distance_watershed(
                    self.fluor, bg_k, cap_p, sigma, min_area_px
                )
            elif "5. Macro-Constrained Micro" in algo:
                micro_m = self._algo_seeded_hysteresis(
                    self.fluor, bg_k, cap_p, sigma, seed_f, noise_f,
                    min_area_px, close_iter, open_iter
                )
                cell_mask = self._apply_macro_constraint(micro_m)

            if cell_mask is None:
                cell_mask = np.zeros((ny, nx), dtype=bool)

            if erosion_px > 0 and cell_mask.any():
                eroded_inner = binary_erosion(cell_mask, iterations=erosion_px)
                eroded_margin_mask = cell_mask & ~eroded_inner
            else:
                eroded_inner = cell_mask

            if enable_nuc and cell_mask.any():
                nucleus_mask, nuc_stats = self.upgraded_processor._detect_nucleus(
                    self.fluor, cell_mask,
                    dark_sigma=nuc_dark_s, smooth_sigma=nuc_smooth_s,
                    min_fraction=min_nuc_f, max_fraction=max_nuc_f,
                    min_compactness=nuc_compact, centrality=nuc_central
                )
            else:
                nucleus_mask = np.zeros((ny, nx), dtype=bool)
                nuc_stats = {'detected': False}

            if enable_clus and cell_mask.any():
                bright_mask, clus_stats = self.upgraded_processor._detect_bright_clusters(
                    self.fluor, cell_mask,
                    cluster_sigma=clus_s, dilate_px=clus_dilate,
                    min_area_px=clus_min_area, max_fraction=0.40
                )
            else:
                bright_mask = np.zeros((ny, nx), dtype=bool)
                clus_stats = []

            processable_mask = self.upgraded_processor._extract_processable_zone(
                cell_mask, nucleus_mask, bright_mask,
                edge_erosion_px=erosion_px, min_area_px=min_area_px // 2,
                mask_bright_clusters=mask_clus
            )

            result = CellProcessingResult((ny, nx))
            result.cell_interior_mask = cell_mask
            result.nucleus_mask = nucleus_mask
            result.bright_cluster_mask = bright_mask
            result.processable_mask = processable_mask
            result.nucleus_stats = nuc_stats
            result.bright_cluster_stats = clus_stats

            if processable_mask.any():
                zone_fluor = self.fluor[processable_mask]
                result.zone_stats = {
                    'area_px': int(processable_mask.sum()),
                    'mean_intensity': float(zone_fluor.mean()),
                    'median_intensity': float(np.median(zone_fluor)),
                    'std_intensity': float(zone_fluor.std()),
                    'processable': True
                }
            else:
                result.zone_stats = {'area_px': 0, 'processable': False}

            self.current_result = result
            self.eroded_margin_mask = eroded_margin_mask

            self.detected_candidates = []
            self.inside_candidates = []
            self.outside_candidates = []

            if enable_poi:
                self._run_poi_inspection(
                    result, poi_thresh_s, poi_min_int, poi_min_snr, poi_min_circ
                )

            elapsed_ms = (time.time() - t0) * 1000.0

            self._update_metrics(result, elapsed_ms)
            self._update_display()

        except Exception as e:
            self.status_bar.showMessage(f"Processing error: {e}")

    # ------------------------------------------------------------------
    # Custom Algorithm Implementations
    # ------------------------------------------------------------------

    def _algo_seeded_hysteresis(self, fluor, bg_kernel, cap_percentile,
                                smooth_sigma, seed_factor, noise_factor,
                                min_area_px, close_iter, open_iter):
        """Seeded Hysteresis bounded by MAD noise floor."""
        ny, nx = fluor.shape
        kernel = bg_kernel if bg_kernel % 2 == 1 else bg_kernel + 1

        bg = median_filter(fluor, size=kernel)
        subtracted = np.maximum(fluor - bg, 0.0)

        log_fluor = np.log1p(np.maximum(fluor, 0.0))
        bg_log = np.log1p(np.maximum(bg, 0.0))
        p_cap = np.percentile(log_fluor, cap_percentile)
        clipped_log = np.clip(log_fluor, a_min=None, a_max=p_cap)

        raw_diff = clipped_log - bg_log
        mad_bg = np.median(np.abs(raw_diff - np.median(raw_diff)))
        noise_sigma = max(1.4826 * mad_bg, 0.01)

        despiked = median_filter(subtracted, size=5)
        smoothed = gaussian_filter(despiked, sigma=smooth_sigma)

        nonzero = smoothed[smoothed > 0]
        if len(nonzero) > 20:
            if HAS_SKIMAGE:
                try:
                    t_otsu = threshold_otsu(nonzero)
                except Exception:
                    t_otsu = np.percentile(nonzero, 50)
            else:
                t_otsu = np.percentile(nonzero, 50)
            t_otsu_scaled = t_otsu * seed_factor
            t_adaptive = max(0.4 * t_otsu_scaled, noise_factor * noise_sigma)
        else:
            t_otsu_scaled = 0.0
            t_adaptive = 0.0

        seed_mask = smoothed > t_otsu_scaled
        expand_mask = smoothed > t_adaptive

        if seed_mask.any():
            mask = binary_propagation(seed_mask, mask=expand_mask)
        else:
            mask = expand_mask

        if close_iter > 0:
            mask = binary_closing(mask, iterations=close_iter)
        mask = binary_fill_holes(mask)
        if open_iter > 0:
            mask = binary_opening(mask, iterations=open_iter)

        labeled, n = label(mask)
        if n == 0:
            return np.zeros((ny, nx), dtype=bool)

        slices = find_objects(labeled)
        best_lbl = 0
        best_area = 0
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            area = int((labeled[sl] == lbl).sum())
            if area >= min_area_px and area > best_area:
                best_area = area
                best_lbl = lbl

        return (labeled == best_lbl) if best_lbl > 0 else (mask if mask.sum() >= min_area_px else np.zeros((ny, nx), dtype=bool))

    def _algo_gated_local_adaptive(self, fluor, bg_kernel, cap_percentile,
                                   smooth_sigma, min_area_px, close_iter, open_iter):
        """Dual-path gated local adaptive thresholding."""
        ny, nx = fluor.shape
        log_f = np.log10(np.maximum(fluor, 0.0) + 1.0)
        p_cap = np.percentile(log_f, cap_percentile)
        clipped = np.minimum(log_f, p_cap)

        bg_k = bg_kernel if bg_kernel % 2 != 0 else bg_kernel + 1
        bg_floor = grey_opening(clipped, size=(bg_k, bg_k))
        subtracted = np.maximum(clipped - bg_floor, 0.0)
        smoothed = gaussian_filter(subtracted, sigma=smooth_sigma)

        if HAS_SKIMAGE and smoothed.any():
            nonzero = smoothed[smoothed > 0]
            try:
                t_global = threshold_otsu(nonzero) if len(nonzero) > 20 else 0.0
            except Exception:
                t_global = np.percentile(smoothed, 50)

            gate = max(0.3 * t_global, np.percentile(smoothed, 30) + 0.02)
            global_gate = smoothed > gate

            local_thresh = threshold_local(smoothed, block_size=bg_k, method='gaussian', offset=0.01)
            binary_mask = (smoothed > local_thresh) & global_gate
        else:
            binary_mask = smoothed > np.percentile(smoothed, 40)

        if close_iter > 0:
            binary_mask = binary_closing(binary_mask, iterations=close_iter)
        binary_mask = binary_fill_holes(binary_mask)
        if open_iter > 0:
            binary_mask = binary_opening(binary_mask, iterations=open_iter)

        labeled, n = label(binary_mask)
        slices = find_objects(labeled)
        best_lbl = 0
        best_area = 0
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            area = int((labeled[sl] == lbl).sum())
            if area >= min_area_px and area > best_area:
                best_area = area
                best_lbl = lbl

        return (labeled == best_lbl) if best_lbl > 0 else np.zeros((ny, nx), dtype=bool)

    def _algo_distance_watershed(self, fluor, bg_kernel, cap_percentile, smooth_sigma, min_area_px):
        """Morphological Top-Hat + Distance Transform Watershed."""
        ny, nx = fluor.shape
        base_mask = self._algo_gated_local_adaptive(
            fluor, bg_kernel, cap_percentile, smooth_sigma, min_area_px, close_iter=2, open_iter=1
        )
        if not HAS_SKIMAGE or not base_mask.any():
            return base_mask

        distance = distance_transform_edt(base_mask)
        coords = peak_local_max(distance, min_distance=10, labels=base_mask)

        if len(coords) > 0:
            peaks = np.zeros_like(base_mask, dtype=bool)
            peaks[tuple(coords.T)] = True
            markers, _ = label(peaks)
            labels = watershed(-distance, markers, mask=base_mask)
            return labels > 0
        return base_mask

    def _apply_macro_constraint(self, micro_mask):
        """Intersect micro mask with interpolated macro mask."""
        if self.macro_mask is None or self.macro_x is None or self.macro_y is None:
            return micro_mask
        try:
            from scipy.interpolate import RegularGridInterpolator
            mx = self.macro_x
            my = self.macro_y
            m_mask = self.macro_mask.astype(float)

            if mx[0] > mx[-1]:
                mx = mx[::-1]
                m_mask = m_mask[:, ::-1]
            if my[0] > my[-1]:
                my = my[::-1]
                m_mask = m_mask[::-1, :]

            interp = RegularGridInterpolator((my, mx), m_mask, bounds_error=False, fill_value=0.0)
            YY, XX = np.meshgrid(self.y_coords, self.x_coords, indexing='ij')
            points = np.stack((YY, XX), axis=-1)
            interp_mask = interp(points) > 0.5
            return micro_mask & interp_mask
        except Exception as e:
            print(f"Macro constraint error: {e}")
            return micro_mask

    # ------------------------------------------------------------------
    # Live POI Extraction & Outside NV Detection
    # ------------------------------------------------------------------

    def _run_poi_inspection(self, cell_res, threshold_sigma, min_intensity, min_snr, min_circ):
        """Run POIExtractor and classify spots into inside-zone vs outside-substrate NVs."""
        cfg_override = {
            'detection_threshold_sigma': threshold_sigma,
            'min_spot_intensity': min_intensity,
            'min_snr': min_snr,
            'min_circularity': min_circ,
        }

        # 1. Extract candidates in the processable zone
        poi_res = self.poi_extractor.extract(
            cell_result=cell_res,
            image=self.image,
            x_coords=self.x_coords,
            y_coords=self.y_coords,
            **cfg_override
        )
        self.inside_candidates = poi_res.strong_candidates + poi_res.marginal_candidates

        # 2. Extract spots in the outside substrate region to detect outside NVs
        ny, nx = self.fluor.shape
        substrate_mask = ~cell_res.cell_interior_mask
        if substrate_mask.any():
            sub_res = CellProcessingResult((ny, nx))
            sub_res.processable_mask = substrate_mask
            sub_res.zone_stats = {
                'area_px': int(substrate_mask.sum()),
                'median_intensity': float(np.median(self.fluor[substrate_mask])),
                'std_intensity': float(np.std(self.fluor[substrate_mask])),
                'processable': True
            }
            outside_poi_res = self.poi_extractor.extract(
                cell_result=sub_res,
                image=self.image,
                x_coords=self.x_coords,
                y_coords=self.y_coords,
                **cfg_override
            )
            self.outside_candidates = outside_poi_res.strong_candidates + outside_poi_res.marginal_candidates

    # ------------------------------------------------------------------
    # Visualization & Display Updates
    # ------------------------------------------------------------------

    def _update_display(self):
        """Update multi-layer overlay and contour graphics."""
        if self.fluor is None or self.current_result is None:
            return

        ny, nx = self.fluor.shape
        res = self.current_result

        overlay = np.zeros((nx, ny, 4), dtype=np.uint8)

        # 🟩 Processable Zone: Green translucent
        if self.show_zone_cb.isChecked() and res.processable_mask.any():
            mask_t = res.processable_mask.T
            overlay[mask_t, 0] = 0
            overlay[mask_t, 1] = 220
            overlay[mask_t, 2] = 50
            overlay[mask_t, 3] = 90

        # 🟦 Nucleus Void: Blue translucent
        if self.show_nuc_cb.isChecked() and res.nucleus_mask.any():
            mask_t = res.nucleus_mask.T
            overlay[mask_t, 0] = 30
            overlay[mask_t, 1] = 120
            overlay[mask_t, 2] = 255
            overlay[mask_t, 3] = 130

        # 🟥 Bright Clusters: Red translucent
        if self.show_clus_cb.isChecked() and res.bright_cluster_mask.any():
            mask_t = res.bright_cluster_mask.T
            overlay[mask_t, 0] = 255
            overlay[mask_t, 1] = 40
            overlay[mask_t, 2] = 40
            overlay[mask_t, 3] = 140

        # 🟧 Eroded Margin Band: Orange translucent
        if self.show_margin_cb.isChecked() and hasattr(self, 'eroded_margin_mask') and self.eroded_margin_mask.any():
            mask_t = self.eroded_margin_mask.T
            overlay[mask_t, 0] = 255
            overlay[mask_t, 1] = 165
            overlay[mask_t, 2] = 0
            overlay[mask_t, 3] = 80

        self.img_item_overlay.setImage(overlay)
        self.img_item_raw.setVisible(self.show_raw_cb.isChecked())

        for item in self.contour_items:
            self.plot_main.removeItem(item)
        self.contour_items.clear()

        for item in self.candidate_items:
            self.plot_main.removeItem(item)
        self.candidate_items.clear()

        # Draw Cell Boundary Contours (Yellow Line)
        if self.show_contour_cb.isChecked() and res.cell_interior_mask.any():
            self._draw_contours(res.cell_interior_mask, pen_color='#FFFF00', width=2)

        # Draw Nucleus Contours (Cyan Line)
        if self.show_nuc_cb.isChecked() and res.nucleus_mask.any():
            self._draw_contours(res.nucleus_mask, pen_color='#00FFFF', width=1.5)

        # Draw Processable Zone Contours (Bright Green Line)
        if self.show_zone_cb.isChecked() and res.processable_mask.any():
            self._draw_contours(res.processable_mask, pen_color='#00FF00', width=1.5)

        # Draw Inside POI Candidate Markers (Green/Cyan Circles)
        if self.show_inside_pois_cb.isChecked() and self.inside_candidates:
            self._draw_candidate_markers(self.inside_candidates, is_inside=True)

        # Draw Outside Substrate NV Markers (Red/Orange Crosses)
        if self.show_outside_nvs_cb.isChecked() and self.outside_candidates:
            self._draw_candidate_markers(self.outside_candidates, is_inside=False)

    def _draw_contours(self, mask, pen_color='#FFFF00', width=2):
        """Extract and draw smooth contour curves over the image."""
        if not HAS_SKIMAGE:
            return
        contours = find_contours(mask.astype(float), 0.5)
        for contour in contours:
            r = contour[:, 0]
            c = contour[:, 1]
            x_um = (self.x_coords[0] + c * (self.x_coords[-1] - self.x_coords[0]) / max(len(self.x_coords) - 1, 1)) * 1e6
            y_um = (self.y_coords[0] + r * (self.y_coords[-1] - self.y_coords[0]) / max(len(self.y_coords) - 1, 1)) * 1e6

            curve = pg.PlotCurveItem(x=x_um, y=y_um, pen=pg.mkPen(color=pen_color, width=width))
            self.plot_main.addItem(curve)
            self.contour_items.append(curve)

    def _draw_candidate_markers(self, candidates, is_inside=True):
        """Draw interactive scatter markers for detected NV candidates."""
        x_pts = []
        y_pts = []
        for cand in candidates:
            x_pts.append(cand.x * 1e6)
            y_pts.append(cand.y * 1e6)

        if not x_pts:
            return

        if is_inside:
            scatter = pg.ScatterPlotItem(
                x=x_pts, y=y_pts, size=12,
                pen=pg.mkPen('#00FF00', width=2),
                brush=pg.mkBrush(0, 255, 0, 100),
                symbol='o'
            )
        else:
            scatter = pg.ScatterPlotItem(
                x=x_pts, y=y_pts, size=14,
                pen=pg.mkPen('#FF0000', width=2),
                brush=pg.mkBrush(255, 0, 0, 120),
                symbol='x'
            )

        self.plot_main.addItem(scatter)
        self.candidate_items.append(scatter)

    def _update_metrics(self, res, elapsed_ms):
        """Update live metrics dashboard and false-positive boundary risk analysis."""
        ny, nx = self.fluor.shape
        pixel_area_um2 = max(self.pixel_size_um ** 2, 1e-6)
        fov_area_um2 = max((nx * self.pixel_size_um) * (ny * self.pixel_size_um), 1e-6)

        cell_px = int(res.cell_interior_mask.sum())
        cell_um2 = cell_px * pixel_area_um2
        cell_pct = (cell_um2 / fov_area_um2) * 100.0

        proc_px = int(res.processable_mask.sum())
        proc_um2 = proc_px * pixel_area_um2
        proc_pct = (proc_px / max(cell_px, 1)) * 100.0

        nuc_px = int(res.nucleus_mask.sum())
        nuc_um2 = nuc_px * pixel_area_um2
        nuc_pct = (nuc_px / max(cell_px, 1)) * 100.0

        n_clus = len(res.bright_cluster_stats)
        n_inside = len(self.inside_candidates)
        n_outside = len(self.outside_candidates)

        risk_level = "LOW (Clean Boundary)"
        risk_color = "#00AA00"

        if cell_px > 0 and self.outside_candidates:
            dist_map = distance_transform_edt(~res.cell_interior_mask)
            near_boundary_count = 0
            for cand in self.outside_candidates:
                r = min(max(int(cand.pixel_row), 0), ny - 1)
                c = min(max(int(cand.pixel_col), 0), nx - 1)
                if dist_map[r, c] <= 3.0:
                    near_boundary_count += 1

            if near_boundary_count > 0:
                risk_level = f"MEDIUM ({near_boundary_count} Outside NV near edge)"
                risk_color = "#FFA500"
            if cell_pct > 85.0:
                risk_level = "HIGH (Substrate Over-Segmentation)"
                risk_color = "#FF0000"

        self.lbl_cell_area.setText(f"Cell Area: <b>{cell_um2:.1f} µm²</b> ({cell_pct:.1f}% FOV)")
        self.lbl_proc_area.setText(f"Processable: <b>{proc_um2:.1f} µm²</b> ({proc_pct:.1f}% Cell)")
        self.lbl_nuc_area.setText(f"Nucleus Void: <b>{nuc_um2:.1f} µm²</b> ({nuc_pct:.1f}% Cell)")
        self.lbl_clus_count.setText(f"Bright Clusters: <b>{n_clus} detected</b>")
        self.lbl_inside_nv.setText(f"Inside Zone POIs: <b>{n_inside} spots</b>")
        self.lbl_outside_nv.setText(f"Outside Substrate NVs: <b>{n_outside} spots</b>")
        self.lbl_risk_score.setText(f"Boundary Risk: <span style='color:{risk_color}; font-weight:bold;'>{risk_level}</span>")
        self.lbl_timing.setText(f"Compute Time: <b>{elapsed_ms:.1f} ms</b>")

    # ------------------------------------------------------------------
    # UI Interactions & Event Handlers
    # ------------------------------------------------------------------

    def _on_view_mode_changed(self):
        mode = self.view_mode_combo.currentText()
        if "Side-by-Side" in mode:
            self.plot_side.show()
        else:
            self.plot_side.hide()

    def _on_colormap_changed(self):
        cmap_name = self.cmap_combo.currentText()
        lut = get_lut(cmap_name)
        self.img_item_raw.setLookupTable(lut)
        self.img_item_side.setLookupTable(lut)

    def _auto_contrast(self):
        if self.fluor is not None:
            p2, p99 = np.percentile(self.fluor, (2, 99))
            self.img_item_raw.setLevels((p2, p99))
            self.img_item_side.setLevels((p2, p99))
            self.hist_lut.setLevels(p2, p99)

    def _on_mouse_moved(self, evt):
        if self.fluor is None:
            return
        pos = evt
        if self.plot_main.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_main.vb.mapSceneToView(pos)
            x_um = mouse_point.x()
            y_um = mouse_point.y()

            x_min_um = self.x_coords[0] * 1e6
            x_max_um = self.x_coords[-1] * 1e6
            y_min_um = self.y_coords[0] * 1e6
            y_max_um = self.y_coords[-1] * 1e6

            nx = self.fluor.shape[1]
            ny = self.fluor.shape[0]

            col = int((x_um - x_min_um) / max(x_max_um - x_min_um, 1e-6) * nx)
            row = int((y_um - y_min_um) / max(y_max_um - y_min_um, 1e-6) * ny)

            if 0 <= row < ny and 0 <= col < nx:
                counts = self.fluor[row, col]
                tag = "Diamond Substrate"
                if self.current_result is not None:
                    if self.current_result.processable_mask[row, col]:
                        tag = "Processable Zone (Cytoplasm)"
                    elif self.current_result.nucleus_mask[row, col]:
                        tag = "Nucleus Void"
                    elif self.current_result.bright_cluster_mask[row, col]:
                        tag = "Bright NV Cluster"
                    elif self.current_result.cell_interior_mask[row, col]:
                        tag = "Cell Margin (Eroded)"

                self.status_bar.showMessage(
                    f"X: {x_um:.2f} µm | Y: {y_um:.2f} µm | Pixel: [{row}, {col}] | Counts: {counts:,.0f} c/s | Region: {tag}"
                )

    # ------------------------------------------------------------------
    # Config Export & Clipboard
    # ------------------------------------------------------------------

    def get_current_config(self):
        """Return the current tuned configuration dictionary."""
        return {
            "algorithm": self.algo_combo.currentText(),
            "cell_interior": {
                "cell_bg_kernel": int(self.bg_kernel_spin.value()),
                "cap_percentile": float(self.cap_percentile_spin.value()),
                "cell_smooth_sigma": float(self.smooth_sigma_spin.value()),
                "seed_factor": float(self.seed_factor_spin.value()),
                "noise_factor": float(self.noise_factor_spin.value()),
                "min_cell_area_um2": float(self.min_cell_area_spin.value()),
                "closing_iterations": int(self.closing_iter_spin.value()),
                "opening_iterations": int(self.opening_iter_spin.value()),
                "zone_edge_erosion_px": int(self.zone_erosion_spin.value()),
            },
            "nucleus": {
                "enable_nucleus": self.enable_nucleus_cb.isChecked(),
                "nucleus_dark_sigma": float(self.nuc_dark_sigma_spin.value()),
                "nucleus_smooth_sigma": float(self.nuc_smooth_spin.value()),
                "min_nucleus_fraction": float(self.min_nuc_frac_spin.value()) / 100.0,
                "max_nucleus_fraction": float(self.max_nuc_frac_spin.value()) / 100.0,
                "nucleus_min_compactness": float(self.nuc_compact_spin.value()),
                "nucleus_centrality": float(self.nuc_centrality_spin.value()),
            },
            "bright_clusters": {
                "enable_clusters": self.enable_clusters_cb.isChecked(),
                "mask_bright_clusters": self.mask_clusters_cb.isChecked(),
                "bright_cluster_sigma": float(self.clus_sigma_spin.value()),
                "bright_dilate_px": int(self.clus_dilate_spin.value()),
                "min_bright_cluster_area_px": int(self.clus_min_area_spin.value()),
            },
            "poi_extraction": {
                "enable_poi": self.enable_poi_cb.isChecked(),
                "detection_threshold_sigma": float(self.poi_thresh_spin.value()),
                "min_spot_intensity": float(self.poi_intensity_spin.value()),
                "min_snr": float(self.poi_snr_spin.value()),
                "min_circularity": float(self.poi_circ_spin.value()),
            }
        }

    def copy_config_to_clipboard(self):
        """Copy tuned settings formatted as Python dict and YAML to clipboard."""
        cfg = self.get_current_config()
        text = "# Qudi Cell Processor Tuned Configuration\n"
        text += "CELL_PROCESSOR_CONFIG = " + json.dumps(cfg, indent=4)
        QtWidgets.QApplication.clipboard().setText(text)
        QtWidgets.QMessageBox.information(
            self, "Copied", "Tuned settings copied to clipboard as Python dictionary / JSON!"
        )

    def export_mask_and_image(self):
        """Save filtered data, boolean mask, and visual overlay to disk."""
        if self.image is None or self.current_result is None:
            return

        save_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Export Directory", PROJECT_ROOT
        )
        if not save_dir:
            return

        base_name = os.path.splitext(os.path.basename(self.current_filepath))[0] if self.current_filepath else "cell_scan"
        mask_path = os.path.join(save_dir, f"{base_name}_processable_mask.npy")
        cfg_path = os.path.join(save_dir, f"{base_name}_tuned_config.json")

        np.save(mask_path, self.current_result.processable_mask)
        with open(cfg_path, 'w') as f:
            json.dump(self.get_current_config(), f, indent=4)

        QtWidgets.QMessageBox.information(
            self, "Exported", f"Successfully exported:\n- {mask_path}\n- {cfg_path}"
        )


# ======================================================================
# Standalone Execution Entrypoint
# ======================================================================

def main():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    image_path = sys.argv[1] if len(sys.argv) > 1 else None
    window = InteractiveCellTuner(image_path=image_path)
    window.show()
    sys.exit(pg.exec())


if __name__ == '__main__':
    main()
