import numpy as np
from scipy.ndimage import (
    gaussian_filter, median_filter, binary_fill_holes,
    binary_opening, binary_closing, binary_erosion, label, grey_opening
)
from skimage.filters import threshold_otsu, sobel
from skimage.feature import peak_local_max
from skimage.segmentation import watershed, morphological_chan_vese
from skimage.morphology import disk, h_maxima
from scipy.ndimage import distance_transform_edt
from skimage.restoration import inpaint_biharmonic

import sys
import os
# Ensure qudi is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from logic.roi_segmentation_logic import ROISegmentationLogic


class DistanceTransformSegmentation(ROISegmentationLogic):
    """Algo 1: Normalization + Smoothed Distance Transform Watershed."""
    def __init__(self):
        super().__init__()
        self.name = "Algo 1 (Dist Transform Tuned)"

    def segment_roi(self, image, **kwargs):
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        
        # 1. Normalization
        normalized = grey_opening(fluor, size=(9, 9))
        
        # Background subtraction
        bg_kernel = kwargs.get('background_kernel', 51)
        background = median_filter(normalized, size=bg_kernel)
        corrected = np.maximum(normalized - background, 0.0)
        
        # Despike and smooth
        despiked = median_filter(corrected, size=kwargs.get('despike_kernel', 7))
        smoothed = gaussian_filter(despiked, sigma=kwargs.get('smooth_sigma', 6.0))
        
        # Thresholding
        nonzero_vals = smoothed[smoothed > 0]
        if len(nonzero_vals) > 10:
            thresh = threshold_otsu(nonzero_vals)
        else:
            thresh = 0.0
            
        noise_med = np.median(normalized - background)
        noise_mad = np.median(np.abs((normalized - background) - noise_med))
        thresh = max(thresh, 0.5 * 1.4826 * noise_mad)
        
        raw_mask = smoothed > thresh
        raw_mask = binary_closing(raw_mask, iterations=2)
        raw_mask = binary_fill_holes(raw_mask)
        raw_mask = binary_opening(raw_mask, iterations=1)
        
        # --- TUNED Distance Transform Watershed ---
        distance = distance_transform_edt(raw_mask)
        
        # TUNE 1: Smooth the distance map to merge shallow bulges
        distance_smoothed = gaussian_filter(distance, sigma=2.0)
        
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2
        min_area_um2 = kwargs.get('min_cell_area_um2', 50.0)
        min_cell_area_px = max(1, int(min_area_um2 / pixel_area_um2)) if pixel_area_um2 > 0 else 50
        
        # TUNE 2: Increase minimum distance between peaks
        min_dist_px = max(5, int(0.7 * np.sqrt(min_cell_area_px / np.pi)))
        
        coords = peak_local_max(distance_smoothed, min_distance=min_dist_px, labels=raw_mask)
        mask_coords = np.zeros(distance.shape, dtype=bool)
        if coords.size > 0:
            mask_coords[tuple(coords.T)] = True
            
        markers, _ = label(mask_coords)
        labeled_all = watershed(-distance_smoothed, markers, mask=raw_mask)
        
        # Properties and filtering
        component_props = self.compute_component_properties(labeled_all, fluor)
        max_cell_area_px = int(ny * nx * kwargs.get('max_cell_fraction', 0.7))
        min_compactness = kwargs.get('min_compactness', 0.05)
        
        accepted_labels = set()
        for prop in component_props:
            if min_cell_area_px <= prop['area'] <= max_cell_area_px and prop['compactness'] >= min_compactness:
                accepted_labels.add(prop['label'])
                
        roi_mask = np.isin(labeled_all, list(accepted_labels))
        component_labels = np.where(roi_mask, labeled_all, 0)
        
        return {
            'roi_mask': roi_mask,
            'component_labels': component_labels,
            'raw_mask': raw_mask, 
            'topography': distance_smoothed 
        }

class GradientEnhancedSegmentation(ROISegmentationLogic):
    """Algo 2: Gradient Watershed using Distance Transform Markers."""
    def __init__(self):
        super().__init__()
        self.name = "Algo 2 (Gradient Tuned)"

    def segment_roi(self, image, **kwargs):
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        
        normalized = grey_opening(fluor, size=(9, 9))
        background = median_filter(normalized, size=kwargs.get('background_kernel', 51))
        corrected = np.maximum(normalized - background, 0.0)
        
        # Thresholding mask
        despiked = median_filter(corrected, size=kwargs.get('despike_kernel', 7))
        mask_smoothed = gaussian_filter(despiked, sigma=kwargs.get('smooth_sigma', 6.0))
        
        nonzero_vals = mask_smoothed[mask_smoothed > 0]
        thresh = threshold_otsu(nonzero_vals) if len(nonzero_vals) > 10 else 0.0
        noise_med = np.median(normalized - background)
        noise_mad = np.median(np.abs((normalized - background) - noise_med))
        thresh = max(thresh, 0.5 * 1.4826 * noise_mad)
        
        raw_mask = mask_smoothed > thresh
        raw_mask = binary_closing(raw_mask, iterations=2)
        raw_mask = binary_fill_holes(raw_mask)
        raw_mask = binary_opening(raw_mask, iterations=1)
        
        # TUNE 1: Get robust markers from smoothed distance transform
        distance = gaussian_filter(distance_transform_edt(raw_mask), sigma=2.0)
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2
        min_cell_area_px = max(1, int(kwargs.get('min_cell_area_um2', 50.0) / pixel_area_um2)) if pixel_area_um2 > 0 else 50
        min_dist_px = max(5, int(0.7 * np.sqrt(min_cell_area_px / np.pi)))
        
        coords = peak_local_max(distance, min_distance=min_dist_px, labels=raw_mask)
        mask_coords = np.zeros(distance.shape, dtype=bool)
        if coords.size > 0:
            mask_coords[tuple(coords.T)] = True
        markers, _ = label(mask_coords)
        
        # TUNE 2: Compute gradient on a LIGHTLY smoothed image so boundaries stay sharp
        light_smoothed = gaussian_filter(despiked, sigma=2.0)
        gradient_mag = sobel(light_smoothed)
        
        # Watershed on gradient using accurate markers
        labeled_all = watershed(gradient_mag, markers, mask=raw_mask)
        
        # Properties and filtering
        component_props = self.compute_component_properties(labeled_all, fluor)
        max_cell_area_px = int(ny * nx * kwargs.get('max_cell_fraction', 0.7))
        min_compactness = kwargs.get('min_compactness', 0.05)
        
        accepted_labels = set()
        for prop in component_props:
            if min_cell_area_px <= prop['area'] <= max_cell_area_px and prop['compactness'] >= min_compactness:
                accepted_labels.add(prop['label'])
                
        roi_mask = np.isin(labeled_all, list(accepted_labels))
        component_labels = np.where(roi_mask, labeled_all, 0)
        
        return {
            'roi_mask': roi_mask,
            'component_labels': component_labels,
            'raw_mask': raw_mask,
            'topography': gradient_mag
        }

class InpaintingActiveContourSegmentation(ROISegmentationLogic):
    """Algo 3: Inpainting Top 5-10% + Smooth Intensity Watershed."""
    def __init__(self):
        super().__init__()
        self.name = "Algo 3 (Inpaint+SmoothBoundary)"

    def segment_roi(self, image, **kwargs):
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        
        # 1. Mask top 5% intensity pixels (extreme bright spots)
        p95 = np.percentile(fluor, 95)
        nv_mask = fluor > p95
        
        # 2. Inpaint biharmonic to mathematically erase and fill them smoothly
        # This creates a perfectly continuous cell body glow
        inpainted = inpaint_biharmonic(fluor, nv_mask)
        
        # Background subtraction
        bg_kernel = kwargs.get('background_kernel', 51)
        background = median_filter(inpainted, size=bg_kernel)
        corrected = np.maximum(inpainted - background, 0.0)
        
        # Smoothing
        smoothed = gaussian_filter(corrected, sigma=kwargs.get('smooth_sigma', 6.0))
        
        # Thresholding for mask
        nonzero_vals = smoothed[smoothed > 0]
        thresh = threshold_otsu(nonzero_vals) if len(nonzero_vals) > 10 else 0.0
        noise_med = np.median(inpainted - background)
        noise_mad = np.median(np.abs((inpainted - background) - noise_med))
        thresh = max(thresh, 0.5 * 1.4826 * noise_mad)
        
        raw_mask = smoothed > thresh
        
        # Morphological Active Contour to get continuous, smooth mask boundary
        # We use the raw_mask as the init level set and evolve it slightly
        active_contour_mask = morphological_chan_vese(corrected, num_iter=15, init_level_set=raw_mask, smoothing=3)
        
        # Because inpainting makes the cell fluorescence itself perfectly smooth, 
        # we can use the true fluorescence intensity directly for peak finding and watershed!
        intensity_smoothed = gaussian_filter(inpainted, sigma=8.0)
        
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2
        min_cell_area_px = max(1, int(kwargs.get('min_cell_area_um2', 50.0) / pixel_area_um2)) if pixel_area_um2 > 0 else 50
        min_dist_px = max(5, int(0.7 * np.sqrt(min_cell_area_px / np.pi)))
        
        coords = peak_local_max(intensity_smoothed, min_distance=min_dist_px, labels=active_contour_mask)
        mask_coords = np.zeros(intensity_smoothed.shape, dtype=bool)
        if coords.size > 0:
            mask_coords[tuple(coords.T)] = True
            
        markers, _ = label(mask_coords)
        
        # Watershed on the inverse smooth true intensity
        labeled_all = watershed(-intensity_smoothed, markers, mask=active_contour_mask)
        
        # Properties and filtering
        component_props = self.compute_component_properties(labeled_all, fluor)
        max_cell_area_px = int(ny * nx * kwargs.get('max_cell_fraction', 0.7))
        min_compactness = kwargs.get('min_compactness', 0.05)
        
        accepted_labels = set()
        for prop in component_props:
            if min_cell_area_px <= prop['area'] <= max_cell_area_px and prop['compactness'] >= min_compactness:
                accepted_labels.add(prop['label'])
                
        roi_mask = np.isin(labeled_all, list(accepted_labels))
        component_labels = np.where(roi_mask, labeled_all, 0)
        
        return {
            'roi_mask': roi_mask,
            'component_labels': component_labels,
            'raw_mask': active_contour_mask, 
            'topography': intensity_smoothed 
        }
