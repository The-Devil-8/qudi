import os
import glob
import numpy as np
from skimage.filters import threshold_otsu
import matplotlib.pyplot as plt
from logic.roi_segmentation_logic import ROISegmentationLogic
from logic.cell_region_processor import CellRegionProcessor

logic = ROISegmentationLogic()
processor = CellRegionProcessor()
files = glob.glob('Confocal3/*_confocal_xy_data.dat')
for f in files:
    image, _, _, _ = logic.parse_dat_file(f)
    fluor = image[:, :, 3]
    import scipy.ndimage as ndi
    kernel = 51
    background = ndi.median_filter(fluor, size=kernel)
    corrected = np.maximum(fluor - background, 0.0)
    smoothed = ndi.gaussian_filter(corrected, sigma=2.0)
    nonzero = smoothed[smoothed > 0]
    if len(nonzero) > 0:
        thresh_otsu = threshold_otsu(nonzero)
        p90 = np.percentile(nonzero, 90)
        p60 = np.percentile(nonzero, 60)
        
        # What does threshold_li give?
        from skimage.filters import threshold_li, threshold_triangle
        thresh_li = threshold_li(nonzero)
        thresh_tri = threshold_triangle(nonzero)
        
        print(f"File: {os.path.basename(f)}")
        print(f"  Otsu: {thresh_otsu:.2f}, P90: {p90:.2f}, P60: {p60:.2f}, Li: {thresh_li:.2f}, Tri: {thresh_tri:.2f}")
