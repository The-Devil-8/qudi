import os
import sys
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from scipy.ndimage import find_objects

# Add qudi root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from logic.roi_segmentation_logic import ROISegmentationLogic

def test_segmentation_on_dataset(dataset_dir, output_prefix):
    # Find a .dat file in the dataset directory
    dat_files = [f for f in os.listdir(dataset_dir) if f.endswith('data.dat') or f.endswith('image_Dev1Ctr3.dat')]
    if not dat_files:
        print(f"No .dat file found in {dataset_dir}")
        return
    
    # Pick the first one
    dat_path = os.path.join(dataset_dir, dat_files[0])
    print(f"Testing on: {dat_path}")
    
    logic = ROISegmentationLogic()
    image, ux, uy, header = logic.parse_dat_file(dat_path)
    
    # Run segmentation
    result = logic.segment_roi(image)
    
    fluor = image[:, :, 3]
    labels = result['component_labels']
    
    # Convert to kc/s for the plot to match user's actual image
    fluor_kcs = fluor / 1000.0
    
    # Calculate physical extents in micrometers
    x_min_um = np.min(ux) * 1e6
    x_max_um = np.max(ux) * 1e6
    y_min_um = np.min(uy) * 1e6
    y_max_um = np.max(uy) * 1e6
    extent = [x_min_um, x_max_um, y_min_um, y_max_um]
    
    fig, ax = plt.subplots(1, 1, figsize=(10, 10))
    
    # Clip the image to a lower percentile (e.g. 97th) to wash out extreme NV spikes
    # and reveal the dimmer cell background, mimicking the user's 0-600 kc/s scale.
    vmax = np.percentile(fluor_kcs, 98)
    
    # The 'plasma' or 'magma' colormap matches the user's uploaded image style
    im = ax.imshow(fluor_kcs, cmap='magma', origin='lower', extent=extent, vmax=vmax)
    plt.colorbar(im, ax=ax, label='Fluorescence (kc/s)')
    
    # Get bounding boxes using find_objects
    slices = find_objects(labels)
    
    for i, sl in enumerate(slices):
        if sl is None:
            continue
        
        # Slices give indices. We must convert indices to micrometers.
        min_r, max_r = sl[0].start, sl[0].stop - 1
        min_c, max_c = sl[1].start, sl[1].stop - 1
        
        # Convert to micrometers
        y_min_box = np.interp(min_r, np.arange(len(uy)), uy) * 1e6
        y_max_box = np.interp(max_r, np.arange(len(uy)), uy) * 1e6
        x_min_box = np.interp(min_c, np.arange(len(ux)), ux) * 1e6
        x_max_box = np.interp(max_c, np.arange(len(ux)), ux) * 1e6
        
        width = x_max_box - x_min_box
        height = y_max_box - y_min_box
        
        # Draw bounding box
        rect = patches.Rectangle((x_min_box, y_min_box), width, height, linewidth=2, edgecolor='red', facecolor='none')
        ax.add_patch(rect)
        ax.text(x_min_box, y_min_box, str(i+1), color='white', fontsize=12, weight='bold', bbox=dict(facecolor='red', alpha=0.5))

    ax.set_xlabel('X position (µm)', fontsize=14)
    ax.set_ylabel('Y position (µm)', fontsize=14)
    ax.set_title(f"ROI Segmentation: {os.path.basename(dat_path)}\nFound {len([s for s in slices if s is not None])} ROIs", fontsize=14)
    
    # Save output
    out_dir = os.path.join(os.path.dirname(__file__), 'output_visuals')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{output_prefix}_segmentation.png")
    
    plt.savefig(out_path, dpi=150)
    print(f"Saved visual to: {out_path}")
    
if __name__ == '__main__':
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    test_segmentation_on_dataset(os.path.join(base_dir, 'Confocal3'), 'confocal3')
    test_segmentation_on_dataset(os.path.join(base_dir, 'Confocal2'), 'confocal2')
    test_segmentation_on_dataset(os.path.join(base_dir, 'Confocal'), 'confocal1')
