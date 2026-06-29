import numpy as np
import time
import os
import sys

# Add qudi root to path so imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from logic.image_analysis import ConfocalImageAnalysis

def generate_dummy_confocal_image(size=100, spot_sigma=1.5, num_spots=15):
    """Generates a dummy confocal image with Gaussian spots."""
    print(f"Generating {size}x{size} dummy confocal scan with {num_spots} NV centers...")
    
    # 20um x 20um area
    x = np.linspace(0, 20e-6, size)
    y = np.linspace(0, 20e-6, size)
    X, Y = np.meshgrid(x, y)
    
    # Background noise (counts per second)
    image = np.random.normal(5000, 1000, (size, size))
    image = np.clip(image, 0, None)
    
    # True positions
    true_positions = []
    
    for i in range(num_spots):
        # Random position within 2um to 18um
        x0 = np.random.uniform(2e-6, 18e-6)
        y0 = np.random.uniform(2e-6, 18e-6)
        true_positions.append((x0, y0))
        
        # Spot amplitude (cps)
        amplitude = np.random.uniform(20000, 100000)
        
        # Convert sigma from pixels to um for the calculation
        sigma_um = (20e-6 / size) * spot_sigma
        
        # Add 2D Gaussian
        spot = amplitude * np.exp(-((X - x0)**2 + (Y - y0)**2) / (2 * sigma_um**2))
        image += spot
        
    return image, x, y, true_positions

def run_test():
    image, x_coords, y_coords, true_positions = generate_dummy_confocal_image(
        size=100, spot_sigma=1.5, num_spots=15
    )
    
    print("\n--- Running CIP Detection Algorithm ---")
    start_time = time.time()
    
    # 1. Background subtraction
    bg = ConfocalImageAnalysis.estimate_background(image, kernel_size=15)
    corrected = ConfocalImageAnalysis.subtract_background(image, bg)
    
    # 2. Noise estimation
    noise_sigma = ConfocalImageAnalysis.estimate_noise_level(corrected)
    threshold_value = 4.0 * noise_sigma
    print(f"Noise sigma: {noise_sigma:.2f} cps, Threshold (4 sigma): {threshold_value:.2f} cps")
    
    # 3. Detection
    mask = ConfocalImageAnalysis.threshold_intensity(corrected, threshold_value)
    
    neighborhood_pixels = 3
    local_max_pos = ConfocalImageAnalysis.detect_local_maxima(corrected, mask, neighborhood_pixels)
    
    results = []
    
    # 4. Processing candidates
    if len(local_max_pos) > 0:
        intensities = [corrected[r, c] for r, c in local_max_pos]
        
        # 5. Clustering
        clusters = ConfocalImageAnalysis.cluster_detections(local_max_pos, intensities, min_distance=3)
        
        for pos, intensity in clusters:
            r, c = int(pos[0]), int(pos[1])
            
            # 6. Shape validation
            valid, circularity = ConfocalImageAnalysis.validate_spot_shape(corrected, r, c, radius=3)
            
            # 7. Subpixel refinement
            refined = ConfocalImageAnalysis.refine_position_gaussian_2d(
                corrected, r, c, radius=3, x_coords=x_coords, y_coords=y_coords
            )
            
            if refined['x'] is not None and refined['y'] is not None:
                snr = intensity / max(noise_sigma, 1.0)
                conf = ConfocalImageAnalysis.compute_detection_confidence(snr, circularity, refined['quality'])
                
                results.append({
                    'x': refined['x'],
                    'y': refined['y'],
                    'intensity': intensity,
                    'confidence': conf,
                    'fit_r2': refined['quality']
                })
                
        # Sort by intensity
        results.sort(key=lambda x: x['intensity'], reverse=True)
    
    dt = time.time() - start_time
    print(f"Detection completed in {dt:.3f} seconds.")
    print(f"Found {len(results)} candidate spots (True spots: {len(true_positions)}).")
    
    print("\n--- Detection Results ---")
    for i, c in enumerate(results[:10]):  # Show top 10
        print(f"[{i+1}] Conf={c['confidence']:.2f} | "
              f"Pos: ({c['x']*1e6:.2f}, {c['y']*1e6:.2f}) um | "
              f"Intensity: {c['intensity']:.0f} cps | "
              f"R2={c['fit_r2']:.2f}")
        
    if len(results) > 10:
        print(f"... and {len(results) - 10} more.")
        
    # Check accuracy
    print("\n--- Accuracy Check ---")
    matched = 0
    for tx, ty in true_positions:
        # Find closest detected spot
        if not results:
            break
        distances = [np.sqrt((c['x']-tx)**2 + (c['y']-ty)**2) for c in results]
        min_dist = min(distances)
        if min_dist < 0.5e-6: # within 500nm
            matched += 1
            
    print(f"Successfully detected {matched}/{len(true_positions)} true NV centers.")
    print("Algorithm accuracy: {:.1f}%".format(matched/len(true_positions)*100 if true_positions else 0))

if __name__ == "__main__":
    run_test()
