# -*- coding: utf-8 -*-
"""
Logic module for processing close-scan images of individual cell regions.

This module sits between the close-scan acquisition (triggered by
``ScanRegionQueue``) and the existing NV detection pipeline
(``ConfocalImageAnalysis`` → ``OptimizerLogic`` → ``PoiManagerLogic``).

It narrows down the search region within a close-scan image by:

  1. Detecting the cell interior (foreground vs diamond substrate).
  2. Detecting and masking the dark nucleus void.
  3. Masking overly bright NV cluster regions.
  4. Extracting the *processable zone* (cytoplasm minus nucleus minus
     bright clusters) — the region where single NV centres are most
     likely to be individually resolvable.

The processable-zone mask is then handed to the existing CIP +
Optimizer pipeline for actual NV detection.  This module does **not**
perform NV detection itself.

Design notes
------------
* All physical units are metres (consistent with Qudi).
* Pixel coordinates use NumPy ``(row, col)`` convention.
* The module is designed to work with variable-dimension close scans
  (e.g. 30×40, 40×25) produced by the scanner's natural behaviour.
* Intensity thresholds are MAD-based (median absolute deviation) for
  robustness against the heavy-tailed fluorescence distribution.
"""

import numpy as np
from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    binary_fill_holes,
    binary_opening,
    binary_closing,
    binary_erosion,
    binary_dilation,
    label,
    find_objects,
    distance_transform_edt,
)

try:
    from skimage.filters import threshold_otsu
    from skimage.measure import regionprops
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


# ======================================================================
# Result container
# ======================================================================

class CellProcessingResult:
    """
    Container for all outputs of the cell region processing pipeline.

    Attributes
    ----------
    cell_interior_mask : numpy.ndarray
        Boolean mask of the cell interior (foreground vs substrate).
    nucleus_mask : numpy.ndarray
        Boolean mask of the detected dark nucleus region.
    bright_cluster_mask : numpy.ndarray
        Boolean mask of overly bright NV cluster regions.
    processable_mask : numpy.ndarray
        Boolean mask of the processable cytoplasm zone (where existing
        CIP + Optimizer pipeline should search for single NVs).
    zone_stats : dict
        Statistics about the processable zone (area, mean intensity,
        fraction of cell, etc.).
    nucleus_stats : dict
        Statistics about the detected nucleus (area, centroid, etc.).
    bright_cluster_stats : list
        Per-cluster statistics (area, peak intensity, centroid).
    diagnostics : dict
        Intermediate processing data for debugging and visualization.
    """

    def __init__(self, shape):
        ny, nx = shape
        self.cell_interior_mask = np.zeros((ny, nx), dtype=bool)
        self.nucleus_mask = np.zeros((ny, nx), dtype=bool)
        self.bright_cluster_mask = np.zeros((ny, nx), dtype=bool)
        self.processable_mask = np.zeros((ny, nx), dtype=bool)
        self.zone_stats = {}
        self.nucleus_stats = {}
        self.bright_cluster_stats = []
        self.diagnostics = {}
        self.x_range = 0.0
        self.hardware_x_shift = 0.0


# ======================================================================
# Main processor
# ======================================================================

class CellRegionProcessor:
    """
    Processes close-scan images to extract the processable zone for NV
    detection.

    The processor identifies three region types within a close-scan image:

    * **Nucleus** — dark central void where nanodiamonds don't penetrate;
      excluded from NV search.
    * **Bright clusters** — overly bright NV cluster aggregations that
      cannot be resolved into individual centres; excluded.
    * **Processable zone** — mid-intensity cytoplasm where single NV
      centres are most likely individually resolvable; handed to the
      existing CIP + Optimizer pipeline.

    Typical usage::

        processor = CellRegionProcessor()
        image, ux, uy, header = seg.parse_dat_file('close_scan.dat')
        result = processor.process(image)

        # Hand processable_mask to existing NV detection pipeline:
        # auto_nv_finder.detect_candidates(image, mask=result.processable_mask)

    Parameters can be adjusted per-scan via keyword arguments to
    ``process()``.
    """

    def __init__(self):
        self.last_x_range = 0.0

    @property
    def x_range(self):
        """Return the physical X-axis range (in metres) of the most recently processed cell region."""
        return getattr(self, 'last_x_range', 0.0)

    def compute_hardware_x_shift(self, fraction=-1.0 / 20.0, x_range=None):
        """Compute temporary hardware X-shift to compensate for position discrepancy.

        Parameters
        ----------
        fraction : float, optional
            Shift fraction of the X range (default -1/20 = -0.05).
        x_range : float, optional
            Physical X-range in metres. Defaults to ``self.x_range``.

        Returns
        -------
        float
            Hardware shift in metres to be added to candidate X position.
        """
        if x_range is None:
            x_range = self.x_range
        return float(fraction * x_range)

    # ------------------------------------------------------------------
    # Main processing entry point
    # ------------------------------------------------------------------

    def process(self, image, scan_region=None,
                # Cell interior detection
                cell_bg_kernel=31,
                cell_smooth_sigma=3.0,
                cell_threshold_method='otsu',
                cell_min_area_fraction=0.05,
                # Nucleus detection
                nucleus_dark_sigma=1.0,
                nucleus_smooth_sigma=2.0,
                min_nucleus_fraction=0.03,
                max_nucleus_fraction=0.45,
                nucleus_min_compactness=0.15,
                nucleus_centrality=0.70,
                # Bright cluster detection
                mask_bright_clusters=False,
                bright_cluster_sigma=4.0,
                bright_dilate_px=2,
                min_bright_cluster_area_px=4,
                max_bright_cluster_fraction=0.35,
                # Processable zone
                zone_edge_erosion_px=2,
                zone_min_area_px=50):
        """
        Process a close-scan image through the full pipeline.

        Parameters
        ----------
        image : numpy.ndarray
            3-D image array ``(ny, nx, 4)`` from ``parse_dat_file``.
            Channel 3 is fluorescence counts/s.

        Cell interior detection
        ~~~~~~~~~~~~~~~~~~~~~~~
        cell_bg_kernel : int
            Median filter kernel for substrate background estimation.
        cell_smooth_sigma : float
            Gaussian smoothing sigma for cell detection.
        cell_threshold_method : str
            ``'otsu'`` or ``'percentile'``.
        cell_min_area_fraction : float
            Minimum cell area as fraction of total image.

        Nucleus detection
        ~~~~~~~~~~~~~~~~~
        nucleus_dark_sigma : float
            How many MAD-sigma below the cell median to threshold the
            nucleus.  Lower = more aggressive dark detection.
        nucleus_smooth_sigma : float
            Smoothing before nucleus detection (reduces noise holes).
        min_nucleus_fraction : float
            Minimum nucleus area as fraction of cell interior.
        max_nucleus_fraction : float
            Maximum nucleus area fraction.
        nucleus_min_compactness : float
            Minimum compactness (4piA/P^2) for a nucleus candidate.
        nucleus_centrality : float
            Nucleus centroid must be within this fraction of the cell
            bounding box centre.

        Bright cluster detection
        ~~~~~~~~~~~~~~~~~~~~~~~~
        mask_bright_clusters : bool
            Whether to subtract detected bright clusters from the processable
            zone (default False, to preserve single/clustered NV candidate spots
            for POIExtractor).
        bright_cluster_sigma : float
            MAD-sigma above cell median for bright cluster threshold.
        bright_dilate_px : int
            Dilation radius to capture intensity halos.
        min_bright_cluster_area_px : int
            Minimum cluster area (reject single-pixel noise).
        max_bright_cluster_fraction : float
            Maximum cluster area as fraction of cell (reject false
            whole-cell detections).

        Processable zone
        ~~~~~~~~~~~~~~~~
        zone_edge_erosion_px : int
            Erode cell boundary to avoid edge artefacts.
        zone_min_area_px : int
            Minimum processable zone area; if smaller, result is empty.

        Returns
        -------
        CellProcessingResult
            Contains all masks and statistics.
        """
        fluor = image[:, :, 3].astype(float)
        x_coords = image[0, :, 0]
        y_coords = image[:, 0, 1]
        ny, nx = fluor.shape
        result = CellProcessingResult((ny, nx))

        # Determine physical X-axis range of current Cell Region
        x_range = 0.0
        if x_coords is not None and len(x_coords) > 1:
            ptp_val = float(np.ptp(x_coords))
            if ptp_val > 0:
                x_range = ptp_val
        if x_range == 0.0 and scan_region is not None:
            if hasattr(scan_region, 'bbox_physical') and scan_region.bbox_physical is not None:
                x_min, x_max = scan_region.bbox_physical[0], scan_region.bbox_physical[1]
                x_range = float(abs(x_max - x_min))
            if x_range == 0.0 and getattr(scan_region, 'width_um', 0.0) > 0:
                x_range = float(scan_region.width_um) * 1e-6

        result.x_range = x_range
        self.last_x_range = x_range
        result.diagnostics['x_range'] = x_range

        # Temporary hardware shift (-X/20) for testing
        hardware_shift = self.compute_hardware_x_shift(fraction=-1.0 / 20.0, x_range=x_range)
        result.hardware_x_shift = hardware_shift
        result.diagnostics['hardware_x_shift'] = hardware_shift

        # --- Stage 1: Detect cell interior ---
        cell_mask = self._detect_cell_interior(
            fluor, cell_bg_kernel, cell_smooth_sigma,
            cell_threshold_method, cell_min_area_fraction,
            x_coords=x_coords, y_coords=y_coords, scan_region=scan_region
        )
        result.cell_interior_mask = cell_mask
        result.diagnostics['cell_area_px'] = int(cell_mask.sum())
        result.diagnostics['cell_area_fraction'] = (
            float(cell_mask.sum()) / (ny * nx)
        )

        if not cell_mask.any():
            # No cell found — return empty result
            result.zone_stats = {'area_px': 0, 'processable': False,
                                 'reason': 'no_cell_detected'}
            return result

        # --- Stage 2: Detect nucleus ---
        nucleus_mask, nuc_stats = self._detect_nucleus(
            fluor, cell_mask,
            nucleus_dark_sigma, nucleus_smooth_sigma,
            min_nucleus_fraction, max_nucleus_fraction,
            nucleus_min_compactness, nucleus_centrality,
        )
        result.nucleus_mask = nucleus_mask
        result.nucleus_stats = nuc_stats

        # --- Stage 3: Detect bright clusters ---
        bright_mask, cluster_stats = self._detect_bright_clusters(
            fluor, cell_mask,
            bright_cluster_sigma, bright_dilate_px,
            min_bright_cluster_area_px, max_bright_cluster_fraction,
        )
        result.bright_cluster_mask = bright_mask
        result.bright_cluster_stats = cluster_stats

        # --- Stage 4: Extract processable zone ---
        processable = self._extract_processable_zone(
            cell_mask, nucleus_mask, bright_mask,
            zone_edge_erosion_px, zone_min_area_px,
            mask_bright_clusters=mask_bright_clusters,
        )
        result.processable_mask = processable

        # Compute zone statistics
        if processable.any():
            zone_fluor = fluor[processable]
            cell_area = int(cell_mask.sum())
            result.zone_stats = {
                'area_px': int(processable.sum()),
                'area_fraction_of_cell': float(processable.sum()) / max(cell_area, 1),
                'mean_intensity': float(zone_fluor.mean()),
                'median_intensity': float(np.median(zone_fluor)),
                'std_intensity': float(zone_fluor.std()),
                'min_intensity': float(zone_fluor.min()),
                'max_intensity': float(zone_fluor.max()),
                'processable': True,
            }
        else:
            result.zone_stats = {
                'area_px': 0,
                'processable': False,
                'reason': 'processable_zone_too_small',
            }

        # Store diagnostics summary
        result.diagnostics['nucleus_area_px'] = int(nucleus_mask.sum())
        result.diagnostics['bright_cluster_area_px'] = int(bright_mask.sum())
        result.diagnostics['processable_area_px'] = int(processable.sum())
        result.diagnostics['n_bright_clusters'] = len(cluster_stats)

        return result

    # ------------------------------------------------------------------
    # Stage 1: Cell interior detection
    # ------------------------------------------------------------------

    def _detect_cell_interior(self, fluor, bg_kernel, smooth_sigma,
                              threshold_method, min_area_fraction,
                              x_coords=None, y_coords=None, scan_region=None):
        """
        Detect the cell foreground vs dark diamond substrate.

        The cell appears as an extended region of moderate-to-bright
        fluorescence on a dark background.  A large median filter
        estimates the substrate baseline; after subtraction, Gaussian
        smoothing + Otsu thresholding isolates the cell body.

        If scan_region with a macro_mask is provided, it intersects the 
        micro-level cell boundary with the interpolated macro-level boundary
        to eliminate false positives at the cell edges.

        Returns
        -------
        cell_mask : numpy.ndarray
            Boolean mask, *True* inside the cell.
        """
        ny, nx = fluor.shape

        # Background estimation
        kernel = bg_kernel if bg_kernel % 2 == 1 else bg_kernel + 1
        background = median_filter(fluor, size=kernel)
        corrected = np.maximum(fluor - background, 0.0)

        # Smooth at cell scale
        smoothed = gaussian_filter(corrected, sigma=smooth_sigma)

        # Threshold
        nonzero = smoothed[smoothed > 0]
        if len(nonzero) < 10:
            return np.zeros((ny, nx), dtype=bool)

        if threshold_method == 'otsu' and HAS_SKIMAGE:
            try:
                thresh = threshold_otsu(nonzero)
                # Safety: If the threshold is higher than the 90th percentile,
                # Otsu is being skewed by extremely bright NV centers and is 
                # missing the cell body. Fall back to percentile.
                if thresh > np.percentile(nonzero, 90):
                    thresh = np.percentile(nonzero, 60)
            except Exception:
                thresh = np.percentile(nonzero, 60)
        else:
            thresh = np.percentile(nonzero, 60)

        mask = smoothed > thresh

        # Morphological cleanup
        mask = binary_closing(mask, iterations=3)
        mask = binary_fill_holes(mask)
        mask = binary_opening(mask, iterations=2)

        # Keep only sufficiently large components
        labeled, n = label(mask)
        min_area = int(ny * nx * min_area_fraction)

        best_label = 0
        best_area = 0
        slices = find_objects(labeled)
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            area = int((labeled[sl] == lbl).sum())
            if area >= min_area and area > best_area:
                best_area = area
                best_label = lbl

        if best_label > 0:
            cell_mask = (labeled == best_label)
        else:
            # No component large enough — take the whole mask if it's
            # big enough
            if mask.sum() >= min_area:
                cell_mask = mask
            else:
                cell_mask = np.zeros((ny, nx), dtype=bool)

        # Impose macro-level cell boundary if available
        if scan_region is not None and getattr(scan_region, 'macro_mask', None) is not None:
            if x_coords is not None and y_coords is not None:
                try:
                    from scipy.interpolate import RegularGridInterpolator
                    macro_x = scan_region.macro_x_coords
                    macro_y = scan_region.macro_y_coords
                    macro_mask = scan_region.macro_mask.astype(float)
                    
                    if len(macro_x) > 1 and len(macro_y) > 1:
                        # Ensure strictly increasing coordinates for RegularGridInterpolator
                        if macro_x[0] > macro_x[-1]:
                            macro_x = macro_x[::-1]
                            macro_mask = macro_mask[:, ::-1]
                        if macro_y[0] > macro_y[-1]:
                            macro_y = macro_y[::-1]
                            macro_mask = macro_mask[::-1, :]
                            
                        interp = RegularGridInterpolator(
                            (macro_y, macro_x), macro_mask, 
                            bounds_error=False, fill_value=0.0
                        )
                        
                        # Create meshgrid of micro coords
                        YY, XX = np.meshgrid(y_coords, x_coords, indexing='ij')
                        points = np.stack((YY, XX), axis=-1)
                        interp_mask = interp(points) > 0.5
                        
                        # Intersect the micro-level boundary with the macro-level boundary
                        cell_mask = cell_mask & interp_mask
                        
                except Exception as e:
                    # In case of any interpolation error, silently fallback to micro cell_mask
                    print(f"Failed to impose macro mask: {e}")

        return cell_mask

    # ------------------------------------------------------------------
    # Stage 2: Nucleus detection
    # ------------------------------------------------------------------

    def _detect_nucleus(self, fluor, cell_mask,
                        dark_sigma, smooth_sigma,
                        min_fraction, max_fraction,
                        min_compactness, centrality):
        """
        Detect the dark nucleus void within the cell interior.

        The nucleus appears as a large, roughly central, dark region
        inside the otherwise fluorescent cell.  It has significantly
        lower intensity than the surrounding cytoplasm because
        nanodiamonds don't readily penetrate the nuclear membrane.

        Algorithm
        ---------
        1. Smooth the fluorescence within the cell to reduce noise.
        2. Compute intensity statistics within the cell mask.
        3. Threshold for dark regions: pixels below
           ``cell_median - dark_sigma × MAD_sigma``.
        4. Morphological cleanup: close gaps, fill holes.
        5. Connected component analysis: accept the largest dark
           component that satisfies size, compactness, and centrality
           constraints.
        6. Ring validation: verify that the annulus around the candidate
           has higher average intensity than its interior.

        Returns
        -------
        nucleus_mask : numpy.ndarray
            Boolean mask of the detected nucleus.
        stats : dict
            Nucleus statistics (area, centroid, mean_intensity, etc.).
        """
        ny, nx = fluor.shape
        empty_mask = np.zeros((ny, nx), dtype=bool)
        empty_stats = {'detected': False, 'reason': 'unknown'}

        cell_area = int(cell_mask.sum())
        if cell_area < 20:
            empty_stats['reason'] = 'cell_too_small'
            return empty_mask, empty_stats

        # Smooth within cell to reduce noise-induced dark holes
        smoothed = gaussian_filter(fluor, sigma=smooth_sigma)

        # Statistics within cell interior
        cell_values = smoothed[cell_mask]
        cell_median = np.median(cell_values)
        cell_mad = np.median(np.abs(cell_values - cell_median))
        cell_sigma = 1.4826 * cell_mad
        if cell_sigma <= 0:
            cell_sigma = 1.0

        # Dark threshold: pixels significantly below the cell median
        dark_thresh = cell_median - dark_sigma * cell_sigma

        # Erode cell mask slightly to avoid the dark rim caused by Gaussian smoothing
        # at the cell boundary.
        eroded_cell = binary_erosion(cell_mask, iterations=3)

        # Create dark candidate mask (within eroded cell only)
        dark_mask = (smoothed < dark_thresh) & eroded_cell

        if not dark_mask.any():
            empty_stats['reason'] = 'no_dark_pixels'
            return empty_mask, empty_stats

        # Morphological cleanup
        dark_mask = binary_closing(dark_mask, iterations=3)
        # Avoid binary_fill_holes globally as it can fill the entire cell if the boundary forms a ring
        dark_mask = binary_opening(dark_mask, iterations=1)
        dark_mask = dark_mask & cell_mask

        # Connected component analysis
        labeled, n_components = label(dark_mask)
        if n_components == 0:
            empty_stats['reason'] = 'no_dark_components'
            return empty_mask, empty_stats

        # Cell bounding box for centrality check
        cell_rows, cell_cols = np.where(cell_mask)
        cell_row_min, cell_row_max = cell_rows.min(), cell_rows.max()
        cell_col_min, cell_col_max = cell_cols.min(), cell_cols.max()
        cell_center_row = 0.5 * (cell_row_min + cell_row_max)
        cell_center_col = 0.5 * (cell_col_min + cell_col_max)
        cell_height = cell_row_max - cell_row_min + 1
        cell_width = cell_col_max - cell_col_min + 1

        min_area = int(cell_area * min_fraction)
        max_area = int(cell_area * max_fraction)

        best_candidate = None
        best_score = -1

        slices = find_objects(labeled)
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            comp = (labeled[sl] == lbl)
            area = int(comp.sum())

            # Size filter
            if area < min_area or area > max_area:
                continue

            # Compactness filter
            # Approximate perimeter from boundary pixels
            padded = np.pad(comp.astype(np.uint8), 1, mode='constant')
            eroded = (padded[1:-1, 1:-1]
                      & padded[:-2, 1:-1]
                      & padded[2:, 1:-1]
                      & padded[1:-1, :-2]
                      & padded[1:-1, 2:])
            perimeter = max(int(comp.sum() - eroded.sum()), 1)
            compactness = (4.0 * np.pi * area) / (perimeter ** 2)

            if compactness < min_compactness:
                continue

            # Centroid in full image coordinates
            rows, cols = np.where(comp)
            centroid_row = float(rows.mean()) + sl[0].start
            centroid_col = float(cols.mean()) + sl[1].start

            # Centrality check: centroid must be near the cell centre
            row_dist = abs(centroid_row - cell_center_row) / max(cell_height, 1)
            col_dist = abs(centroid_col - cell_center_col) / max(cell_width, 1)
            max_offset = (1.0 - centrality) / 2.0  # e.g. 0.15 for centrality=0.70
            if row_dist > max_offset + 0.15 or col_dist > max_offset + 0.15:
                continue

            # Ring validation: check that the annulus around the dark
            # region has higher intensity than the interior
            full_comp = (labeled == lbl)
            dilated = binary_dilation(full_comp, iterations=3)
            ring = dilated & ~full_comp & cell_mask
            if ring.any():
                interior_mean = float(fluor[full_comp].mean())
                ring_mean = float(fluor[ring].mean())
                if ring_mean <= interior_mean:
                    # Ring is not brighter than interior — not a real
                    # nucleus (may be substrate bleed-through)
                    continue
                contrast = (ring_mean - interior_mean) / max(ring_mean, 1.0)
            else:
                contrast = 0.0

            # Score: prefer larger, more compact, more central, higher
            # contrast candidates
            score = (area / max(cell_area, 1)) * compactness * (1 + contrast)
            if score > best_score:
                best_score = score
                best_candidate = {
                    'label': lbl,
                    'area': area,
                    'compactness': compactness,
                    'centroid_row': centroid_row,
                    'centroid_col': centroid_col,
                    'mean_intensity': float(fluor[full_comp].mean()),
                    'ring_mean_intensity': float(fluor[ring].mean()) if ring.any() else 0.0,
                    'contrast': contrast,
                }

        if best_candidate is None:
            empty_stats['reason'] = 'no_valid_nucleus_candidate'
            return empty_mask, empty_stats

        nucleus_mask = (labeled == best_candidate['label'])

        stats = {
            'detected': True,
            'area_px': best_candidate['area'],
            'area_fraction_of_cell': best_candidate['area'] / max(cell_area, 1),
            'compactness': best_candidate['compactness'],
            'centroid_row': best_candidate['centroid_row'],
            'centroid_col': best_candidate['centroid_col'],
            'mean_intensity': best_candidate['mean_intensity'],
            'ring_contrast': best_candidate['contrast'],
        }

        return nucleus_mask, stats

    # ------------------------------------------------------------------
    # Stage 3: Bright cluster detection
    # ------------------------------------------------------------------

    def _detect_bright_clusters(self, fluor, cell_mask,
                                cluster_sigma, dilate_px,
                                min_area_px, max_fraction):
        """
        Detect overly bright NV cluster regions within the cell.

        These are regions where NV centres are too densely packed to be
        individually resolved.  They appear as intense fluorescence
        spots (often at the cell periphery/membrane).

        Algorithm
        ---------
        1. Compute MAD-based intensity statistics within the cell mask.
        2. Threshold: pixels above ``cell_median + cluster_sigma × MAD_sigma``.
        3. Dilate to capture intensity halos around cluster cores.
        4. Connected component analysis with area filtering.

        Returns
        -------
        bright_mask : numpy.ndarray
            Boolean mask of bright cluster regions.
        cluster_stats : list of dict
            Per-cluster statistics.
        """
        ny, nx = fluor.shape
        cell_area = int(cell_mask.sum())

        if cell_area < 10:
            return np.zeros((ny, nx), dtype=bool), []

        # Statistics within cell
        cell_values = fluor[cell_mask]
        cell_median = float(np.median(cell_values))
        cell_mad = float(np.median(np.abs(cell_values - cell_median)))
        cell_sigma = 1.4826 * cell_mad
        if cell_sigma <= 0:
            cell_sigma = 1.0

        # Bright threshold
        bright_thresh = cell_median + cluster_sigma * cell_sigma
        bright_raw = (fluor > bright_thresh) & cell_mask

        if not bright_raw.any():
            return np.zeros((ny, nx), dtype=bool), []

        # Dilate to capture halos
        if dilate_px > 0:
            struct = np.ones((2 * dilate_px + 1, 2 * dilate_px + 1), dtype=bool)
            bright_dilated = binary_dilation(bright_raw, structure=struct)
            bright_dilated = bright_dilated & cell_mask  # keep within cell
        else:
            bright_dilated = bright_raw

        # Connected component analysis with filtering
        labeled, n = label(bright_dilated)
        max_area = int(cell_area * max_fraction)

        accepted_labels = set()
        cluster_stats = []
        slices = find_objects(labeled)

        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            comp = (labeled[sl] == lbl)
            area = int(comp.sum())

            if area < min_area_px:
                continue
            if area > max_area:
                continue

            full_comp = (labeled == lbl)
            comp_fluor = fluor[full_comp]

            rows, cols = np.where(full_comp)
            stats = {
                'area_px': area,
                'peak_intensity': float(comp_fluor.max()),
                'mean_intensity': float(comp_fluor.mean()),
                'centroid_row': float(rows.mean()),
                'centroid_col': float(cols.mean()),
            }
            accepted_labels.add(lbl)
            cluster_stats.append(stats)

        bright_mask = np.isin(labeled, list(accepted_labels))
        return bright_mask, cluster_stats

    # ------------------------------------------------------------------
    # Stage 4: Processable zone extraction
    # ------------------------------------------------------------------

    def _extract_processable_zone(self, cell_mask, nucleus_mask,
                                  bright_mask, edge_erosion_px,
                                  min_area_px, mask_bright_clusters=False):
        """
        Extract the processable cytoplasm zone.

        ``processable = cell_interior AND NOT nucleus`` (and optionally
        ``AND NOT bright_clusters`` if ``mask_bright_clusters=True``).

        With additional cleanup:
        * Erode cell boundary to avoid edge artefacts.
        * Remove thin strips (< 3 px wide) via morphological opening.
        * Keep only the largest connected component.

        Returns
        -------
        processable : numpy.ndarray
            Boolean mask of the processable zone.
        """
        # Start with cell interior
        zone = cell_mask.copy()

        # Erode cell edges to avoid boundary artefacts
        if edge_erosion_px > 0:
            zone = binary_erosion(zone, iterations=edge_erosion_px)

        # Subtract nucleus
        zone = zone & ~nucleus_mask

        # Subtract bright clusters only if explicitly requested
        if mask_bright_clusters and bright_mask is not None and bright_mask.any():
            zone = zone & ~bright_mask

        # Morphological cleanup: remove thin strips
        zone = binary_opening(zone, iterations=1)

        # Check minimum area
        if zone.sum() < min_area_px:
            return np.zeros_like(zone)

        # Keep largest connected component (avoid fragmentation)
        labeled, n = label(zone)
        if n <= 1:
            return zone

        # Find largest
        best_label = 0
        best_area = 0
        slices = find_objects(labeled)
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            area = int((labeled[sl] == lbl).sum())
            if area > best_area:
                best_area = area
                best_label = lbl

        if best_label > 0:
            return (labeled == best_label)
        return zone

    # ------------------------------------------------------------------
    # Convenience: generate diagnostic overlay data
    # ------------------------------------------------------------------

    def get_overlay_colors(self, result):
        """
        Generate a colour-coded overlay array for visualization.

        Returns a ``(ny, nx, 4)`` RGBA float array where:
        * Blue (0, 0, 1, 0.3) = nucleus
        * Red (1, 0, 0, 0.3) = bright clusters
        * Green (0, 1, 0, 0.3) = processable zone
        * Transparent = cell exterior / unclassified

        Parameters
        ----------
        result : CellProcessingResult
            Output of ``process()``.

        Returns
        -------
        numpy.ndarray
            RGBA overlay ``(ny, nx, 4)``, float in [0, 1].
        """
        ny, nx = result.cell_interior_mask.shape
        overlay = np.zeros((ny, nx, 4), dtype=float)

        # Processable zone: green
        overlay[result.processable_mask, 1] = 1.0
        overlay[result.processable_mask, 3] = 0.3

        # Nucleus: blue
        overlay[result.nucleus_mask, 2] = 1.0
        overlay[result.nucleus_mask, 3] = 0.3

        # Excluded bright clusters: red (only for clusters actually excluded from processable zone)
        excluded_bright = result.bright_cluster_mask & ~result.processable_mask
        if excluded_bright.any():
            overlay[excluded_bright, 0] = 1.0
            overlay[excluded_bright, 1] = 0.0
            overlay[excluded_bright, 3] = 0.3

        return overlay
