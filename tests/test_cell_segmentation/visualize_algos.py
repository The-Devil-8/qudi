import os
import sys
import glob
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

# Ensure qudi is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from logic.roi_segmentation_logic import ROISegmentationLogic
from tests.test_cell_segmentation.research_algos import (
    DistanceTransformSegmentation,
    GradientEnhancedSegmentation,
    InpaintingActiveContourSegmentation
)

def create_random_cmap(n_colors=256):
    """Create a random colormap for categorical data (e.g., cell labels)."""
    np.random.seed(42) # Consistent colors for comparison
    colors = np.random.rand(n_colors, 4)
    colors[:, 3] = 1.0 # opaque
    colors[0] = [0, 0, 0, 1] # Background is black
    return ListedColormap(colors)

def process_and_plot(dat_filepath, output_dir):
    print(f"Processing {os.path.basename(dat_filepath)}...")
    base_logic = ROISegmentationLogic()
    
    try:
        image, ux, uy, header = base_logic.parse_dat_file(dat_filepath)
    except Exception as e:
        print(f"Failed to load {dat_filepath}: {e}")
        return
        
    fluor = image[:, :, 3]
    
    algorithms = [
        DistanceTransformSegmentation(),
        GradientEnhancedSegmentation(),
        InpaintingActiveContourSegmentation()
    ]
    
    results = []
    for algo in algorithms:
        print(f"  Running {algo.name}...")
        try:
            res = algo.segment_roi(image)
            results.append(res)
        except Exception as e:
            print(f"  Error in {algo.name}: {e}")
            results.append(None)
            
    # Plotting
    fig, axes = plt.subplots(1, 4, figsize=(20, 5))
    
    # Original Image (clipped for visibility without log distortion)
    ax = axes[0]
    p98 = np.percentile(fluor, 98)
    ax.imshow(np.clip(fluor, 0, p98), cmap='gray')
    ax.set_title("Original Image")
    ax.axis('off')
    
    cmap = create_random_cmap(500)
    
    for i, (algo, res) in enumerate(zip(algorithms, results)):
        ax = axes[i + 1]
        if res is not None:
            labels = res['component_labels']
            num_cells = len(np.unique(labels)) - 1
            ax.imshow(labels, cmap=cmap, interpolation='nearest')
            ax.set_title(f"{algo.name}\nCells Found: {num_cells}")
        else:
            ax.set_title(f"{algo.name}\nFAILED")
        ax.axis('off')
        
    plt.tight_layout()
    
    # Save figure
    filename = os.path.basename(dat_filepath)
    name, _ = os.path.splitext(filename)
    out_path = os.path.join(output_dir, f"compare_{name}.png")
    
    os.makedirs(output_dir, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved comparison to {out_path}")

if __name__ == "__main__":
    confocal3_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'Confocal3'))
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'output'))
    
    dat_files = glob.glob(os.path.join(confocal3_dir, "*.dat"))
    # Filter only image dat files, not the raw data point arrays if they exist, but here we parse the standard Qudi confocal map.
    # Usually the maps are named `...image_Dev1Ctr3.dat` or similar. Let's process the ones containing 'image'
    target_files = [f for f in dat_files if 'data' in os.path.basename(f)]
    
    for df in target_files:
        process_and_plot(df, output_dir)
