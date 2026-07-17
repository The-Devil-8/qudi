import sys
import time
import rpyc

import json

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Set these to the coordinates of your actual, stable reference spot!
# You can find these by manually optimizing a bright spot in Qudi.
BASE_X = 109.92343077292014e-6
BASE_Y = 86.95113854439189e-6
BASE_Z = 19.60795420850365e-6

# -----------------------------------------------------------------------------
# Connection
# -----------------------------------------------------------------------------
print("Connecting to running Qudi instance...")
try:
    conn = rpyc.connect('localhost', 12345, config={'allow_all_attrs': True})
    manager = conn.root
except Exception as e:
    print(f"Failed to connect to Qudi: {e}")
    print("Ensure Qudi is running with the 'module_server' configured and active.")
    sys.exit(1)

try:
    verifier = manager.getModule('nv_candidate_verifier')
except Exception as e:
    print("Failed to get 'nv_candidate_verifier' module. Check your config.")
    sys.exit(1)

# -----------------------------------------------------------------------------
# Generate grid of seed offsets for the calibration series
# -----------------------------------------------------------------------------
# The default optimizer pitch for resolution=10 over 0.6um is ~66.7nm.
# We test offsets to see how the optimizer behaves when not perfectly centered.
pitch = 66.7e-9  
offsets = [
    (0, 0),                   # Centered
    (pitch/2, 0),             # +X sub-pixel shift
    (-pitch/2, 0),            # -X sub-pixel shift
    (0, pitch/2),             # +Y sub-pixel shift
    (0, -pitch/2),            # -Y sub-pixel shift
    (pitch, pitch),           # +XY shift
    (-pitch, -pitch),         # -XY shift
    (pitch * 2, pitch * 2),   # Large shift
]

candidates = []
candidate_idx = 1

# Repeat each offset condition 3 times to check for stability
for offset_x, offset_y in offsets:
    for attempt in range(3):
        candidates.append({
            'candidate_id': f'calib_p{candidate_idx:03d}',
            'x': BASE_X + offset_x,
            'y': BASE_Y + offset_y,
            'z_estimate': BASE_Z
        })
        candidate_idx += 1

# -----------------------------------------------------------------------------
# Run context metadata (saved to manifest.json)
# -----------------------------------------------------------------------------
run_context = {
    'operator': 'user',
    'calibration_series': 'seed-offset-calibration',
    'base_x': BASE_X,
    'base_y': BASE_Y,
    'base_z': BASE_Z,
    'note': 'Replace BASE_X, BASE_Y, BASE_Z with your actual reference spot in the script.'
}

# -----------------------------------------------------------------------------
# Submit Batch
# -----------------------------------------------------------------------------
print(f"Submitting {len(candidates)} candidates for diagnostic verification...")

try:
    # Convert local lists/dicts to strings, then load them natively on the Qudi side.
    # This prevents RPyC from creating netrefs (transparent proxies) back to this script,
    # meaning this script can exit immediately without breaking Qudi's background processing.
    json_module = conn.modules.json
    server_candidates = json_module.loads(json.dumps(candidates))
    server_run_context = json_module.loads(json.dumps(run_context))

    run_id = verifier.verify_batch(server_candidates, run_context=server_run_context)
    print(f"Batch successfully started with run_id: {run_id}")
    print("The verifier is now running these independently in the background.")
    print("Check the Qudi SaveLogic output directory (usually under NVCandidateVerifier/) for the results.")
    print("This script will now exit; Qudi will continue running the scans.")
except Exception as e:
    print(f"Failed to start verification batch: {e}")
    sys.exit(1)
