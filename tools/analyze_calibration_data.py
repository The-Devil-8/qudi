import json
import os
import glob
import numpy as np
import sys

def analyze_calibration_run(run_directory):
    manifest_path = os.path.join(run_directory, 'manifest.json')
    if not os.path.exists(manifest_path):
        print(f"Error: Could not find manifest.json in {run_directory}")
        sys.exit(1)

    print(f"--- Analyzing Calibration Run: {run_directory} ---")
    
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
        
    attempts = manifest.get('attempts', [])
    if not attempts:
        print("No attempts found in manifest.")
        return

    # Group attempts by candidate_id to check stability
    candidates = {}
    for att in attempts:
        cid = att['candidate_id']
        if cid not in candidates:
            candidates[cid] = []
        candidates[cid].append(att)

    for cid, att_list in candidates.items():
        print(f"\nCandidate: {cid} ({len(att_list)} attempts)")
        
        valid_positions = []
        r2_scores = []
        
        for i, att in enumerate(att_list):
            outcome = att.get('outcome', 'unknown')
            opt2 = att.get('optimizer2_xy', {})
            
            # Extract independent bounded re-analysis data
            success = opt2.get('success', False)
            r2 = opt2.get('r_squared', None)
            is_edge = opt2.get('is_edge_fit', False)
            pos_m = opt2.get('position_m', None)
            
            # Print attempt summary
            r2_str = f"{r2:.4f}" if r2 is not None else "N/A"
            edge_str = "YES" if is_edge else "NO"
            print(f"  Attempt {i+1}: Outcome: {outcome} | Fit Success: {success} | Edge Hit: {edge_str} | R^2: {r2_str}")
            
            if success and pos_m is not None:
                valid_positions.append(pos_m)
                r2_scores.append(r2)
                
        if len(valid_positions) > 1:
            valid_positions = np.array(valid_positions)
            median_pos = np.median(valid_positions, axis=0)
            
            # Calculate standard deviation/spread from the median position
            deviations = np.linalg.norm(valid_positions - median_pos, axis=1)
            max_spread = np.max(deviations) * 1e9  # converted to nm
            
            print(f"  -> Total Valid Fits: {len(valid_positions)}")
            print(f"  -> Median Position (X, Y): ({median_pos[0]*1e6:.4f} um, {median_pos[1]*1e6:.4f} um)")
            print(f"  -> Maximum Radial Spread: {max_spread:.2f} nm")
            
            if r2_scores:
                print(f"  -> Min R^2 observed: {min(r2_scores):.4f}")
                print(f"  -> Max R^2 observed: {max(r2_scores):.4f}")
        else:
            print("  -> Not enough successful fits to calculate repeatability spread.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_calibration_data.py <path_to_nvverify_folder>")
        print("Example: python analyze_calibration_data.py C:/Data/2026/07/16/NVCandidateVerifier/nvverify_XXXXXX")
        sys.exit(1)
        
    analyze_calibration_run(sys.argv[1])
