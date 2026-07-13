import rpyc
import time
import sys

print("Connecting to running Qudi instance...")
try:
    conn = rpyc.connect('localhost', 12345)
    manager = conn.root
except Exception as e:
    print(f"Failed to connect to Qudi: {e}")
    print("Make sure Qudi is running with the module_server enabled.")
    sys.exit(1)

print("Getting modules...")
finder = manager.getModule('auto_nv_finder_logic')
scanner = manager.getModule('scannerlogic')
poi_manager = manager.getModule('poimanagerlogic')

print("Configuring scan...")
# Set scan parameters: 20x20 um, 50x50 pixels, fast integration
scanner.set_scanner_limits(x_start=0, x_stop=20e-6, y_start=0, y_stop=20e-6)
scanner.set_scanner_pixel_count(x_pixels=50, y_pixels=50)
scanner.set_scanner_integration_time(time=0.005) # 5ms per pixel

print("Starting scan...")
scanner.start_scan_xy()

# Wait for scan to finish
while scanner.scan_is_running():
    time.sleep(1)
    sys.stdout.write('.')
    sys.stdout.flush()
print("\nScan completed.")

print("Configuring Auto NV Finder...")
finder.set_threshold(3.0)
finder.set_min_intensity(50000) # Dummy NVs are bright (~400k cps)
finder.set_spot_diameter(1.5e-6)
finder.set_auto_register_pois(True)
finder.set_z_optimization(False) # Skip Z to make it faster for testing

print("Starting Auto NV Finder...")
finder.start_auto_find()

# Wait for finder to finish
while finder.is_running:
    time.sleep(1)
    
print("\nAuto NV Finder completed.")
print(f"Results summary: {finder.results_summary}")

print("\nRegistered POIs:")
pois = poi_manager.poi_names
nv_pois = [p for p in pois if "NV_" in p]
if not nv_pois:
    print("No NV POIs were registered. :(")
else:
    for name in nv_pois:
        pos = poi_manager.get_poi_position(name)
        print(f"  {name}: X={pos[0]*1e6:.2f} um, Y={pos[1]*1e6:.2f} um, Z={pos[2]*1e6:.2f} um")

print("\nDone.")
