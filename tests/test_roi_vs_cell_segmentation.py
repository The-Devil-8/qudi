import os
import sys
import glob
import numpy as np
import pytest
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if os.path.join(PROJECT_ROOT, 'logic') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'logic'))

from cell_segmentation_logic import CellSegmentationLogic
from roi_segmentation_logic import ROISegmentationLogic

VISUALS_DIR = os.path.join(PROJECT_ROOT, 'tests', 'output_visuals')
CONFOCAL3_DIR = os.path.join(PROJECT_ROOT, 'Confocal3')

def create_categorical_cmap(n_colors=256):
    np.random.seed(42)
    colors = np.random.rand(n_colors, 4)
    colors[:, 3] = 0.8  
    colors[0] = [0, 0, 0, 0]  
    return ListedColormap(colors)

def test_compare_roi_and_cell_segmentation():
    os.makedirs(VISUALS_DIR, exist_ok=True)
    c3_files = sorted(glob.glob(os.path.join(CONFOCAL3_DIR, '*_confocal_xy_data.dat')))
    
    if not c3_files:
        pytest.skip("Confocal3 dataset files missing.")

    cell_logic = CellSegmentationLogic()
    roi_logic = ROISegmentationLogic()

    for filepath in c3_files:
        filename = os.path.basename(filepath)
        
        # Parse dat file
        image, ux, uy, header = cell_logic.parse_dat_file(filepath)
        fluor = image[:, :, 3]
        ny, nx = fluor.shape

        # 1. ROISegmentationLogic
        roi_result = roi_logic.segment_roi(image)
        roi_mask = roi_result.get('roi_mask', np.zeros_like(fluor, dtype=bool))
        
        # 2. CellSegmentationLogic
        cell_mask, smooth, labeled, cell_boxes = cell_logic.segment_cells_with_instances(image)
        contours = cell_logic.get_contours(cell_mask)

        # Plot Visual Comparison
        fig, axes = plt.subplots(1, 4, figsize=(22, 5))
        p2, p98 = np.percentile(fluor, (2, 98))
        
        # Raw
        axes[0].imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
        axes[0].set_title(f"Raw Scan (Confocal3)\n{filename}", fontsize=10, fontweight='bold')
        axes[0].axis('off')
        
        # ROI Segmentation
        axes[1].imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
        roi_overlay = np.zeros((*fluor.shape, 4))
        roi_overlay[roi_mask] = [0, 1, 0, 0.4] # Green overlay
        axes[1].imshow(roi_overlay)
        axes[1].set_title(f"ROISegmentationLogic\nMask Area: {roi_mask.sum()} px", fontsize=10, fontweight='bold')
        axes[1].axis('off')
        
        # Cell Segmentation Mask
        axes[2].imshow(fluor, cmap='inferno', vmin=p2, vmax=p98)
        for cnt in contours:
            axes[2].plot(cnt[:, 1], cnt[:, 0], color='cyan', linewidth=1.5)
        axes[2].set_title(f"CellSegmentationLogic\nMask Area: {cell_mask.sum()} px", fontsize=10, fontweight='bold')
        axes[2].axis('off')
        
        # Cell Segmentation Instances
        cmap_lbls = create_categorical_cmap()
        axes[3].imshow(labeled, cmap=cmap_lbls, interpolation='nearest')
        for box in cell_boxes:
            min_r, min_c, max_r, max_c = box['bbox_px']
            rect = plt.Rectangle((min_c, min_r), max_c - min_c, max_r - min_r,
                                 fill=False, edgecolor='yellow', linewidth=0.8, linestyle=':')
            axes[3].add_patch(rect)
        axes[3].set_title(f"Extracted Cell Instances\nN={len(cell_boxes)}", fontsize=10, fontweight='bold')
        axes[3].axis('off')
        
        plt.tight_layout()
        out_png = os.path.join(VISUALS_DIR, f"comparison_roi_vs_cell_{os.path.splitext(filename)[0]}.png")
        plt.savefig(out_png, dpi=150, bbox_inches='tight')
        plt.close(fig)

if __name__ == '__main__':
    test_compare_roi_and_cell_segmentation()
