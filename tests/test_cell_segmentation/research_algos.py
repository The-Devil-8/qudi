import numpy as np
from scipy.ndimage import (
    gaussian_filter, median_filter, binary_fill_holes,
    binary_opening, binary_closing, binary_erosion, label
)
from skimage.filters import threshold_otsu, sobel
from skimage.feature import peak_local_max
from skimage.segmentation import watershed
from skimage.morphology import disk
from scipy.ndimage import distance_transform_edt

import sys
import os
# Ensure qudi is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from logic.roi_segmentation_logic import ROISegmentationLogic

class BaselineSegmentation(ROISegmentationLogic):
    """Algo 0: The current working intensity-based watershed logic."""
    def __init__(self):
        super().__init__()
        self.name = "Baseline (Intensity Watershed)"

class DistanceTransformSegmentation(ROISegmentationLogic):
    """Algo 1: Normalization + Distance Transform Watershed."""
    def __init__(self):
        super().__init__()
        self.name = "Algo 1 (Distance Transform)"

    def segment_roi(self, image, **kwargs):
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        
        # 1. Normalization: Remove extreme bright NV spots by clipping to 95th percentile
        p95 = np.percentile(fluor, 95)
        normalized = np.clip(fluor, a_min=None, a_max=p95)
        
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
        
        # Pre-cleanup
        raw_mask = binary_closing(raw_mask, iterations=2)
        raw_mask = binary_fill_holes(raw_mask)
        raw_mask = binary_opening(raw_mask, iterations=1)
        
        # --- Distance Transform Watershed ---
        # Instead of smoothed intensity, we compute Euclidean distance to background
        distance = distance_transform_edt(raw_mask)
        
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2
        min_area_um2 = kwargs.get('min_cell_area_um2', 50.0)
        min_cell_area_px = max(1, int(min_area_um2 / pixel_area_um2)) if pixel_area_um2 > 0 else 50
        min_dist_px = max(3, int(0.5 * np.sqrt(min_cell_area_px / np.pi)))
        
        # Find peaks in distance transform
        coords = peak_local_max(distance, min_distance=min_dist_px, labels=raw_mask)
        mask_coords = np.zeros(distance.shape, dtype=bool)
        if coords.size > 0:
            mask_coords[tuple(coords.T)] = True
            
        markers, _ = label(mask_coords)
        
        # Watershed on inverted distance map
        labeled_all = watershed(-distance, markers, mask=raw_mask)
        
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
            'raw_mask': raw_mask, # For visualization
            'topography': distance # For visualization
        }

class GradientEnhancedSegmentation(ROISegmentationLogic):
    """Algo 2: Normalization + Sobel Gradient Watershed."""
    def __init__(self):
        super().__init__()
        self.name = "Algo 2 (Gradient Enhanced)"

    def segment_roi(self, image, **kwargs):
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape
        
        # 1. Normalization: Remove extreme bright NV spots
        p95 = np.percentile(fluor, 95)
        normalized = np.clip(fluor, a_min=None, a_max=p95)
        
        # Background subtraction
        bg_kernel = kwargs.get('background_kernel', 51)
        background = median_filter(normalized, size=bg_kernel)
        corrected = np.maximum(normalized - background, 0.0)
        
        # Despike and VERY strong smooth for gradient calculation
        despiked = median_filter(corrected, size=kwargs.get('despike_kernel', 7))
        # Use slightly stronger sigma to prevent noise edges
        smooth_sigma = kwargs.get('smooth_sigma', 6.0) * 1.5 
        smoothed = gaussian_filter(despiked, sigma=smooth_sigma)
        
        # Edge Map Generation
        gradient_mag = sobel(smoothed)
        
        # Thresholding for mask
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
        
        # --- Gradient Watershed ---
        # Create markers by aggressively eroding the mask so they are strictly inside the cells
        pixel_size = self.estimate_pixel_size(image)
        pixel_area_um2 = (pixel_size * 1e6) ** 2
        min_area_um2 = kwargs.get('min_cell_area_um2', 50.0)
        min_cell_area_px = max(1, int(min_area_um2 / pixel_area_um2)) if pixel_area_um2 > 0 else 50
        min_dist_px = max(3, int(0.5 * np.sqrt(min_cell_area_px / np.pi)))
        
        markers = binary_erosion(raw_mask, iterations=min_dist_px)
        markers, _ = label(markers)
        
        # If erosion killed all markers, fallback to distance peaks
        if np.max(markers) == 0:
            distance = distance_transform_edt(raw_mask)
            coords = peak_local_max(distance, min_distance=min_dist_px, labels=raw_mask)
            mask_coords = np.zeros(distance.shape, dtype=bool)
            if coords.size > 0:
                mask_coords[tuple(coords.T)] = True
            markers, _ = label(mask_coords)
            
        # Watershed on gradient magnitude
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
            'raw_mask': raw_mask, # For visualization
            'topography': gradient_mag # For visualization
        }
