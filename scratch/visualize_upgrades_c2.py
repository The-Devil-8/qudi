import os
import sys
import glob
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches

sys.path.insert(0, os.path.abspath('.'))

from upgrade.cell_region_processor import CellRegionProcessor
from upgrade.poi_extractor import POIExtractor
from logic.roi_segmentation_logic import ROISegmentationLogic # just for parse_dat_file

CONFOCAL2_DIR = 'Confocal2'
OUTPUT_DIR = os.path.join('tests', 'output_visuals', 'upgrades_c2')

os.makedirs(OUTPUT_DIR, exist_ok=True)

CLOSE_SCANS = [
    '20260706-1701-46_confocal_xy_data.dat',
    '20260706-1724-08_confocal_xy_data.dat',
    '20260706-1833-28_confocal_xy_data.dat'
]

def generate_visuals():
    seg = ROISegmentationLogic()
    proc = CellRegionProcessor()
    extractor = POIExtractor()
    
    for filename in CLOSE_SCANS:
        fp = os.path.join(CONFOCAL2_DIR, filename)
        if not os.path.exists(fp):
            print(f"Skipping {filename}, not found.")
            continue
            
        print(f"Processing {filename}...")
        image, ux, uy, hdr = seg.parse_dat_file(fp)
        
        # 1. Run Upgraded CellRegionProcessor
        cell_result = proc.process(image)
        
        # 2. Run Upgraded POIExtractor
        extract_result = extractor.extract(cell_result, image, x_coords=ux, y_coords=uy)
        
        # 3. Plotting
        fluor = image[:, :, 3]
        
        fig, axes = plt.subplots(1, 3, figsize=(18, 6))
        
        # --- Plot 1: Raw Fluorescence ---
        ax = axes[0]
        im = ax.imshow(fluor, cmap='inferno', origin='lower', extent=[ux[0]*1e6, ux[-1]*1e6, uy[0]*1e6, uy[-1]*1e6])
        ax.set_title("Raw Fluorescence")
        ax.set_xlabel("X (um)")
        ax.set_ylabel("Y (um)")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        
        # --- Plot 2: CellRegionProcessor Overlay ---
        ax = axes[1]
        overlay = proc.get_overlay_colors(cell_result)
        # overlay is (ny, nx, 4) where colors are mapped
        # to show it over fluor, we blend it
        norm_fluor = (fluor - fluor.min()) / (fluor.max() - fluor.min() + 1e-9)
        gray_bg = np.stack([norm_fluor]*3, axis=-1)
        
        # Alpha blending
        alpha = overlay[:, :, 3:4]
        blended = overlay[:, :, :3] * alpha + gray_bg * (1 - alpha)
        
        ax.imshow(blended, origin='lower', extent=[ux[0]*1e6, ux[-1]*1e6, uy[0]*1e6, uy[-1]*1e6])
        ax.set_title("Cell Region (Green=Processable, Red=Clusters)")
        ax.set_xlabel("X (um)")
        
        # --- Plot 3: POIExtractor Candidates ---
        ax = axes[2]
        ax.imshow(fluor, cmap='inferno', origin='lower', extent=[ux[0]*1e6, ux[-1]*1e6, uy[0]*1e6, uy[-1]*1e6])
        ax.set_title("POI Candidates (Green=Strong, Yellow=Marg, Red=Rej)")
        ax.set_xlabel("X (um)")
        
        # Plot candidates
        def plot_cands(cands, color, marker, label):
            if not cands: return
            xs = [c.x * 1e6 for c in cands]
            ys = [c.y * 1e6 for c in cands]
            ax.scatter(xs, ys, facecolors='none', edgecolors=color, marker=marker, s=80, linewidths=1.5, label=label)
            
        plot_cands(extract_result.strong_candidates, 'lime', 'o', 'Strong')
        plot_cands(extract_result.marginal_candidates, 'yellow', 's', 'Marginal')
        plot_cands(extract_result.rejected_candidates, 'red', 'x', 'Rejected')
        
        if extract_result.strong_candidates or extract_result.marginal_candidates or extract_result.rejected_candidates:
            ax.legend(loc='upper right')
            
        plt.tight_layout()
        
        out_filename = filename.replace('.dat', '_upgraded_vis.png')
        out_path = os.path.join(OUTPUT_DIR, out_filename)
        plt.savefig(out_path, dpi=150)
        plt.close(fig)
        print(f"Saved visual to {out_path}")

if __name__ == '__main__':
    generate_visuals()
