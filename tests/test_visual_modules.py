import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.scan_region_queue import ScanRegionQueue
from logic.cell_region_processor import CellRegionProcessor
import matplotlib.patches as patches

def generate_scan_region_queue_visuals(parent_file, out_dir):
    print(f"Generating visual for ScanRegionQueue using {parent_file}")
    roi_logic = ROISegmentationLogic()
    image, ux, uy, header = roi_logic.parse_dat_file(parent_file)
    result = roi_logic.segment_roi(image)
    
    queue = ScanRegionQueue()
    print(f"DEBUG: type(result) = {type(result)}")
    if isinstance(result, tuple):
        print(f"DEBUG: len(result) = {len(result)}")
    queue.extract_regions_from_segmentation(result, image, ux, uy)
    
    fluor = image[:, :, 3]
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(fluor, origin='lower', cmap='inferno')
    
    for i, r in enumerate(queue.regions):
        rmin, rmax, cmin, cmax = r.bbox_pixels
        rect = patches.Rectangle((cmin, rmin), 
                                 cmax - cmin, 
                                 rmax - rmin, 
                                 linewidth=2, edgecolor='cyan', facecolor='none')
        ax.add_patch(rect)
        ax.text(cmin, rmin - 5, f"ROI {i+1}", color='cyan', fontsize=12)
        
    ax.set_title(f"ScanRegionQueue Bounding Boxes\n({len(queue.regions)} regions found)")
    
    out_path = os.path.join(out_dir, "scan_region_queue_output.png")
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {out_path}")
    return out_path

def generate_cell_region_processor_visuals(close_file, out_dir):
    print(f"Generating visual for CellRegionProcessor using {close_file}")
    roi_logic = ROISegmentationLogic()
    image, _, _, _ = roi_logic.parse_dat_file(close_file)
    
    processor = CellRegionProcessor()
    result = processor.process(image)
    
    fluor = image[:, :, 3]
    
    fig, axes = plt.subplots(2, 2, figsize=(12, 12))
    axes = axes.flatten()
    
    axes[0].imshow(fluor, origin='lower', cmap='inferno')
    axes[0].set_title("Original Close Scan")
    
    axes[1].imshow(result.cell_interior_mask, origin='lower', cmap='gray')
    axes[1].set_title("Cell Interior Mask")
    
    axes[2].imshow(result.nucleus_mask | result.bright_cluster_mask, origin='lower', cmap='gray')
    axes[2].set_title("Nucleus + Bright Clusters (Excluded)")
    
    axes[3].imshow(fluor * result.processable_mask, origin='lower', cmap='inferno')
    axes[3].set_title("Processable Cytoplasm Zone")
    
    out_path = os.path.join(out_dir, "cell_region_processor_output.png")
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {out_path}")
    return out_path

if __name__ == '__main__':
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    out_dir = os.path.join(base_dir, 'tests', 'output_visuals')
    os.makedirs(out_dir, exist_ok=True)
    
    parent_scan = os.path.join(base_dir, 'Confocal2', '20260705-1517-07_confocal_xy_data.dat')
    close_scan = os.path.join(base_dir, 'Confocal2', '20260706-1701-46_confocal_xy_data.dat')
    
    generate_scan_region_queue_visuals(parent_scan, out_dir)
    generate_cell_region_processor_visuals(close_scan, out_dir)
    print("Done generating visuals.")
