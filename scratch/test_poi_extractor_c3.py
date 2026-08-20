import os, sys, glob
import numpy as np

sys.path.insert(0, os.path.abspath('.'))

from logic.cell_segmentation_logic import CellSegmentationLogic
from logic.cell_region_processor import CellRegionProcessor
from logic.poi_extractor import POIExtractor

def test():
    cell_logic = CellSegmentationLogic()
    proc = CellRegionProcessor()
    extractor = POIExtractor()
    
    files = glob.glob('Confocal3/*_confocal_xy_data.dat')
    for f in files:
        image, _, _, _ = cell_logic.parse_dat_file(f)
        
        # 1. Get cell boxes
        mask, smooth, labeled, cell_boxes = cell_logic.segment_cells_with_instances(image)
        if not cell_boxes:
            continue
            
        print(f"\nFile: {os.path.basename(f)}, Boxes: {len(cell_boxes)}")
        
        # Test first 2 boxes
        for i, box in enumerate(cell_boxes[:5]):
            min_r, min_c, max_r, max_c = box['bbox_px']
            # Add padding
            min_r = max(0, min_r - 15)
            max_r = min(image.shape[0], max_r + 15)
            min_c = max(0, min_c - 15)
            max_c = min(image.shape[1], max_c + 15)
            
            crop = image[min_r:max_r, min_c:max_c, :]
            
            # 2. Cell Region Processor
            result = proc.process(crop, mask_bright_clusters=False)
            zone_mask = result.processable_mask
            print(f"  Box {i} ({max_r-min_r}x{max_c-min_c}): zone area = {zone_mask.sum()} px")
            
            if not zone_mask.any():
                print("    -> No processable zone")
                continue
                
            # 3. POI Extractor
            try:
                strong, marginal, rejected, diag = extractor.extract(crop, zone_mask)
                print(f"    POI Extractor => Strong: {len(strong)}, Marginal: {len(marginal)}, Rejected: {len(rejected)}")
                if diag:
                    if 'cip_error' in diag:
                        print(f"      CIP Error: {diag['cip_error']}")
            except Exception as e:
                print(f"    POI Extractor failed: {e}")

if __name__ == '__main__':
    test()
