# -*- coding: utf-8 -*-
"""
Visualizer widgets for automated NV finding pipeline (Macro Queue and Micro Cell being scanned).
"""

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets, QtGui

from gui.colordefs import ColorScaleInferno

class ROIBox(pg.RectROI):
    """Simple non-interactive bounding box for the queue."""
    def __init__(self, pos, size, region_id, **kwargs):
        super().__init__(pos, size, movable=False, removable=False, pen=pg.mkPen('y', width=2), **kwargs)
        self.label = pg.TextItem(text=str(region_id), color='y')
        self.label.setPos(pos[0], pos[1] + size[1])
        
    def add_to_view(self, view):
        view.addItem(self)
        view.addItem(self.label)
        
    def _addHandles(self):
        pass


class MacroQueueWindow(QtWidgets.QDialog):
    """Standalone popup window displaying the macro scan with all queued bounding boxes."""
    
    def __init__(self, image_data, x_coords, y_coords, regions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Macro Scan Queue")
        self.resize(800, 800)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        self.gl_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.gl_widget)
        
        plot_view = self.gl_widget.addPlot()
        self.image_item = pg.ImageItem()
        plot_view.addItem(self.image_item)
        
        # Color bar with contrast control (HistogramLUTItem)
        self.lut = pg.HistogramLUTItem()
        self.lut.setImageItem(self.image_item)
        
        # Apply Inferno colormap to match main confocal GUI
        inferno = ColorScaleInferno()
        self.image_item.setLookupTable(inferno.lut)
        
        # Add the LUT to the layout
        self.gl_widget.addItem(self.lut)
        
        # Set data
        self.image_item.setImage(image_data)
        
        # Set axes scaling if coords are provided
        if x_coords is not None and y_coords is not None and len(x_coords) > 1 and len(y_coords) > 1:
            x_scale = (x_coords[-1] - x_coords[0]) / len(x_coords)
            y_scale = (y_coords[-1] - y_coords[0]) / len(y_coords)
            self.image_item.setRect(QtCore.QRectF(x_coords[0], y_coords[0], x_coords[-1]-x_coords[0], y_coords[-1]-y_coords[0]))
        else:
            x_scale, y_scale = 1.0, 1.0
            
        plot_view.setAspectLocked(True)
        
        # Draw bounding boxes
        for region in regions:
            # bbox_physical is (ymin, ymax, xmin, xmax) in real coordinates
            ymin, ymax, xmin, xmax = region.bbox_physical
            w = xmax - xmin
            h = ymax - ymin
            box = ROIBox(pos=(xmin, ymin), size=(w, h), region_id=region.region_id)
            box.add_to_view(plot_view)
            
        # Add a close button
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)


class CellProcessingViewerWidget(QtWidgets.QDockWidget):
    """Dock widget for viewing individual micro scans and processing steps."""
    
    def __init__(self, parent=None):
        super().__init__('Cell Processing Viewer', parent)
        
        self.main_widget = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(self.main_widget)
        self.setWidget(self.main_widget)
        
        self.label = QtWidgets.QLabel("Waiting for macro crop data...")
        self.label.setStyleSheet("font-weight: bold; padding: 4px;")
        self.main_layout.addWidget(self.label)
        
        self.gl_widget = pg.GraphicsLayoutWidget()
        self.main_layout.addWidget(self.gl_widget)
        
        self.plot_view = self.gl_widget.addPlot()
        self.image_item = pg.ImageItem()
        self.plot_view.addItem(self.image_item)
        self.plot_view.setAspectLocked(True)
        self.plot_view.invertY(False)
        
        self.lut = pg.HistogramLUTItem()
        self.lut.setImageItem(self.image_item)
        self.gl_widget.addItem(self.lut)
        
        # Apply Inferno colormap to match main confocal GUI
        inferno = ColorScaleInferno()
        self.image_item.setLookupTable(inferno.lut)
        
    @QtCore.Slot(str, object, object, object)
    def update_view(self, title, image_data, x_coords, y_coords):
        """Update the displayed image and title."""
        self.label.setText(title)
        if image_data is not None:
            self.image_item.setImage(image_data)
            
            if x_coords is not None and y_coords is not None and len(x_coords) > 1 and len(y_coords) > 1:
                w = x_coords[-1] - x_coords[0]
                h = y_coords[-1] - y_coords[0]
                self.image_item.setRect(QtCore.QRectF(x_coords[0], y_coords[0], w, h))
            
            self.lut.autoHistogramRange()
