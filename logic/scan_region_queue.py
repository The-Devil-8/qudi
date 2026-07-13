# -*- coding: utf-8 -*-
"""
Logic module for managing a queue of scan regions extracted from ROI
segmentation.

This module bridges the gap between wide-field ROI segmentation
(``ROISegmentationLogic``) and close-scan acquisition by:

  1. Extracting bounding boxes from segmentation masks.
  2. Separating coupled / touching cell regions into independent ROIs.
  3. Filtering false-positive regions (too small, too dim, etc.).
  4. Maintaining a priority queue of regions for close scanning.
  5. Computing scanner FOV parameters for each region.
  6. Storing cropped ROI thumbnail images for GUI display.
  7. Tracking each region's state through the scanning pipeline.

Design notes
------------
* All physical coordinates are in **metres** (consistent with Qudi).
* Pixel coordinates follow NumPy ``(row, col)`` convention where
  ``row`` corresponds to the Y axis and ``col`` to X.
* The ``segment_roi()`` result from ``ROISegmentationLogic`` is the
  primary input.  This module adds bbox extraction and queue management
  on top, without modifying the segmentation pipeline itself.
* Coupled / touching cells that form a single connected component in
  the ROI mask are split using a watershed-based separation so that
  each cell is queued and processed independently.
"""

import json
import uuid
import numpy as np

from scipy.ndimage import (
    label,
    find_objects,
    binary_erosion,
    binary_dilation,
    distance_transform_edt,
)

try:
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


# ======================================================================
# Data structures
# ======================================================================

class ScanRegion:
    """
    A single region extracted from ROI segmentation, queued for close scanning.

    Attributes
    ----------
    region_id : str
        Unique identifier (e.g. ``'R-a1b2c3'``).
    bbox_physical : tuple of float
        ``(x_min, x_max, y_min, y_max)`` in metres.
    bbox_pixels : tuple of int
        ``(row_min, row_max, col_min, col_max)`` in the parent image.
    width_um : float
        Physical width in µm.
    height_um : float
        Physical height in µm.
    area_um2 : float
        Physical area in µm².
    centroid_physical : tuple of float
        ``(x_center, y_center)`` in metres.
    peak_intensity : float
        Maximum fluorescence count rate inside the region (Hz / counts·s⁻¹).
    mean_intensity : float
        Mean fluorescence count rate inside the region.
    parent_scan_id : str
        Identifier of the parent wide-field scan (typically the .dat
        filename timestamp, e.g. ``'20260705-1517-07'``).
    cropped_image : numpy.ndarray or None
        Cropped fluorescence sub-image from the parent scan
        (2-D float array, counts/s).  Used for GUI thumbnails.
    status : str
        One of ``'queued'``, ``'scanning'``, ``'processed'``,
        ``'skipped'``, ``'failed'``.
    priority : float
        Higher values are scanned first.
    close_scan_data : numpy.ndarray or None
        Close-scan image data, populated after scanning.
    nv_candidates_found : int
        Number of NV candidates found in this region (post-processing).
    pois_registered : int
        Number of POIs registered from this region.
    processing_notes : str
        Free-form notes / issues encountered.
    """

    def __init__(self, region_id=None, bbox_physical=None, bbox_pixels=None,
                 width_um=0.0, height_um=0.0, area_um2=0.0,
                 centroid_physical=None, peak_intensity=0.0,
                 mean_intensity=0.0, parent_scan_id='',
                 cropped_image=None):
        self.region_id = region_id or f'R-{uuid.uuid4().hex[:8]}'
        self.bbox_physical = bbox_physical or (0.0, 0.0, 0.0, 0.0)
        self.bbox_pixels = bbox_pixels or (0, 0, 0, 0)
        self.width_um = width_um
        self.height_um = height_um
        self.area_um2 = area_um2
        self.centroid_physical = centroid_physical or (0.0, 0.0)
        self.peak_intensity = peak_intensity
        self.mean_intensity = mean_intensity
        self.parent_scan_id = parent_scan_id
        self.cropped_image = cropped_image

        # State tracking
        self.status = 'queued'
        self.priority = 0.0
        self.close_scan_data = None
        self.close_scan_path = ''

        # Processing results
        self.nv_candidates_found = 0
        self.pois_registered = 0
        self.processing_notes = ''

    def __repr__(self):
        return (f'ScanRegion({self.region_id}, '
                f'{self.width_um:.1f}×{self.height_um:.1f} µm, '
                f'status={self.status}, priority={self.priority:.1f})')

    def to_dict(self):
        """Serialize to a JSON-compatible dictionary.

        Note: ``cropped_image`` and ``close_scan_data`` are excluded
        (large numpy arrays are stored separately if needed).
        """
        return {
            'region_id': self.region_id,
            'bbox_physical': list(self.bbox_physical),
            'bbox_pixels': list(self.bbox_pixels),
            'width_um': self.width_um,
            'height_um': self.height_um,
            'area_um2': self.area_um2,
            'centroid_physical': list(self.centroid_physical),
            'peak_intensity': self.peak_intensity,
            'mean_intensity': self.mean_intensity,
            'parent_scan_id': self.parent_scan_id,
            'status': self.status,
            'priority': self.priority,
            'close_scan_path': self.close_scan_path,
            'nv_candidates_found': self.nv_candidates_found,
            'pois_registered': self.pois_registered,
            'processing_notes': self.processing_notes,
        }

    @classmethod
    def from_dict(cls, d):
        """Deserialize from a dictionary."""
        region = cls(
            region_id=d.get('region_id'),
            bbox_physical=tuple(d.get('bbox_physical', (0, 0, 0, 0))),
            bbox_pixels=tuple(d.get('bbox_pixels', (0, 0, 0, 0))),
            width_um=d.get('width_um', 0.0),
            height_um=d.get('height_um', 0.0),
            area_um2=d.get('area_um2', 0.0),
            centroid_physical=tuple(d.get('centroid_physical', (0, 0))),
            peak_intensity=d.get('peak_intensity', 0.0),
            mean_intensity=d.get('mean_intensity', 0.0),
            parent_scan_id=d.get('parent_scan_id', ''),
        )
        region.status = d.get('status', 'queued')
        region.priority = d.get('priority', 0.0)
        region.close_scan_path = d.get('close_scan_path', '')
        region.nv_candidates_found = d.get('nv_candidates_found', 0)
        region.pois_registered = d.get('pois_registered', 0)
        region.processing_notes = d.get('processing_notes', '')
        return region


# ======================================================================
# Main queue class
# ======================================================================

class ScanRegionQueue:
    """
    Manages a priority queue of ROI regions for close scanning.

    Typical usage::

        from logic.roi_segmentation_logic import ROISegmentationLogic

        seg = ROISegmentationLogic()
        image, ux, uy, header = seg.parse_dat_file('scan.dat')
        result = seg.segment_roi(image)

        queue = ScanRegionQueue()
        queue.extract_regions_from_segmentation(
            segmentation_result=result,
            image=image,
            x_coords=ux,
            y_coords=uy,
            parent_scan_id='20260705-1517-07',
        )
        queue.filter_false_positives()
        queue.prioritize_queue()

        while queue.has_queued_regions():
            region = queue.get_next_region()
            scan_params = queue.compute_scan_parameters(region)
            # ... trigger scanner with scan_params ...
            queue.mark_region_status(region.region_id, 'processed')
    """

    def __init__(self):
        self._regions = []            # List[ScanRegion]
        self._region_index = {}       # region_id -> index in _regions

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def regions(self):
        """Return a copy of the regions list."""
        return list(self._regions)

    @property
    def queued_regions(self):
        """Return regions with status 'queued', sorted by priority."""
        return sorted(
            [r for r in self._regions if r.status == 'queued'],
            key=lambda r: r.priority,
            reverse=True,
        )

    @property
    def total_count(self):
        return len(self._regions)

    @property
    def queued_count(self):
        return sum(1 for r in self._regions if r.status == 'queued')

    @property
    def processed_count(self):
        return sum(1 for r in self._regions if r.status == 'processed')

    # ------------------------------------------------------------------
    # Region extraction
    # ------------------------------------------------------------------

    def extract_regions_from_segmentation(self, segmentation_result, image,
                                          x_coords, y_coords,
                                          parent_scan_id='',
                                          separate_touching=True):
        """
        Extract bounding-box regions from an ROI segmentation result.

        Parameters
        ----------
        segmentation_result : dict
            Output of ``ROISegmentationLogic.segment_roi()``.  Must
            contain at least ``'roi_mask'`` (bool 2-D array).
        image : numpy.ndarray
            The 3-D image array ``(ny, nx, 4)`` from ``parse_dat_file``.
            Channel 3 is fluorescence counts.
        x_coords : numpy.ndarray
            1-D array of unique X positions (metres), length ``nx``.
        y_coords : numpy.ndarray
            1-D array of unique Y positions (metres), length ``ny``.
        parent_scan_id : str
            Identifier for the parent scan (e.g. timestamp).
        separate_touching : bool
            If *True*, attempt to separate coupled/touching cells that
            appear as a single connected component.

        Returns
        -------
        int
            Number of regions extracted (before false-positive filtering).
        """
        roi_mask = segmentation_result.get('roi_mask', None)
        if roi_mask is None or not np.any(roi_mask):
            self._regions = []
            self._region_index = {}
            return 0

        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape

        # Compute pixel sizes from coordinate arrays
        pixel_size_x = abs(x_coords[-1] - x_coords[0]) / max(nx - 1, 1)
        pixel_size_y = abs(y_coords[-1] - y_coords[0]) / max(ny - 1, 1)

        # Label connected components in the ROI mask
        labeled_mask, n_components = label(roi_mask)

        # Optionally split coupled / touching cells
        if separate_touching and HAS_SKIMAGE:
            labeled_mask, n_components = self._separate_touching_cells(
                labeled_mask, n_components, fluor, roi_mask,
                pixel_size_x, pixel_size_y,
            )

        # Extract regions from each labelled component
        self._regions = []
        self._region_index = {}
        slices = find_objects(labeled_mask)

        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            component_mask = (labeled_mask[sl] == lbl)
            area_px = int(component_mask.sum())
            if area_px == 0:
                continue

            # Pixel bounding box (in parent image coordinates)
            row_min, row_max = sl[0].start, sl[0].stop - 1
            col_min, col_max = sl[1].start, sl[1].stop - 1

            # Physical bounding box (metres)
            x_min = x_coords[col_min] if col_min < nx else x_coords[-1]
            x_max = x_coords[min(col_max, nx - 1)]
            y_min = y_coords[row_min] if row_min < ny else y_coords[-1]
            y_max = y_coords[min(row_max, ny - 1)]

            width_m = abs(x_max - x_min)
            height_m = abs(y_max - y_min)
            width_um = width_m * 1e6
            height_um = height_m * 1e6
            area_um2 = width_um * height_um

            # Centroid (physical)
            rows, cols = np.where(labeled_mask == lbl)
            centroid_row = float(rows.mean())
            centroid_col = float(cols.mean())
            centroid_x = np.interp(centroid_col, np.arange(nx), x_coords)
            centroid_y = np.interp(centroid_row, np.arange(ny), y_coords)

            # Intensity stats inside the component
            region_fluor = fluor[labeled_mask == lbl]
            peak_intensity = float(region_fluor.max())
            mean_intensity = float(region_fluor.mean())

            # Crop the fluorescence image for GUI thumbnails
            cropped = fluor[sl].copy()
            cropped[~component_mask] = 0.0

            region = ScanRegion(
                bbox_physical=(x_min, x_max, y_min, y_max),
                bbox_pixels=(row_min, row_max, col_min, col_max),
                width_um=width_um,
                height_um=height_um,
                area_um2=area_um2,
                centroid_physical=(centroid_x, centroid_y),
                peak_intensity=peak_intensity,
                mean_intensity=mean_intensity,
                parent_scan_id=parent_scan_id,
                cropped_image=cropped,
            )
            self._regions.append(region)
            self._region_index[region.region_id] = len(self._regions) - 1

        return len(self._regions)

    # ------------------------------------------------------------------
    # Touching cell separation
    # ------------------------------------------------------------------

    def _separate_touching_cells(self, labeled_mask, n_components,
                                 fluor, roi_mask, pixel_size_x,
                                 pixel_size_y):
        """
        Split connected components that likely contain multiple cells.

        Uses a marker-controlled watershed on the distance transform of
        each large component.  Only splits components whose bounding box
        is large enough to plausibly contain two cells (width or height
        > ``split_threshold_um``).

        Parameters
        ----------
        labeled_mask : numpy.ndarray
            Integer-labelled image from ``scipy.ndimage.label``.
        n_components : int
            Number of components in ``labeled_mask``.
        fluor : numpy.ndarray
            2-D fluorescence image.
        roi_mask : numpy.ndarray
            Original boolean ROI mask.
        pixel_size_x, pixel_size_y : float
            Physical pixel sizes in metres.

        Returns
        -------
        labeled_mask : numpy.ndarray
            Updated labels (may have more components).
        n_components : int
            Updated component count.
        """
        # Threshold: a component wider/taller than this (in µm) may
        # contain multiple cells and should be checked.
        split_threshold_um = 50.0
        min_cell_diameter_um = 15.0

        pixel_size_avg_um = 0.5 * (pixel_size_x + pixel_size_y) * 1e6

        slices = find_objects(labeled_mask)
        new_label_offset = n_components

        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            component = (labeled_mask[sl] == lbl)
            bbox_height_um = (sl[0].stop - sl[0].start) * pixel_size_y * 1e6
            bbox_width_um = (sl[1].stop - sl[1].start) * pixel_size_x * 1e6

            # Only attempt split if component is large enough
            if bbox_height_um < split_threshold_um and bbox_width_um < split_threshold_um:
                continue

            # Distance transform within the component
            dist = distance_transform_edt(component)

            # Find peaks in the distance transform — these are the
            # approximate centres of individual cells.
            min_distance_px = max(
                3, int(min_cell_diameter_um / (2.0 * pixel_size_avg_um))
            )

            try:
                coords = peak_local_max(
                    dist,
                    min_distance=min_distance_px,
                    labels=component.astype(int),
                    num_peaks_per_label=10,
                )
            except Exception:
                continue

            if len(coords) <= 1:
                # Only one peak — component is a single cell (or too
                # small to split).
                continue

            # Build markers for watershed
            markers = np.zeros_like(component, dtype=int)
            for j, (r, c) in enumerate(coords):
                markers[r, c] = j + 1

            # Watershed on inverted distance (valleys become basins)
            ws_labels = watershed(-dist, markers, mask=component)

            # Replace original labels with split labels
            for j in range(1, len(coords) + 1):
                new_label_offset += 1
                full_mask = np.zeros_like(labeled_mask)
                sub_region = np.zeros_like(labeled_mask[sl])
                sub_region[ws_labels == j] = 1
                full_mask[sl] = sub_region
                labeled_mask[full_mask == 1] = new_label_offset

        # Re-label to get clean sequential labels
        # (some original labels may have been overwritten)
        unique_labels = np.unique(labeled_mask)
        unique_labels = unique_labels[unique_labels > 0]
        new_labeled = np.zeros_like(labeled_mask)
        for new_lbl, old_lbl in enumerate(unique_labels, start=1):
            new_labeled[labeled_mask == old_lbl] = new_lbl

        return new_labeled, len(unique_labels)

    # ------------------------------------------------------------------
    # False positive filtering
    # ------------------------------------------------------------------

    def filter_false_positives(self, min_long_dim_um=20.0,
                               min_short_dim_um=10.0,
                               min_area_um2=200.0,
                               max_area_um2=5000.0,
                               min_peak_intensity=None,
                               background_median=None):
        """
        Remove regions that are too small to be real cells.

        Filtering rules
        ~~~~~~~~~~~~~~~
        1. **Asymmetric dimension rule**: The *longer* axis must be
           ≥ ``min_long_dim_um`` (default 20 µm) and the *shorter*
           axis must be ≥ ``min_short_dim_um`` (default 10 µm).  This
           allows elongated cells (e.g. 29×14 µm) while rejecting
           tiny noise fragments.
        2. **Minimum area**: ``area_um2`` must be ≥ ``min_area_um2``
           (default 200 µm²).
        3. **Maximum area**: ``area_um2`` must be ≤ ``max_area_um2``
           (default 5000 µm²).  Very large regions likely represent
           merged cells or segmentation artefacts.
        4. **Minimum intensity** (optional): ``peak_intensity`` must
           exceed ``2 × background_median``.

        Parameters
        ----------
        min_long_dim_um : float
            Minimum size for the *longer* axis (default 20 µm).
        min_short_dim_um : float
            Minimum size for the *shorter* axis (default 10 µm).
        min_area_um2 : float
            Minimum bounding-box area.
        max_area_um2 : float
            Maximum bounding-box area.
        min_peak_intensity : float or None
            Absolute minimum peak intensity.  If *None*, computed from
            ``background_median``.
        background_median : float or None
            If given, regions with ``peak_intensity < 2 × background_median``
            are rejected.

        Returns
        -------
        dict
            Summary with keys ``'accepted'``, ``'rejected'``,
            ``'rejection_reasons'`` (list of ``(region_id, reason)``).
        """
        if min_peak_intensity is None and background_median is not None:
            min_peak_intensity = 2.0 * background_median

        accepted = []
        rejected = []
        reasons = []

        for region in self._regions:
            reject_reason = None

            # Rule 1: asymmetric dimension check
            longer = max(region.width_um, region.height_um)
            shorter = min(region.width_um, region.height_um)
            if longer < min_long_dim_um or shorter < min_short_dim_um:
                reject_reason = (
                    f'too_small: {region.width_um:.1f}×{region.height_um:.1f} µm '
                    f'(need longer≥{min_long_dim_um}, shorter≥{min_short_dim_um})'
                )

            # Rule 2: minimum area
            elif region.area_um2 < min_area_um2:
                reject_reason = (
                    f'area_too_small: {region.area_um2:.0f} µm² '
                    f'(min {min_area_um2})'
                )

            # Rule 3: maximum area
            elif region.area_um2 > max_area_um2:
                reject_reason = (
                    f'area_too_large: {region.area_um2:.0f} µm² '
                    f'(max {max_area_um2})'
                )

            # Rule 4: minimum intensity
            elif min_peak_intensity is not None and \
                    region.peak_intensity < min_peak_intensity:
                reject_reason = (
                    f'too_dim: peak={region.peak_intensity:.0f} '
                    f'(min {min_peak_intensity:.0f})'
                )

            if reject_reason:
                region.status = 'skipped'
                region.processing_notes = reject_reason
                rejected.append(region)
                reasons.append((region.region_id, reject_reason))
            else:
                accepted.append(region)

        self._regions = accepted
        self._rebuild_index()

        return {
            'accepted': len(accepted),
            'rejected': len(rejected),
            'rejection_reasons': reasons,
        }

    # ------------------------------------------------------------------
    # Priority
    # ------------------------------------------------------------------

    def prioritize_queue(self, method='intensity_area'):
        """
        Assign priority scores and sort the queue.

        Parameters
        ----------
        method : str
            Prioritization method:

            ``'intensity_area'``
                ``score = peak_intensity × √area_um2``.
                Balances brightness with region size.
            ``'intensity'``
                ``score = peak_intensity``.
            ``'area'``
                ``score = area_um2`` (largest first).
            ``'spatial'``
                Left-to-right, top-to-bottom raster order.
        """
        for region in self._regions:
            if method == 'intensity_area':
                region.priority = region.peak_intensity * np.sqrt(
                    max(region.area_um2, 1.0))
            elif method == 'intensity':
                region.priority = region.peak_intensity
            elif method == 'area':
                region.priority = region.area_um2
            elif method == 'spatial':
                # Priority = -(y * 1e6 + x) so top-left is highest
                cx, cy = region.centroid_physical
                region.priority = -(cy * 1e6 + cx)
            else:
                region.priority = region.peak_intensity

        self._regions.sort(key=lambda r: r.priority, reverse=True)
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def has_queued_regions(self):
        """Return True if any regions are still queued."""
        return any(r.status == 'queued' for r in self._regions)

    def get_next_region(self):
        """
        Return the highest-priority queued region, or *None* if empty.

        Does **not** change the region's status — call
        ``mark_region_status`` when scanning begins.
        """
        for region in self._regions:
            if region.status == 'queued':
                return region
        return None

    def get_region_by_id(self, region_id):
        """Retrieve a region by its ID, or *None*."""
        idx = self._region_index.get(region_id)
        if idx is not None and idx < len(self._regions):
            return self._regions[idx]
        return None

    def mark_region_status(self, region_id, status, **kwargs):
        """
        Update a region's status and optional metadata.

        Parameters
        ----------
        region_id : str
            The region to update.
        status : str
            New status (``'scanning'``, ``'processed'``, ``'skipped'``,
            ``'failed'``).
        **kwargs
            Optional fields to set: ``close_scan_path``,
            ``nv_candidates_found``, ``pois_registered``,
            ``processing_notes``, ``close_scan_data``.
        """
        region = self.get_region_by_id(region_id)
        if region is None:
            return
        region.status = status
        for key, value in kwargs.items():
            if hasattr(region, key):
                setattr(region, key, value)

    # ------------------------------------------------------------------
    # Scanner parameter computation
    # ------------------------------------------------------------------

    def compute_scan_parameters(self, region, margin_fraction=0.10,
                                resolution=200, min_fov_um=5.0,
                                scanner_limits=None):
        """
        Compute the scanner FOV settings for a close scan of *region*.

        Adds a margin around the bounding box, clamps to scanner limits,
        and computes the expected pixel size.

        Parameters
        ----------
        region : ScanRegion
            The region to scan.
        margin_fraction : float
            Fractional margin to add around the bbox (default 10 %).
        resolution : int
            Number of samples per range (default 200, the hardware
            standard).
        min_fov_um : float
            Minimum scan FOV in µm (default 5).
        scanner_limits : dict or None
            If given, must contain ``'x_range'`` and ``'y_range'``
            tuples ``(min_m, max_m)`` to clamp the FOV.

        Returns
        -------
        dict
            Keys: ``'x_range'`` (tuple of metres), ``'y_range'`` (tuple),
            ``'resolution'``, ``'expected_pixel_size_x_um'``,
            ``'expected_pixel_size_y_um'``, ``'fov_x_um'``,
            ``'fov_y_um'``, ``'center'`` (tuple).
        """
        x_min, x_max, y_min, y_max = region.bbox_physical

        # Add margin
        dx = abs(x_max - x_min)
        dy = abs(y_max - y_min)
        margin_x = dx * margin_fraction
        margin_y = dy * margin_fraction
        x_min_m = x_min - margin_x
        x_max_m = x_max + margin_x
        y_min_m = y_min - margin_y
        y_max_m = y_max + margin_y

        # Enforce minimum FOV
        min_fov_m = min_fov_um * 1e-6
        fov_x = abs(x_max_m - x_min_m)
        fov_y = abs(y_max_m - y_min_m)

        if fov_x < min_fov_m:
            center_x = 0.5 * (x_min_m + x_max_m)
            x_min_m = center_x - min_fov_m / 2
            x_max_m = center_x + min_fov_m / 2
            fov_x = min_fov_m

        if fov_y < min_fov_m:
            center_y = 0.5 * (y_min_m + y_max_m)
            y_min_m = center_y - min_fov_m / 2
            y_max_m = center_y + min_fov_m / 2
            fov_y = min_fov_m

        # Clamp to scanner limits if provided
        if scanner_limits is not None:
            sx_min, sx_max = scanner_limits.get('x_range', (0, 200e-6))
            sy_min, sy_max = scanner_limits.get('y_range', (0, 200e-6))
            x_min_m = max(x_min_m, sx_min)
            x_max_m = min(x_max_m, sx_max)
            y_min_m = max(y_min_m, sy_min)
            y_max_m = min(y_max_m, sy_max)
            fov_x = abs(x_max_m - x_min_m)
            fov_y = abs(y_max_m - y_min_m)

        fov_x_um = fov_x * 1e6
        fov_y_um = fov_y * 1e6

        # The scanner uses ``resolution`` samples for the larger axis;
        # the shorter axis gets proportionally fewer pixels.
        larger_fov = max(fov_x, fov_y)
        px_size = larger_fov / max(resolution - 1, 1)
        px_size_x_um = px_size * 1e6
        px_size_y_um = px_size * 1e6

        return {
            'x_range': (x_min_m, x_max_m),
            'y_range': (y_min_m, y_max_m),
            'resolution': resolution,
            'expected_pixel_size_x_um': px_size_x_um,
            'expected_pixel_size_y_um': px_size_y_um,
            'fov_x_um': fov_x_um,
            'fov_y_um': fov_y_um,
            'center': (0.5 * (x_min_m + x_max_m),
                       0.5 * (y_min_m + y_max_m)),
        }

    # ------------------------------------------------------------------
    # GUI helpers
    # ------------------------------------------------------------------

    def get_cropped_images(self):
        """
        Return a list of ``(region_id, cropped_image)`` tuples for GUI
        thumbnail display.

        Only regions with status ``'queued'`` or ``'processed'`` are
        included.
        """
        return [
            (r.region_id, r.cropped_image)
            for r in self._regions
            if r.cropped_image is not None
            and r.status in ('queued', 'processed', 'scanning')
        ]

    def get_queue_summary(self):
        """
        Return a summary dictionary for GUI display.

        Returns
        -------
        dict
            Keys: ``'total'``, ``'queued'``, ``'scanning'``,
            ``'processed'``, ``'skipped'``, ``'failed'``,
            ``'total_nv_candidates'``, ``'total_pois'``.
        """
        summary = {
            'total': len(self._regions),
            'queued': 0,
            'scanning': 0,
            'processed': 0,
            'skipped': 0,
            'failed': 0,
            'total_nv_candidates': 0,
            'total_pois': 0,
        }
        for r in self._regions:
            if r.status in summary:
                summary[r.status] += 1
            summary['total_nv_candidates'] += r.nv_candidates_found
            summary['total_pois'] += r.pois_registered
        return summary

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self):
        """
        Serialize the queue state to a JSON string.

        Cropped images and close-scan data are **not** included
        (they are large numpy arrays).
        """
        data = {
            'regions': [r.to_dict() for r in self._regions],
        }
        return json.dumps(data, indent=2)

    def from_json(self, json_str):
        """
        Restore queue state from a JSON string produced by ``to_json``.
        """
        data = json.loads(json_str)
        self._regions = [
            ScanRegion.from_dict(d) for d in data.get('regions', [])
        ]
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rebuild_index(self):
        """Rebuild the region_id → index lookup."""
        self._region_index = {
            r.region_id: i for i, r in enumerate(self._regions)
        }
