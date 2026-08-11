import sys
import os
import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import median_filter, gaussian_filter, binary_closing, binary_fill_holes, label, distance_transform_edt
from skimage.filters import threshold_otsu, threshold_multiotsu
from skimage.feature import peak_local_max
from skimage.segmentation import watershed
from skimage.morphology import h_maxima

# Adjust path to your qudi workspace
sys.path.append(r'd:\qudi-working\qudi')
from logic.roi_segmentation_logic import ROISegmentationLogic

def run_tests():
    dat_path = r"d:\qudi-working\qudi\Confocal3\20260805-0001-21_confocal_xy_data.dat"
    
    logic = ROISegmentationLogic()
    image, _, _, _ = logic.parse_dat_file(dat_path)
    fluor = image[:, :, 3].astype(float)
    pixel_size = logic.estimate_pixel_size(image)
    pixel_area_um2 = (pixel_size * 1e6) ** 2
    
    # --- Replicate pipeline up to raw_mask ---
    bg = median_filter(fluor, size=51)
    corrected = np.maximum(fluor - bg, 0)
    despiked = median_filter(corrected, size=7)
    smoothed = gaussian_filter(despiked, sigma=6.0)
    
    nonzero = smoothed[smoothed > 0]
    p99 = np.percentile(nonzero, 99)
    clipped = np.clip(nonzero, a_min=None, a_max=p99)
    thresh = threshold_otsu(clipped)
    
    raw_mask = smoothed > thresh
    raw_mask = binary_closing(raw_mask, iterations=2)
    raw_mask = binary_fill_holes(raw_mask)
    
    # --- 1. Current Approach: Distance Transform ---
    distance = distance_transform_edt(raw_mask)
    min_cell_area_px = max(1, int(50.0 / pixel_area_um2))
    min_dist_px = max(3, int(0.5 * np.sqrt(min_cell_area_px / np.pi)))
    
    coords_dt = peak_local_max(distance, min_distance=min_dist_px, labels=raw_mask)
    mask_dt = np.zeros(distance.shape, dtype=bool)
    mask_dt[tuple(coords_dt.T)] = True
    markers_dt, _ = label(mask_dt)
    labels_dt = watershed(-distance, markers_dt, mask=raw_mask)
    
    # --- 2. Recommended: Intensity-Based Watershed ---
    coords_int = peak_local_max(smoothed, min_distance=min_dist_px, labels=raw_mask)
    mask_int = np.zeros(smoothed.shape, dtype=bool)
    mask_int[tuple(coords_int.T)] = True
    markers_int, _ = label(mask_int)
    labels_int = watershed(-smoothed, markers_int, mask=raw_mask)
    
    # --- Plotting ---
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    axes[0].imshow(labels_dt, cmap='nipy_spectral'); axes[0].set_title('1. Current: Distance Transform')
    axes[1].imshow(labels_int, cmap='nipy_spectral'); axes[1].set_title('2. Intensity-Based (Recommended)')
    plt.tight_layout()
    out_path = os.path.join(r"d:\qudi-working\qudi", 'watershed_comparison.png')
    plt.savefig(out_path)
    print("Saved watershed_comparison.png")

if __name__ == "__main__":
    run_tests()
