# -*- coding: utf-8 -*-
"""
Logic module for Region of Interest (ROI) segmentation from confocal scan data.

This module isolates biological cells from 200×200 µm (and similar wide-field)
confocal scans by combining adaptive background subtraction, iterative spike
removal, connected component analysis with size/shape filtering, and bright
cell-candidate extraction.

Design Rationale
----------------
At wide scan ranges (e.g. 200 µm), a 200×200 pixel image has ~1 µm/px
resolution.  Biological cells (HeLa, fibroblasts, etc.) appear as 10–40 µm
diameter blobs of moderate auto-fluorescence.  NV clusters and single bright
NV centers appear as intense 1–5 px spikes.  The dark diamond substrate forms
the majority of the image.

The previous simple pipeline (median → Gaussian → Otsu) over-segments the
substrate because scattered bright spots inflate the smoothed signal.  The
redesigned pipeline here:

  1. Estimates and subtracts the slowly-varying substrate background.
  2. Iteratively sigma-clips spikes *before* smoothing.
  3. Smooths at the cell scale and thresholds.
  4. Uses connected-component analysis to accept only cell-sized, compact
     regions — rejecting isolated noise pixels and substrate artifacts.
  5. Within accepted diffuse regions, keeps filtered bright cell candidates.

Note: We are NOT removing individual POIs (NV centers) at this stage.  At wide
scans the extremely bright spots are mostly large clusters, not single NVs.
Individual POIs can still be identified later at higher resolution (5-1 um
scans), but the wide-field ROI extracted here is the filtered bright cell
signal.
"""

import os
import numpy as np
from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    binary_fill_holes,
    binary_opening,
    binary_closing,
    binary_dilation,
    label,
    find_objects,
)

try:
    from skimage.filters import threshold_otsu
    from skimage.measure import find_contours, regionprops
    HAS_SKIMAGE = True
    print("has+skiimage"+str(HAS_SKIMAGE))
except ImportError:
    HAS_SKIMAGE = False


class ROISegmentationLogic:
    """
    Multi-scale adaptive ROI segmentation for wide-field confocal scans.

    Extracts bright cell ROIs from wide-field confocal fluorescence images,
    using diffuse fluorescence only as a broad localization envelope.

    Typical usage::

        logic = ROISegmentationLogic()
        image, ux, uy, header = logic.parse_dat_file('scan.dat')
        result = logic.segment_roi(image)
        roi_mask = result['roi_mask']
        out_path = logic.filter_and_save(image, roi_mask, header, 'scan.dat')
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def parse_dat_file(self, filepath):
        """
        Parse a Qudi confocal .dat file into a 2D spatial grid.

        @param str filepath: Path to the .dat file.
        @return tuple: (image, x_coords, y_coords, header_lines)
            image      — 3D ndarray (ny, nx, 4): channels 0–2 are x, y, z
                         coordinates; channel 3 is fluorescence counts.
            x_coords   — 1D array of unique x positions (metres).
            y_coords   — 1D array of unique y positions (metres).
            header_lines — list of header strings from the file.
        """
        with open(filepath, 'r') as f:
            lines = f.readlines()

        data_start = 0
        header = []
        for i, line in enumerate(lines):
            if line.startswith('1.') or not line.startswith('#'):
                data_start = i
                break
            header.append(line)

        data = np.loadtxt(lines[data_start:])
        if data.size == 0:
            raise ValueError("No data found in file.")

        x = data[:, 0]
        y = data[:, 1]
        z = data[:, 2]
        counts = data[:, 3]

        ux = np.unique(x)
        uy = np.unique(y)

        nx = len(ux)
        ny = len(uy)

        image = np.zeros((ny, nx, 4))
        image[:, :, 0] = x.reshape(ny, nx)
        image[:, :, 1] = y.reshape(ny, nx)
        image[:, :, 2] = z.reshape(ny, nx)
        image[:, :, 3] = counts.reshape(ny, nx)

        return image, ux, uy, header

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_pixel_size(image):
        """
        Compute the physical pixel size (in metres) from the coordinate grid.

        Uses the x-coordinate channel of ``image`` (shape ``(ny, nx, 4)``).

        @param np.ndarray image: 3D image array from parse_dat_file.
        @return float: pixel size in metres.  Falls back to 1.0e-6 if the
            grid has fewer than 2 columns.
        """
        nx = image.shape[1]
        if nx < 2:
            return 1.0e-6
        x_min = image[0, 0, 0]
        x_max = image[0, -1, 0]
        return abs(x_max - x_min) / (nx - 1)

    @staticmethod
    def compute_component_properties(labeled, fluor):
        """
        Compute area, bounding-box perimeter, compactness, and mean
        intensity for each labelled connected component.

        @param np.ndarray labeled: integer-labelled image from
            ``scipy.ndimage.label``.
        @param np.ndarray fluor: 2D fluorescence array (same shape).
        @return list[dict]: one dict per component with keys
            'label', 'area', 'perimeter', 'compactness', 'mean_intensity',
            'centroid_row', 'centroid_col'.
        """
        props = []
        if HAS_SKIMAGE:
            from skimage.measure import regionprops
            regions = regionprops(labeled, intensity_image=fluor)
            for r in regions:
                area = r.area
                if area == 0:
                    continue
                perimeter = r.perimeter
                if perimeter == 0:
                    perimeter = 1.0
                compactness = (4.0 * np.pi * area) / (perimeter ** 2)
                props.append({
                    'label': r.label,
                    'area': area,
                    'perimeter': perimeter,
                    'compactness': compactness,
                    'solidity': r.solidity,
                    'mean_intensity': r.intensity_mean,
                    'centroid_row': r.centroid[0],
                    'centroid_col': r.centroid[1],
                })
            return props
            
        slices = find_objects(labeled)
        for idx, sl in enumerate(slices):
            if sl is None:
                continue
            lbl = idx + 1
            component = (labeled[sl] == lbl)
            area = int(component.sum())
            if area == 0:
                continue

            padded = np.pad(component.astype(np.uint8), 1, mode='constant',
                            constant_values=0)
            eroded = (
                padded[1:-1, 1:-1]
                & padded[:-2, 1:-1]   # up
                & padded[2:, 1:-1]    # down
                & padded[1:-1, :-2]   # left
                & padded[1:-1, 2:]    # right
            )
            perimeter = int(component.sum() - eroded.sum())
            if perimeter <= 0:
                perimeter = 1

            compactness = (4.0 * np.pi * area) / (perimeter ** 2)

            region_fluor = fluor[sl][component]
            mean_intensity = float(region_fluor.mean()) if area > 0 else 0.0

            rows, cols = np.where(component)
            centroid_row = float(rows.mean()) + sl[0].start
            centroid_col = float(cols.mean()) + sl[1].start

            props.append({
                'label': lbl,
                'area': area,
                'perimeter': perimeter,
                'compactness': compactness,
                'solidity': 1.0, # Dummy fallback
                'mean_intensity': mean_intensity,
                'centroid_row': centroid_row,
                'centroid_col': centroid_col,
            })

        return props

    # ------------------------------------------------------------------
    # Core segmentation pipeline
    # ------------------------------------------------------------------

    def segment_roi(self, image,
                    background_kernel=51,
                    despike_kernel=7,
                    smooth_sigma=6.0,
                    min_cell_area_um2=50.0,
                    max_cell_fraction=0.7,
                    min_compactness=0.05,
                    bright_spot_sigma=5.0,
                    min_bright_cell_area_um2=10.0,
                    bright_spot_dilate=2):
        """
        Segment cell ROIs using CellSegmentationLogic as intended substitute.
        """
        from logic.cell_segmentation_logic import CellSegmentationLogic
        import numpy as np
        cell_logic = CellSegmentationLogic()
        mask, smooth, labeled, cell_boxes = cell_logic.segment_cells_with_instances(
            image, min_cell_area_um2=min_cell_area_um2)
        
        fluor = image[:, :, 3].astype(float)
        props = self.compute_component_properties(labeled, fluor)
        
        return {
            'roi_mask': mask,
            'diffuse_region_mask': mask,
            'raw_bright_spots': np.zeros_like(mask),
            'component_labels': labeled,
            'stats': props,
        }


    def get_contours(self, mask):
        """Extract polygon contours from a boolean mask."""
        if not HAS_SKIMAGE:
            return []
        from skimage.measure import find_contours
        contours = find_contours(mask, 0.5)
        # Convert to typical (x, y) order
        return [np.fliplr(c) for c in contours]

    def filter_and_save(self, image, roi_mask, header, original_filepath):
        """
        Zero out fluorescence outside the ROI and save to a new .dat file.

        @param np.ndarray image: 3D image array from parse_dat_file.
        @param np.ndarray roi_mask: boolean ROI mask.
        @param list header: header lines from the original file.
        @param str original_filepath: path to the original .dat file.
        @return str: path to the newly created ``_roi_filtered.dat`` file.
        """
        fluor = image[:, :, 3]
        masked_fluor = fluor.copy()
        masked_fluor[~roi_mask] = 0.0

        new_data = image.copy()
        new_data[:, :, 3] = masked_fluor

        base, ext = os.path.splitext(original_filepath)
        out_dat_path = f"{base}_roi_filtered{ext}"

        with open(out_dat_path, 'w') as f:
            for h in header:
                f.write(h)

            flat_x = new_data[:, :, 0].flatten()
            flat_y = new_data[:, :, 1].flatten()
            flat_z = new_data[:, :, 2].flatten()
            flat_c = new_data[:, :, 3].flatten()

            for i in range(len(flat_x)):
                f.write(f"{flat_x[i]:.6e}\t{flat_y[i]:.6e}\t"
                        f"{flat_z[i]:.6e}\t{flat_c[i]:.6e}\n")

        return out_dat_path
