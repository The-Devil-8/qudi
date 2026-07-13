import os
import sys

# Add qudi to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from logic.cell_segmentation_logic import CellSegmentationLogic
from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.image_rebuild_logic import ImageRebuildLogic

def run_test():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(test_dir, 'output')
    os.makedirs(output_dir, exist_ok=True)
    
    # Original data
    confocal_file = r"d:\qudi-working\qudi\Confocal\20260615-1140-42_confocal_xy_data.dat"
    
    print(f"Testing with file: {confocal_file}")
    
    # Initialize logic modules
    seg_logic = CellSegmentationLogic()
    roi_logic = ROISegmentationLogic()
    rebuild_logic = ImageRebuildLogic()
    
    print("--- Testing Cell Segmentation ---")
    image, x_coords, y_coords, header = seg_logic.parse_dat_file(confocal_file)
    mask, smoothed = seg_logic.segment_cells(image)
    
    out_dat_name = os.path.basename(confocal_file).replace('.dat', '_filtered.dat')
    out_dat_path = os.path.join(output_dir, out_dat_name)
    final_dat_path = seg_logic.filter_and_save(image, mask, header, out_dat_path)
    print(f"Saved filtered data to: {final_dat_path}")
    
    out_png_path = os.path.join(output_dir, out_dat_name.replace('.dat', '.png'))
    rebuild_logic.generate_visual_display(final_dat_path, out_png_path, title="Filtered Cell Image")
    
    print("--- Testing ROI Segmentation ---")
    roi_mask, cell_mask, bright_cluster_mask = roi_logic.segment_roi(image)
    roi_dat_name = os.path.basename(confocal_file).replace('.dat', '_roi.dat')
    roi_dat_path = os.path.join(output_dir, roi_dat_name)
    final_roi_path = roi_logic.filter_and_save(image, roi_mask, header, roi_dat_path)
    print(f"Saved ROI data to: {final_roi_path}")
    
    roi_png_path = os.path.join(output_dir, roi_dat_name.replace('.dat', '.png'))
    rebuild_logic.generate_visual_display(final_roi_path, roi_png_path, title="ROI Filtered Image (Mid-intensity, No Bright Clusters)")
    
    # Also build original image for comparison
    orig_png_path = os.path.join(output_dir, "original_image.png")
    rebuild_logic.generate_visual_display(confocal_file, orig_png_path, title="Original Image")
    print("Test completed successfully.")

if __name__ == "__main__":
    run_test()
