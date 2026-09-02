# -*- coding: utf-8 -*-
"""
Cell Data Logger — Close-Scan Annotation and Systematic Data Archiving Engine

This module provides systematic data logging, coordinate interpolation, and
diagnostic visualization for the automated confocal NV detection and
measurement pipeline.

Key Capabilities:
  1. Coordinate Interpolation: Maps physical coordinates (x, y in meters) onto
     the micro-scan image pixel grid (col, row) using exact scan coordinate arrays.
  2. Publication-Quality Close-Scan Visualization: Renders 2D confocal
     fluorescence close-scans with pinpointed verified NV markers, numbered
     badges, physical coordinate labels, and pulsed measurement tags.
  3. Systematic Directory Archiving: Stores data in timestamped directories
     organized by date, time, run ID, and cell ROI ID:
       <base_dir>/AutoNV_<YYYYMMDD_HHMMSS>_<run_id>/
         ├── run_manifest.json
         ├── run_all_pois.csv
         └── Cell_<region_id>_<timestamp>/
             ├── micro_scan_annotated.png (.pdf)
             ├── micro_scan_raw.npz
             ├── cell_summary.json
             └── cell_pois.csv
  4. Cross-Referencing: Embeds PulsedMeasurementExecutor `save_tag`, run ID,
     and timestamps directly into JSON and CSV records for easy cross-referencing
     with pulsed experiment data files.
"""

import os
import json
import time
import datetime
import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive background rendering
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects


def _json_serialize(obj):
    """Convert numpy scalars and arrays to native Python types for JSON."""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_serialize(v) for v in obj]
    return obj


def interpolate_physical_to_pixel(x_m, y_m, x_coords_m, y_coords_m):
    """Interpolate physical (x, y) coordinates in meters to fractional pixel (col, row).

    Parameters
    ----------
    x_m : float
        X position in meters.
    y_m : float
        Y position in meters.
    x_coords_m : array-like
        1D array of X coordinates along columns of the scan image (length Nx).
    y_coords_m : array-like
        1D array of Y coordinates along rows of the scan image (length Ny).

    Returns
    -------
    tuple of (float, float)
        (col_pixel, row_pixel) fractional pixel coordinates in the image array.
    """
    x_arr = np.asarray(x_coords_m, dtype=float)
    y_arr = np.asarray(y_coords_m, dtype=float)

    nx = len(x_arr)
    ny = len(y_arr)

    if nx <= 1 or ny <= 1:
        return 0.0, 0.0

    # np.interp requires monotonically increasing x values
    if x_arr[0] <= x_arr[-1]:
        col = float(np.interp(x_m, x_arr, np.arange(nx, dtype=float)))
    else:
        # Decreasing coordinates
        col = float(np.interp(x_m, x_arr[::-1], np.arange(nx, dtype=float)[::-1]))

    if y_arr[0] <= y_arr[-1]:
        row = float(np.interp(y_m, y_arr, np.arange(ny, dtype=float)))
    else:
        # Decreasing coordinates
        row = float(np.interp(y_m, y_arr[::-1], np.arange(ny, dtype=float)[::-1]))

    return col, row


def interpolate_pixel_to_physical(col, row, x_coords_m, y_coords_m):
    """Interpolate pixel (col, row) to physical (x, y) coordinates in meters.

    Parameters
    ----------
    col : float
        Column pixel index.
    row : float
        Row pixel index.
    x_coords_m : array-like
        1D array of X coordinates along columns.
    y_coords_m : array-like
        1D array of Y coordinates along rows.

    Returns
    -------
    tuple of (float, float)
        (x_m, y_m) physical coordinates in meters.
    """
    x_arr = np.asarray(x_coords_m, dtype=float)
    y_arr = np.asarray(y_coords_m, dtype=float)

    nx = len(x_arr)
    ny = len(y_arr)

    col_clamped = max(0.0, min(float(col), float(nx - 1)))
    row_clamped = max(0.0, min(float(row), float(ny - 1)))

    x_m = float(np.interp(col_clamped, np.arange(nx, dtype=float), x_arr))
    y_m = float(np.interp(row_clamped, np.arange(ny, dtype=float), y_arr))

    return x_m, y_m


class CellDataLogger:
    """Manages systematic logging, close-scan visualization, and dataset archiving."""

    def __init__(self, base_data_dir=None, run_id=None, run_tag='AutoNV', config_metadata=None):
        """Initialize a new experiment run session logger.

        Parameters
        ----------
        base_data_dir : str, optional
            Root directory to store auto NV runs. If None, defaults to 'data/auto_nv_runs'
            relative to the project root.
        run_id : str, optional
            Unique run ID identifier. If None, a timestamped ID is generated.
        run_tag : str, optional
            Prefix tag for the run folder (default 'AutoNV').
        config_metadata : dict, optional
            Configuration dictionary / metadata describing the experiment parameters.
        """
        self.run_start_time = datetime.datetime.now()
        self.timestamp_str = self.run_start_time.strftime('%Y%m%d_%H%M%S')
        self.run_id = str(run_id or 'run_{0}'.format(self.timestamp_str))
        self.run_tag = run_tag
        self.config_metadata = config_metadata or {}

        # Determine root directory
        if base_data_dir is None:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            base_data_dir = os.path.join(project_root, 'data', 'auto_nv_runs')
        self.base_data_dir = os.path.abspath(base_data_dir)

        # Create session run directory: <base_data_dir>/AutoNV_<timestamp>_<run_id>/
        folder_name = '{0}_{1}_{2}'.format(self.run_tag, self.timestamp_str, self.run_id[:8])
        self.run_dir = os.path.join(self.base_data_dir, folder_name)
        os.makedirs(self.run_dir, exist_ok=True)

        # Master tracking records
        self.cell_records = []
        self.all_verified_pois = []
        self._is_finalized = False

        # Initialize run manifest
        self._write_run_manifest(status='running')

    @property
    def output_directory(self):
        """Return the current session directory path."""
        return self.run_dir

    def _write_run_manifest(self, status='running', final_reason=None, run_stats=None):
        """Write or update the top-level run manifest JSON."""
        manifest_path = os.path.join(self.run_dir, 'run_manifest.json')
        manifest_data = {
            'run_id': self.run_id,
            'run_tag': self.run_tag,
            'status': status,
            'started_utc': self.run_start_time.isoformat() + 'Z',
            'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z',
            'output_directory': self.run_dir,
            'config_metadata': self.config_metadata,
            'total_cells_processed': len(self.cell_records),
            'total_verified_nvs': len(self.all_verified_pois),
            'cells': [
                {
                    'region_id': c.get('region_id'),
                    'cell_dir': c.get('cell_dir'),
                    'nvs_verified': len(c.get('verified_pois', [])),
                    'timestamp': c.get('timestamp'),
                }
                for c in self.cell_records
            ],
            'run_stats': run_stats or {},
            'final_reason': final_reason,
        }
        temp_path = manifest_path + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(_json_serialize(manifest_data), f, indent=2, sort_keys=True)
        os.replace(temp_path, manifest_path)

    def render_annotated_cell_image(self, image_data, x_coords_m, y_coords_m,
                                    verified_pois, scan_region=None,
                                    title=None, all_candidates=None):
        """Render a publication-quality annotated 2D close-scan plot.

        Parameters
        ----------
        image_data : numpy.ndarray
            2D or 3D fluorescence image array. If 3D, channel 3 is extracted.
        x_coords_m : array-like
            1D array of X coordinates in meters along columns.
        y_coords_m : array-like
            1D array of Y coordinates in meters along rows.
        verified_pois : list of dict
            List of verified POI dictionaries. Each dict should have:
            'accepted_position_m', 'candidate_id', 'poi_name', 'optical_stats', 'pulsed_measurement'.
        scan_region : ScanRegion or object, optional
            ScanRegion metadata object (with region_id, width_um, height_um).
        title : str, optional
            Custom figure title.
        all_candidates : list of object, optional
            List of all extracted candidates (optional, plotted as faint markers).

        Returns
        -------
        matplotlib.figure.Figure
            The rendered matplotlib figure.
        """
        # Extract 2D fluorescence array
        if image_data.ndim == 3 and image_data.shape[2] >= 4:
            fluor = image_data[:, :, 3].astype(float)
        elif image_data.ndim == 3:
            fluor = image_data[:, :, 0].astype(float)
        else:
            fluor = image_data.astype(float)

        ny, nx = fluor.shape
        x_arr_um = np.asarray(x_coords_m, dtype=float) * 1e6
        y_arr_um = np.asarray(y_coords_m, dtype=float) * 1e6

        x_min_um, x_max_um = float(x_arr_um[0]), float(x_arr_um[-1])
        y_min_um, y_max_um = float(y_arr_um[0]), float(y_arr_um[-1])

        # Ensure correct extent ordering for matplotlib imshow
        x_extent_left = min(x_min_um, x_max_um)
        x_extent_right = max(x_min_um, x_max_um)
        y_extent_bottom = min(y_min_um, y_max_um)
        y_extent_top = max(y_min_um, y_max_um)

        extent = [x_extent_left, x_extent_right, y_extent_bottom, y_extent_top]

        # Setup figure with 2 subplots: Left = Confocal Heatmap, Right = Diagnostic POI Summary Table
        fig = plt.figure(figsize=(14, 7), dpi=180)
        gs = fig.add_gridspec(1, 2, width_ratios=[1.25, 0.75], wspace=0.25)
        ax_img = fig.add_subplot(gs[0, 0])
        ax_info = fig.add_subplot(gs[0, 1])

        # Plot 2D confocal fluorescence scan
        v_min = float(np.percentile(fluor, 1))
        v_max = float(np.percentile(fluor, 99.5))
        if v_max <= v_min:
            v_max = float(np.max(fluor)) + 1.0

        im = ax_img.imshow(fluor, origin='lower', extent=extent,
                           cmap='inferno', vmin=v_min, vmax=v_max, aspect='equal')

        cbar = fig.colorbar(im, ax=ax_img, fraction=0.046, pad=0.04)
        cbar.set_label('Fluorescence (counts/s)', fontsize=10, weight='bold')
        cbar.formatter.set_powerlimits((0, 0))
        cbar.ax.tick_params(labelsize=9)

        ax_img.set_xlabel(r'X Position ($\mu\mathrm{m}$)', fontsize=11, weight='bold')
        ax_img.set_ylabel(r'Y Position ($\mu\mathrm{m}$)', fontsize=11, weight='bold')
        ax_img.tick_params(labelsize=10)

        region_id_str = getattr(scan_region, 'region_id', 'Cell') if scan_region else 'Cell'
        fig_title = title or r'Confocal Close-Scan: {0} ({1:.1f}x{2:.1f} $\mu$m)'.format(
            region_id_str, abs(x_max_um - x_min_um), abs(y_max_um - y_min_um))
        ax_img.set_title(fig_title, fontsize=12, weight='bold', pad=10)

        # Plot faint markers for unverified / all candidates if provided
        if all_candidates:
            for cand in all_candidates:
                cx = getattr(cand, 'x', None) or (cand.get('x', 0.0) if isinstance(cand, dict) else 0.0)
                cy = getattr(cand, 'y', None) or (cand.get('y', 0.0) if isinstance(cand, dict) else 0.0)
                cx_um, cy_um = cx * 1e6, cy * 1e6
                ax_img.plot(cx_um, cy_um, marker='+', color='cyan', markersize=6,
                            markeredgewidth=1.0, alpha=0.45)

        # Pinpoint and annotate each verified NV
        stroke_effect = [path_effects.Stroke(linewidth=2.5, foreground='black'),
                         path_effects.Normal()]

        for i, poi_info in enumerate(verified_pois, start=1):
            pos_m = poi_info.get('accepted_position_m') or poi_info.get('position_m') or [0, 0, 0]
            nv_x_um = float(pos_m[0]) * 1e6
            nv_y_um = float(pos_m[1]) * 1e6
            cand_id = str(poi_info.get('candidate_id') or 'POI-{0}'.format(i))

            # Pulsed measurement status
            meas_res = poi_info.get('pulsed_measurement') or poi_info.get('measurement_result') or {}
            meas_success = meas_res.get('success', False) if isinstance(meas_res, dict) else False
            has_pulsed = bool(meas_res)

            if has_pulsed and meas_success:
                marker_color = '#00FF66'  # Vivid green
                status_text = 'Pulsed: OK'
            elif has_pulsed and not meas_success:
                marker_color = '#FFCC00'  # Amber / Yellow
                status_text = 'Pulsed: FAIL'
            else:
                marker_color = '#00E5FF'  # Cyan / Optical verified only
                status_text = 'Optically Verified'

            # 1. Target Pinpoint Marker (Inner circle + outer ring + crosshair)
            ax_img.plot(nv_x_um, nv_y_um, marker='o', markersize=9,
                        markerfacecolor='none', markeredgecolor=marker_color,
                        markeredgewidth=2.2, zorder=5)
            ax_img.plot(nv_x_um, nv_y_um, marker='+', markersize=14,
                        color=marker_color, markeredgewidth=1.8, zorder=5)

            # 2. Numbered Badge Box
            badge_text = '#{0}'.format(i)
            ax_img.text(nv_x_um + 0.35, nv_y_um + 0.35, badge_text,
                        color='white', fontsize=9, weight='bold',
                        bbox=dict(boxstyle='round,pad=0.25', facecolor=marker_color,
                                  edgecolor='black', alpha=0.9, linewidth=1.2),
                        zorder=6)

            # 3. Label text below marker
            lbl_line = r'{0}' '\n' r'({1:.2f}, {2:.2f}) $\mu$m' '\n' r'[{3}]'.format(
                cand_id, nv_x_um, nv_y_um, status_text)
            txt = ax_img.text(nv_x_um + 0.35, nv_y_um - 0.7, lbl_line,
                              color='yellow' if marker_color == '#00FF66' else marker_color,
                              fontsize=8, weight='bold', zorder=6)
            txt.set_path_effects(stroke_effect)

        # Right Panel: Structured Diagnostic & POI List Summary Table
        ax_info.axis('off')
        ax_info.set_title('Cell & NV Experiment Manifest', fontsize=12, weight='bold', pad=10)

        info_lines = [
            r"$\bf{Cell\ Region:}$ " + "{0}".format(region_id_str),
            r"$\bf{Scan\ FOV:}$ " + r"{0:.2f} $\mu$m $\times$ {1:.2f} $\mu$m ({2}$\times${3} px)".format(
                abs(x_max_um - x_min_um), abs(y_max_um - y_min_um), nx, ny),
            r"$\bf{Center\ Coord:}$ " + r"({0:.2f}, {1:.2f}) $\mu$m".format(
                (x_min_um + x_max_um) / 2.0, (y_min_um + y_max_um) / 2.0),
            r"$\bf{Timestamp:}$ " + "{0}".format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            r"$\bf{Total\ Verified\ NVs:}$ " + "{0}".format(len(verified_pois)),
            "-" * 45,
        ]

        y_pos = 0.95
        line_spacing = 0.045
        for line in info_lines:
            ax_info.text(0.02, y_pos, line, fontsize=9.5, transform=ax_info.transAxes)
            y_pos -= line_spacing

        # List details for each verified NV
        if not verified_pois:
            ax_info.text(0.02, y_pos - 0.05, "No verified NV centers found in this cell.",
                         fontsize=9.5, style='italic', color='gray', transform=ax_info.transAxes)
        else:
            y_pos -= 0.01
            for i, poi_info in enumerate(verified_pois, start=1):
                pos_m = poi_info.get('accepted_position_m') or poi_info.get('position_m') or [0, 0, 0]
                cand_id = poi_info.get('candidate_id', 'POI-{0}'.format(i))
                poi_name = poi_info.get('poi_name', '')
                opt_stats = poi_info.get('optical_stats') or {}
                r2 = opt_stats.get('r_squared')
                r2_str = '{0:.3f}'.format(r2) if r2 is not None else 'N/A'
                sig = opt_stats.get('sigma_m') or [None, None]
                sig_str = '{0:.1f} nm'.format(sig[0] * 1e9) if (sig and sig[0] is not None) else 'N/A'
                peak_kcs = opt_stats.get('peak_fluorescence_cps', 0.0) / 1e3

                meas_res = poi_info.get('pulsed_measurement') or poi_info.get('measurement_result') or {}
                save_tag = meas_res.get('save_tag', '') if isinstance(meas_res, dict) else ''
                meas_status = 'Success' if (isinstance(meas_res, dict) and meas_res.get('success')) else (
                    'Failed' if (isinstance(meas_res, dict) and 'success' in meas_res) else 'N/A')

                poi_block = [
                    r"$\bf{\#%d\ %s}$ (%s)" % (i, cand_id, poi_name),
                    r"  • $\bf{Coord:}$ (%.3f, %.3f, %.3f) $\mu$m" % (
                        pos_m[0] * 1e6, pos_m[1] * 1e6, pos_m[2] * 1e6 if len(pos_m) > 2 else 0.0),
                    r"  • $\bf{Optical:}$ $R^2$=%s, $\sigma$=%s, Peak=%.1f kc/s" % (
                        r2_str, sig_str, peak_kcs),
                    r"  • $\bf{Pulsed:}$ %s (Tag: %s)" % (meas_status, save_tag or 'none'),
                ]

                for p_line in poi_block:
                    if y_pos < 0.05:
                        break
                    ax_info.text(0.04, y_pos, p_line, fontsize=8.5, transform=ax_info.transAxes)
                    y_pos -= 0.038
                y_pos -= 0.015

        fig.tight_layout()
        return fig

    def save_cell_data(self, scan_region, image_data, x_coords_m, y_coords_m, z_current_m,
                       verified_pois, all_candidates=None, cell_diagnostics=None,
                       save_pdf=True):
        """Process, interpolate, annotate, and save all data for a completed cell ROI.

        Parameters
        ----------
        scan_region : ScanRegion or object
            The region object containing region_id, width_um, height_um, etc.
        image_data : numpy.ndarray
            Confocal micro-scan image array (Ny, Nx, 4).
        x_coords_m : array-like
            1D array of X coordinates along columns (meters).
        y_coords_m : array-like
            1D array of Y coordinates along rows (meters).
        z_current_m : float
            Current Z coordinate in meters.
        verified_pois : list of dict
            List of verified / measured POIs for this cell.
        all_candidates : list, optional
            List of all initial POIExtractor candidates.
        cell_diagnostics : dict, optional
            Cell processing diagnostics dictionary.
        save_pdf : bool, optional
            Whether to also save a PDF vector copy of the annotated image.

        Returns
        -------
        dict
            Summary dictionary of saved files and cell metadata.
        """
        region_id = getattr(scan_region, 'region_id', 'R000') if scan_region else 'R000'
        timestamp_now = datetime.datetime.now()
        timestamp_slug = timestamp_now.strftime('%H%M%S')

        # 1. Create Cell Directory: <run_dir>/Cell_<region_id>_<timestamp>/
        cell_folder_name = 'Cell_{0}_{1}'.format(region_id, timestamp_slug)
        cell_dir = os.path.join(self.run_dir, cell_folder_name)
        os.makedirs(cell_dir, exist_ok=True)

        # 2. Enrich verified POIs with interpolated pixel coordinates
        enriched_pois = []
        for poi in verified_pois:
            poi_dict = dict(poi)
            pos_m = poi_dict.get('accepted_position_m') or poi_dict.get('position_m') or [0, 0, 0]
            col_px, row_px = interpolate_physical_to_pixel(
                pos_m[0], pos_m[1], x_coords_m, y_coords_m)
            poi_dict['pixel_col_interpolated'] = col_px
            poi_dict['pixel_row_interpolated'] = row_px
            poi_dict['cell_region_id'] = region_id
            poi_dict['recorded_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'
            enriched_pois.append(poi_dict)

        # 3. Render and save annotated close-scan figure
        fig = self.render_annotated_cell_image(
            image_data=image_data,
            x_coords_m=x_coords_m,
            y_coords_m=y_coords_m,
            verified_pois=enriched_pois,
            scan_region=scan_region,
            all_candidates=all_candidates)

        annotated_png_path = os.path.join(cell_dir, 'micro_scan_annotated.png')
        fig.savefig(annotated_png_path, dpi=200, bbox_inches='tight')

        annotated_pdf_path = None
        if save_pdf:
            annotated_pdf_path = os.path.join(cell_dir, 'micro_scan_annotated.pdf')
            fig.savefig(annotated_pdf_path, bbox_inches='tight')

        plt.close(fig)

        # 4. Save raw scan array in compressed NPZ
        raw_npz_path = os.path.join(cell_dir, 'micro_scan_raw.npz')
        fluor = image_data[:, :, 3] if (image_data.ndim == 3 and image_data.shape[2] >= 4) else image_data
        np.savez_compressed(
            raw_npz_path,
            image_xy=image_data,
            fluorescence=fluor,
            x_coords_m=np.asarray(x_coords_m, dtype=float),
            y_coords_m=np.asarray(y_coords_m, dtype=float),
            z_m=float(z_current_m),
            region_id=str(region_id))

        # 5. Save cell POIs tabular CSV
        csv_path = os.path.join(cell_dir, 'cell_pois.csv')
        csv_headers = [
            'Index', 'Candidate_ID', 'POI_Name',
            'X_um', 'Y_um', 'Z_um',
            'Pixel_Col_Interpolated', 'Pixel_Row_Interpolated',
            'R2_Goodness_Fit', 'Sigma_X_nm', 'Sigma_Y_nm', 'Peak_kcps',
            'Pulsed_Status', 'Pulsed_Save_Tag', 'Pulsed_Ensemble', 'Laser_Ensemble',
            'Pulsed_Elapsed_s', 'Pulsed_Run_ID', 'Timestamp_UTC'
        ]
        with open(csv_path, 'w', newline='', encoding='utf-8') as f_csv:
            writer = csv.writer(f_csv)
            writer.writerow(csv_headers)
            for idx, poi in enumerate(enriched_pois, start=1):
                pos = poi.get('accepted_position_m') or poi.get('position_m') or [0, 0, 0]
                opt = poi.get('optical_stats') or {}
                sig = opt.get('sigma_m') or [None, None]
                meas = poi.get('pulsed_measurement') or poi.get('measurement_result') or {}
                meas_success = meas.get('success') if isinstance(meas, dict) else None
                status_str = 'SUCCESS' if meas_success is True else (
                    'FAILED' if meas_success is False else 'OPTICALLY_VERIFIED')

                writer.writerow([
                    idx,
                    poi.get('candidate_id', ''),
                    poi.get('poi_name', ''),
                    '{0:.4f}'.format(pos[0] * 1e6),
                    '{0:.4f}'.format(pos[1] * 1e6),
                    '{0:.4f}'.format(pos[2] * 1e6 if len(pos) > 2 else 0.0),
                    '{0:.2f}'.format(poi.get('pixel_col_interpolated', 0.0)),
                    '{0:.2f}'.format(poi.get('pixel_row_interpolated', 0.0)),
                    '{0:.4f}'.format(opt.get('r_squared', 0.0)) if opt.get('r_squared') is not None else 'N/A',
                    '{0:.1f}'.format(sig[0] * 1e9) if (sig and sig[0] is not None) else 'N/A',
                    '{0:.1f}'.format(sig[1] * 1e9) if (sig and sig[1] is not None) else 'N/A',
                    '{0:.1f}'.format(opt.get('peak_fluorescence_cps', 0.0) / 1e3),
                    status_str,
                    meas.get('save_tag', '') if isinstance(meas, dict) else '',
                    meas.get('measurement_ensemble', '') if isinstance(meas, dict) else '',
                    meas.get('laser_pulse_ensemble', '') if isinstance(meas, dict) else '',
                    '{0:.2f}'.format(meas.get('elapsed_s', 0.0)) if isinstance(meas, dict) and 'elapsed_s' in meas else 'N/A',
                    meas.get('run_id', '') if isinstance(meas, dict) else '',
                    poi.get('recorded_at_utc', '')
                ])

        # 6. Save complete cell summary JSON
        summary_json_path = os.path.join(cell_dir, 'cell_summary.json')
        cell_summary = {
            'region_id': region_id,
            'cell_folder': cell_folder_name,
            'cell_directory': cell_dir,
            'timestamp': timestamp_now.isoformat(),
            'scan_parameters': {
                'x_range_m': [float(x_coords_m[0]), float(x_coords_m[-1])],
                'y_range_m': [float(y_coords_m[0]), float(y_coords_m[-1])],
                'z_m': float(z_current_m),
                'resolution_x': len(x_coords_m),
                'resolution_y': len(y_coords_m),
                'width_um': abs(float(x_coords_m[-1] - x_coords_m[0])) * 1e6,
                'height_um': abs(float(y_coords_m[-1] - y_coords_m[0])) * 1e6,
            },
            'nvs_verified_count': len(enriched_pois),
            'verified_pois': enriched_pois,
            'cell_diagnostics': cell_diagnostics or {},
            'files_generated': {
                'annotated_png': annotated_png_path,
                'annotated_pdf': annotated_pdf_path,
                'raw_npz': raw_npz_path,
                'summary_json': summary_json_path,
                'pois_csv': csv_path,
            }
        }
        with open(summary_json_path, 'w', encoding='utf-8') as f_json:
            json.dump(_json_serialize(cell_summary), f_json, indent=2, sort_keys=True)

        # 7. Update session records
        self.cell_records.append(cell_summary)
        self.all_verified_pois.extend(enriched_pois)
        self._write_run_manifest(status='running')

        return cell_summary

    def finalize_run(self, run_stats=None, final_reason=None):
        """Finalize the overall experiment run, write master CSV and manifest.

        Parameters
        ----------
        run_stats : dict, optional
            Final experiment run statistics.
        final_reason : str, optional
            Completion reason string.

        Returns
        -------
        dict
            Master run summary report.
        """
        if self._is_finalized:
            return {
                'run_id': self.run_id,
                'output_directory': self.run_dir,
                'total_cells_processed': len(self.cell_records),
                'total_verified_nvs': len(self.all_verified_pois),
            }

        # 1. Write master run_all_pois.csv
        master_csv_path = os.path.join(self.run_dir, 'run_all_pois.csv')
        csv_headers = [
            'Global_Index', 'Cell_Region_ID', 'Candidate_ID', 'POI_Name',
            'X_um', 'Y_um', 'Z_um',
            'R2_Goodness_Fit', 'Sigma_X_nm', 'Sigma_Y_nm', 'Peak_kcps',
            'Pulsed_Status', 'Pulsed_Save_Tag', 'Pulsed_Ensemble', 'Pulsed_Run_ID',
            'Timestamp_UTC'
        ]
        with open(master_csv_path, 'w', newline='', encoding='utf-8') as f_csv:
            writer = csv.writer(f_csv)
            writer.writerow(csv_headers)
            for idx, poi in enumerate(self.all_verified_pois, start=1):
                pos = poi.get('accepted_position_m') or poi.get('position_m') or [0, 0, 0]
                opt = poi.get('optical_stats') or {}
                sig = opt.get('sigma_m') or [None, None]
                meas = poi.get('pulsed_measurement') or poi.get('measurement_result') or {}
                meas_success = meas.get('success') if isinstance(meas, dict) else None
                status_str = 'SUCCESS' if meas_success is True else (
                    'FAILED' if meas_success is False else 'OPTICALLY_VERIFIED')

                writer.writerow([
                    idx,
                    poi.get('cell_region_id', poi.get('region_id', '')),
                    poi.get('candidate_id', ''),
                    poi.get('poi_name', ''),
                    '{0:.4f}'.format(pos[0] * 1e6),
                    '{0:.4f}'.format(pos[1] * 1e6),
                    '{0:.4f}'.format(pos[2] * 1e6 if len(pos) > 2 else 0.0),
                    '{0:.4f}'.format(opt.get('r_squared', 0.0)) if opt.get('r_squared') is not None else 'N/A',
                    '{0:.1f}'.format(sig[0] * 1e9) if (sig and sig[0] is not None) else 'N/A',
                    '{0:.1f}'.format(sig[1] * 1e9) if (sig and sig[1] is not None) else 'N/A',
                    '{0:.1f}'.format(opt.get('peak_fluorescence_cps', 0.0) / 1e3),
                    status_str,
                    meas.get('save_tag', '') if isinstance(meas, dict) else '',
                    meas.get('measurement_ensemble', '') if isinstance(meas, dict) else '',
                    meas.get('run_id', '') if isinstance(meas, dict) else '',
                    poi.get('recorded_at_utc', '')
                ])

        # 2. Update and close run manifest
        self._write_run_manifest(
            status='completed',
            final_reason=final_reason or 'Experiment completed normally.',
            run_stats=run_stats or {})

        self._is_finalized = True

        return {
            'run_id': self.run_id,
            'output_directory': self.run_dir,
            'total_cells_processed': len(self.cell_records),
            'total_verified_nvs': len(self.all_verified_pois),
            'manifest_path': os.path.join(self.run_dir, 'run_manifest.json'),
            'master_csv_path': master_csv_path,
        }
