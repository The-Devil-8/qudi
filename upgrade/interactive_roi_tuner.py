import os
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pyqtgraph.Qt import QtWidgets, QtCore
import pyqtgraph as pg

# Map common classes for convenience
QApplication = QtWidgets.QApplication
QMainWindow = QtWidgets.QMainWindow
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QSlider = QtWidgets.QSlider
QLabel = QtWidgets.QLabel
QComboBox = QtWidgets.QComboBox
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QFileDialog = QtWidgets.QFileDialog
Qt = QtCore.Qt

from logic.cell_segmentation_logic import CellSegmentationLogic
from logic.roi_segmentation_logic import ROISegmentationLogic
from upgrade.roi_segmentation_logic import ROISegmentationLogic as UpgradedROISegmentationLogic

class InteractiveTuner(QMainWindow):
    def __init__(self, image_path=None):
        super().__init__()
        self.setWindowTitle("Interactive ROI Segmentation Tuner")
        self.resize(1200, 800)
        
        self.image = None
        self.img_data = None
        
        self.cell_logic = CellSegmentationLogic()
        self.roi_logic = ROISegmentationLogic()
        self.upgraded_roi_logic = UpgradedROISegmentationLogic()
        
        self.init_ui()
        
        if image_path and os.path.exists(image_path):
            self.load_image(image_path)
        else:
            default_path = os.path.join('Confocal4', '20260812-1610-05_confocal_xy_scan_raw_pixel_image_raw.png')
            if os.path.exists(default_path):
                self.load_image(default_path)
                
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        layout = QHBoxLayout()
        main_widget.setLayout(layout)
        
        # Left Panel (Controls)
        controls_layout = QVBoxLayout()
        
        load_btn = QPushButton("Load Image (.dat or .png)")
        load_btn.clicked.connect(self.prompt_load_image)
        controls_layout.addWidget(load_btn)
        
        # Algorithm selection
        controls_layout.addWidget(QLabel("Algorithm:"))
        self.algo_combo = QComboBox()
        self.algo_combo.addItems(["Legacy ROISegmentationLogic", 
                                  "Upgraded ROISegmentationLogic",
                                  "Upgraded CellSegmentationLogic (Instances)"])
        self.algo_combo.currentIndexChanged.connect(self.update_segmentation)
        controls_layout.addWidget(self.algo_combo)
        
        # Sliders
        self.cap_percentile_slider, self.cap_percentile_lbl = self.create_slider("Cap Percentile", 50, 100, 85, controls_layout)
        self.bg_kernel_slider, self.bg_kernel_lbl = self.create_slider("BG Kernel (odd)", 3, 201, 51, controls_layout)
        self.smooth_sigma_slider, self.smooth_sigma_lbl = self.create_slider("Smooth Sigma (*10)", 0, 100, 40, controls_layout)
        self.min_area_slider, self.min_area_lbl = self.create_slider("Min Area (um^2)", 0, 500, 30, controls_layout)
        
        self.show_mask_cb = QCheckBox("Show Mask Overlay")
        self.show_mask_cb.setChecked(True)
        self.show_mask_cb.stateChanged.connect(self.update_segmentation)
        controls_layout.addWidget(self.show_mask_cb)
        
        controls_layout.addStretch()
        
        control_panel = QWidget()
        control_panel.setLayout(controls_layout)
        control_panel.setFixedWidth(300)
        
        layout.addWidget(control_panel)
        
        # Right Panel (Image)
        self.graph_layout = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graph_layout)
        
        self.view = self.graph_layout.addViewBox()
        self.view.setAspectLocked(True)
        self.img_item = pg.ImageItem()
        self.view.addItem(self.img_item)
        
        # Overlay for instances/mask
        self.mask_item = pg.ImageItem()
        # self.mask_item.setCompositionMode(...)
        self.view.addItem(self.mask_item)
        
    def create_slider(self, name, min_val, max_val, default_val, layout):
        lbl = QLabel(f"{name}: {default_val}")
        layout.addWidget(lbl)
        
        slider = QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(default_val)
        
        def on_change(val):
            if "Kernel" in name and val % 2 == 0:
                val += 1
                slider.setValue(val)
                
            display_val = val / 10.0 if "Sigma" in name else val
            lbl.setText(f"{name}: {display_val}")
            self.update_segmentation()
            
        slider.valueChanged.connect(on_change)
        layout.addWidget(slider)
        return slider, lbl

    def prompt_load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.dat)")
        if path:
            self.load_image(path)

    def load_image(self, path):
        if path.endswith('.png'):
            img = Image.open(path).convert('L')
            self.img_data = np.array(img, dtype=float).T # pyqtgraph expects (x, y)
        elif path.endswith('.dat'):
            try:
                # Use the built-in parser from the logic module
                parsed_image, _, _, _ = self.roi_logic.parse_dat_file(path)
                # parsed_image is (ny, nx, 4), we just want the fluor channel transposed for pyqtgraph
                self.img_data = parsed_image[:, :, 3].T
            except Exception as e:
                print(f"Could not parse .dat file: {e}")
                
        if self.img_data is None:
            print("Failed to load image.")
            return
            
        # Create standard qudi pseudo 4-channel image (y, x, c)
        nx, ny = self.img_data.shape
        self.image = np.zeros((ny, nx, 4), dtype=float) # Note: transposed back for logic
        self.image[:, :, 3] = self.img_data.T
        
        # Display base image
        p2, p98 = np.percentile(self.img_data, (2, 98))
        self.img_item.setImage(self.img_data, autoLevels=False, levels=(p2, p98))
        self.img_item.setLookupTable(pg.colormap.get('inferno').getLookupTable())
        
        self.update_segmentation()

    def update_segmentation(self):
        if self.image is None or not self.show_mask_cb.isChecked():
            self.mask_item.clear()
            return
            
        algo = self.algo_combo.currentText()
        cap_p = self.cap_percentile_slider.value()
        bg_k = self.bg_kernel_slider.value()
        sigma = self.smooth_sigma_slider.value() / 10.0
        min_area = self.min_area_slider.value()
        
        mask = None
        overlay = np.zeros((self.img_data.shape[0], self.img_data.shape[1], 4), dtype=np.uint8)
        
        try:
            if algo == "Legacy ROISegmentationLogic":
                # Legacy does not take our new params
                res = self.roi_logic.segment_roi(self.image)
                mask = res.get('roi_mask')
                if mask is not None:
                    mask = mask.T # match pyqtgraph
                    overlay[mask] = [0, 255, 0, 100] # Green
                    
            elif algo == "Upgraded ROISegmentationLogic":
                # Uses cell segmentation logic under the hood via wrapper
                res = self.upgraded_roi_logic.segment_roi(self.image)
                mask = res.get('roi_mask')
                if mask is not None:
                    mask = mask.T
                    overlay[mask] = [0, 255, 255, 100] # Cyan
                    
            elif algo == "Upgraded CellSegmentationLogic (Instances)":
                mask, smooth, labeled, boxes = self.cell_logic.segment_cells_with_instances(
                    self.image, 
                    min_cell_area_um2=min_area,
                    cap_percentile=cap_p,
                    bg_kernel=bg_k,
                    smooth_sigma=sigma
                )
                mask = mask.T
                labeled = labeled.T
                
                # Color instances randomly
                np.random.seed(42)
                colors = np.random.randint(50, 255, size=(labeled.max() + 1, 3))
                
                for i in range(1, labeled.max() + 1):
                    instance_mask = (labeled == i)
                    overlay[instance_mask, :3] = colors[i]
                    overlay[instance_mask, 3] = 120 # Alpha
                    
            self.mask_item.setImage(overlay)
        except Exception as e:
            print(f"Algorithm crashed with parameters: {e}")
            self.mask_item.clear()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = InteractiveTuner(sys.argv[1] if len(sys.argv) > 1 else None)
    window.show()
    sys.exit(pg.exec())
