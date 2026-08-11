import os
import numpy as np
import matplotlib.pyplot as plt
from logic.roi_segmentation_logic import ROISegmentationLogic

def test_watershed():
    logic = ROISegmentationLogic()
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # Let's use Confocal1 first to see why Box 1 and 2 are merged.
    dat_path = os.path.join(base_dir, 'Confocal', '20260615-1140-42_confocal_xy_data.dat')
    
    image, ux, uy, header = logic.parse_dat_file(dat_path)
    
    # We will instrument the segment_roi method manually here to see the distance transform and peaks.
    fluor = image[:, :, 3].astype(float)
    
    # Step 1-4: Background and smoothing
    from scipy.ndimage import median_filter, gaussian_filter
    bg = median_filter(fluor, size=51)
    subtracted = fluor - bg
    subtracted[subtracted < 0] = 0
    despiked = median_filter(subtracted, size=7)
    smoothed = gaussian_filter(despiked, sigma=6.0)
    
    # Step 5: Threshold
    nonzero = smoothed[smoothed > 0]
    p99 = np.percentile(nonzero, 99)
    clipped = np.clip(nonzero, a_min=None, a_max=p99)
    from skimage.filters import threshold_otsu
    thresh = threshold_otsu(clipped)
    
    raw_mask = smoothed > thresh
    
    # Step 6: Morph and watershed
    from scipy.ndimage import binary_closing, binary_fill_holes
    raw_mask = binary_closing(raw_mask, iterations=2)
    raw_mask = binary_fill_holes(raw_mask)
    
    from scipy.ndimage import distance_transform_edt
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed
    from scipy.ndimage import label
    
    # Try different min_dist_px
    distance = distance_transform_edt(raw_mask)
    
    # Let's test using the smoothed intensity as the distance map instead!
    # Because intensity has natural peaks at the center of each cell.
    # We will use 'smoothed' as the landscape for peak finding instead of 'distance'.
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    axes[0].imshow(smoothed, cmap='magma')
    axes[0].set_title('Smoothed Intensity')
    
    # Classic distance transform
    min_dist_px = 5
    coords_dist = peak_local_max(distance, min_distance=min_dist_px, labels=raw_mask)
    axes[1].imshow(distance, cmap='viridis')
    axes[1].plot(coords_dist[:, 1], coords_dist[:, 0], 'r.')
    axes[1].set_title(f'Distance Peaks (min_dist={min_dist_px})')
    
    # Intensity-based peaks
    coords_int = peak_local_max(smoothed, min_distance=min_dist_px, labels=raw_mask)
    axes[2].imshow(smoothed, cmap='viridis')
    axes[2].plot(coords_int[:, 1], coords_int[:, 0], 'r.')
    axes[2].set_title(f'Intensity Peaks (min_dist={min_dist_px})')
    
    plt.savefig(os.path.join(base_dir, 'scratch', 'watershed_research.png'))
    print(f"Found {len(coords_dist)} peaks using distance, {len(coords_int)} peaks using intensity.")

if __name__ == '__main__':
    test_watershed()
