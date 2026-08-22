import os
import sys
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image

sys.path.insert(0, os.path.abspath('.'))
from logic.cell_segmentation_logic import CellSegmentationLogic
from logic.roi_segmentation_logic import ROISegmentationLogic
from matplotlib.colors import ListedColormap

def create_categorical_cmap(n_colors=256):
    np.random.seed(42)
    colors = np.random.rand(n_colors, 4)
    colors[:, 3] = 0.8  
    colors[0] = [0, 0, 0, 0]  
    return ListedColormap(colors)

def main():
    png_path = 'Confocal4/20260812-1610-05_confocal_xy_scan_raw_pixel_image_raw.png'
    
    # Load PNG
    img = Image.open(png_path).convert('L') # Convert to grayscale
    img_data = np.array(img, dtype=float)
    
    # Construct a pseudo 4-channel image
    ny, nx = img_data.shape
    image = np.zeros((ny, nx, 4), dtype=float)
    image[:, :, 3] = img_data
    
    cell_logic = CellSegmentationLogic()
    roi_logic = ROISegmentationLogic()
    
    # 1. ROISegmentationLogic
    roi_result = roi_logic.segment_roi(image)
    roi_mask = roi_result.get('roi_mask', np.zeros_like(img_data, dtype=bool))
    
    # 2. CellSegmentationLogic
    cell_mask, smooth, labeled, cell_boxes = cell_logic.segment_cells_with_instances(image)
    contours = cell_logic.get_contours(cell_mask)
    
    # Plot Visual Comparison
    fig, axes = plt.subplots(1, 4, figsize=(22, 5))
    p2, p98 = np.percentile(img_data, (2, 98))
    
    # Raw
    axes[0].imshow(img_data, cmap='inferno', vmin=p2, vmax=p98)
    axes[0].set_title(f"Raw Scan (Confocal4 PNG)", fontsize=10, fontweight='bold')
    axes[0].axis('off')
    
    # ROI Segmentation
    axes[1].imshow(img_data, cmap='inferno', vmin=p2, vmax=p98)
    roi_overlay = np.zeros((*img_data.shape, 4))
    roi_overlay[roi_mask] = [0, 1, 0, 0.4] # Green overlay
    axes[1].imshow(roi_overlay)
    axes[1].set_title(f"ROISegmentationLogic\nMask Area: {roi_mask.sum()} px", fontsize=10, fontweight='bold')
    axes[1].axis('off')
    
    # Cell Segmentation Mask
    axes[2].imshow(img_data, cmap='inferno', vmin=p2, vmax=p98)
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
    out_dir = os.path.join('tests', 'output_visuals', 'Confocal4')
    os.makedirs(out_dir, exist_ok=True)
    out_png = os.path.join(out_dir, "comparison_roi_vs_cell_Confocal4.png")
    plt.savefig(out_png, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved visual to {out_png}")

if __name__ == '__main__':
    main()
