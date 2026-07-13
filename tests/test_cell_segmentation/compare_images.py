import os
import sys
import matplotlib.image as mpimg
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

def analyze_diff():
    # Load both images
    img_orig = mpimg.imread(r"d:\qudi-working\qudi\tests\test_cell_segmentation\output\original_image.png")
    img_ref = mpimg.imread(r"d:\qudi-working\qudi\Confocal\20260615-1140-42_confocal_xy_image_Dev1Ctr3_fig.png")
    
    print("img_orig shape:", img_orig.shape)
    print("img_ref shape:", img_ref.shape)
    
    # Check if they are transposed, flipped, etc.
    # Since they are figures, they include axes, labels, etc. So the raw data might be exactly the same but the plot is different.
    # Let's check the pixel values of the raw data.
    # Load the dat files directly
    
    dat_ref = np.loadtxt(r"d:\qudi-working\qudi\Confocal\20260615-1140-42_confocal_xy_image_Dev1Ctr3.dat")
    print("dat_ref shape:", dat_ref.shape)
    
    # Load the parsed data
    from logic.image_rebuild_logic import ImageRebuildLogic
    logic = ImageRebuildLogic()
    image, ux, uy = logic.load_dat_file(r"d:\qudi-working\qudi\Confocal\20260615-1140-42_confocal_xy_data.dat")
    dat_parsed = image[:, :, 3]
    
    print("dat_parsed shape:", dat_parsed.shape)
    
    diff = np.max(np.abs(dat_ref - dat_parsed))
    print("Max diff between raw data matrices:", diff)
    
    if diff == 0:
        print("The raw matrices match exactly. The visual difference is purely in matplotlib rendering (e.g., axes, colorbar, DPI, or aspect ratio).")
    else:
        # Check if transposed
        diff_T = np.max(np.abs(dat_ref - dat_parsed.T))
        print("Max diff if transposed:", diff_T)
        
        # Check if flipped UD
        diff_UD = np.max(np.abs(dat_ref - np.flipud(dat_parsed)))
        print("Max diff if flipped UD:", diff_UD)
        
        # Check if flipped LR
        diff_LR = np.max(np.abs(dat_ref - np.fliplr(dat_parsed)))
        print("Max diff if flipped LR:", diff_LR)
        
if __name__ == "__main__":
    analyze_diff()
