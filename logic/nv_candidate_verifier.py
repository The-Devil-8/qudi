# -*- coding: utf-8 -*-
"""Repeated optical verification for extracted NV candidates.

This module deliberately wraps the existing :class:`OptimizerLogic` without
changing it.  The legacy optimizer only publishes a final coordinate and may
fall back to its seed when a fit fails, so this module archives its raw XY scan
and re-analyses that scan with :class:`logic.optimizer2.Optimizer2D`.

Supports three operating modes:

  - ``diagnostic`` (default): collects calibration data only.
    No automatic acceptance/rejection or POI registration.
  - ``hybrid``: applies optical acceptance gates AND registers accepted POIs
    to PoiManagerLogic, while still collecting the full calibration audit.
    This is the recommended mode for initial production use.
  - ``production``: applies gates and registers POIs.  Suppresses verbose
    per-attempt logging to reduce overhead for high-throughput runs.

The ``hybrid`` mode preserves the calibration pipeline established in
diagnostic mode, adding only acceptance gates and POI registration on top.
This enables real experiment loop integration while continuing to build
the calibration dataset needed for future drift compensation modules.
"""

from __future__ import division

import datetime
import json
import os
import re
import time
import uuid

import numpy as np
from qtpy import QtCore

from core.connector import Connector
from core.statusvariable import StatusVar
from logic.generic_logic import GenericLogic
from logic.optimizer2 import Optimizer2D
from logic.poi_verification_logger import POIVerificationLogger


def _utc_timestamp():
    """Return a sortable UTC timestamp with millisecond resolution."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def _json_value(value):
    """Convert NumPy values and tuples into values accepted by ``json``."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _safe_slug(value, fallback):
    """Return a filesystem-safe stable fragment, never an empty string."""
    slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('-._')
    return slug or fallback


def candidate_to_record(candidate, index):
    """Normalize a POIExtractor candidate or mapping for hardware use."""
    if isinstance(candidate, dict):
        getter = candidate.get
    else:
        getter = lambda name, default=None: getattr(candidate, name, default)

    candidate_id = str(getter('candidate_id', '') or 'candidate-{0:03d}'.format(index + 1))
    try:
        position = (float(getter('x')), float(getter('y')),
                    float(getter('z_estimate', 0.0)))
    except (TypeError, ValueError):
        raise ValueError('candidate {0} must provide finite x, y, z_estimate coordinates'.format(candidate_id))
    if not all(np.isfinite(position)):
        raise ValueError('candidate {0} has non-finite coordinates'.format(candidate_id))

    return {
        'candidate_id': candidate_id,
        'candidate_label': _safe_slug(candidate_id, 'candidate-{0:03d}'.format(index + 1)),
        'seed_position_m': list(position),
        'region_id': str(getter('region_id', '') or ''),
        'pixel_row': getter('pixel_row', None),
        'pixel_col': getter('pixel_col', None),
        'classification': str(getter('classification', '') or ''),
        'overall_score': getter('overall_score', None),
        'attempts': [],
        'status': 'queued',
    }


def optimizer2_result_record(result):
    """Turn an ``Optimizer2DResult`` into an audit-friendly dictionary."""
    return {
        'success': bool(result.success),
        'position_m': None if result.position_m is None else list(result.position_m),
        'sigma_m': None if result.sigma_m is None else list(result.sigma_m),
        'amplitude': result.amplitude,
        'offset': result.offset,
        'r_squared': result.r_squared,
        'sampled_bounds_m': list(result.sampled_bounds_m),
        'pitch_m': list(result.pitch_m),
        'sample_shape': list(result.sample_shape),
        'is_edge_fit': bool(result.is_edge_fit),
        'error': result.error,
    }


def xy_offset_record(seed_position_m, measured_position_m):
    """Return signed X/Y displacement and radial magnitude, or ``None``.

    This is deliberately a recorded calibration observable, not a current
    acceptance gate.  The bounds check belongs to the actual sampled support,
    while the acceptable seed offset must be calibrated on live data.
    """
    if measured_position_m is None:
        return None
    try:
        delta_x = float(measured_position_m[0]) - float(seed_position_m[0])
        delta_y = float(measured_position_m[1]) - float(seed_position_m[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not np.isfinite(delta_x) or not np.isfinite(delta_y):
        return None
    return {
        'delta_x_m': delta_x,
        'delta_y_m': delta_y,
        'radial_m': float(np.hypot(delta_x, delta_y)),
    }


def xy_distance_m(first_position, second_position):
    """Return radial XY distance in metres, or ``None``."""
    offset = xy_offset_record(first_position, second_position)
    return None if offset is None else offset['radial_m']


def analysis_gate_details(analysis, min_r_squared=0.6,
                          sigma_range_m=(0.05e-6, 0.4e-6),
                          min_fluorescence_cps=None,
                          max_fluorescence_cps=None,
                          poi_gaussian_distance_m=None,
                          poi_gaussian_center_tolerance_m=50e-9,
                          stage='stage1'):
    """Return detailed gate evaluations with measured values, criteria, and failure reasons.

    Parameters
    ----------
    analysis : dict
        An optimizer2 XY analysis record containing fit results.
    min_r_squared : float
        Minimum acceptable R² goodness of fit.
    sigma_range_m : tuple of float
        (min_sigma, max_sigma) acceptable PSF widths in metres.
    min_fluorescence_cps : float or None
        Minimum acceptable peak fluorescence in counts/s.
    max_fluorescence_cps : float or None
        Maximum acceptable peak fluorescence in counts/s.
    poi_gaussian_distance_m : float or None
        Radial XY distance between POI/optimizer return and fitted Gaussian center in metres.
    poi_gaussian_center_tolerance_m : float or None
        Maximum acceptable distance between POI and Gaussian center in metres.
    stage : str
        Verification stage ('stage1' or 'final_state').

    Returns
    -------
    dict
        Dictionary containing:
        - 'gate_failures': list of str failure codes
        - 'passed': bool (True if no gate failures)
        - 'details': list of dicts describing every evaluated gate:
            {
                'gate_name': str,
                'label': str,
                'passed': bool,
                'failure_code': str or None,
                'measured_value': str,
                'passing_criteria': str,
                'reason': str or None,
                'raw': dict,
            }
        - 'measured_metrics': dict of numerical metrics
    """
    failures = []
    details = []
    measured_metrics = {}

    # 1. 2D Gaussian Fit Convergence
    fit_success = bool(analysis.get('success')) if analysis else False
    fit_error = analysis.get('error') if analysis else 'no analysis record'
    measured_metrics['fit_success'] = fit_success
    measured_metrics['fit_error'] = fit_error

    if not fit_success:
        failures.append('xy_fit_failed')
        details.append({
            'gate_name': 'xy_fit',
            'label': 'XY 2D Gaussian Fit',
            'passed': False,
            'failure_code': 'xy_fit_failed',
            'measured_value': 'Fit Failed ({0})'.format(fit_error or 'Non-convergence'),
            'passing_criteria': 'success == True (2D Gaussian fit converged)',
            'reason': '2D Gaussian fit failed to converge on optimizer scan: {0}'.format(
                fit_error or 'non-convergence'),
            'raw': {'success': False, 'error': fit_error},
        })
        return {
            'gate_failures': failures,
            'passed': False,
            'details': details,
            'measured_metrics': measured_metrics,
        }

    details.append({
        'gate_name': 'xy_fit',
        'label': 'XY 2D Gaussian Fit',
        'passed': True,
        'failure_code': None,
        'measured_value': 'Fit Succeeded',
        'passing_criteria': 'success == True (2D Gaussian fit converged)',
        'reason': None,
        'raw': {'success': True, 'error': None},
    })

    # 2. Window Margin / Edge Fit
    is_edge_fit = bool(analysis.get('is_edge_fit'))
    measured_metrics['is_edge_fit'] = is_edge_fit
    if is_edge_fit:
        failures.append('edge_fit')
        details.append({
            'gate_name': 'edge_fit',
            'label': 'Window Margin / Edge Fit',
            'passed': False,
            'failure_code': 'edge_fit',
            'measured_value': 'is_edge_fit = True (peak at scan boundary)',
            'passing_criteria': 'is_edge_fit == False (peak inside sampled window)',
            'reason': 'Fitted peak is on/near the scan boundary, indicating NV spot is not centered in optimizer window',
            'raw': {'is_edge_fit': True},
        })
    else:
        details.append({
            'gate_name': 'edge_fit',
            'label': 'Window Margin / Edge Fit',
            'passed': True,
            'failure_code': None,
            'measured_value': 'is_edge_fit = False (peak well inside scan bounds)',
            'passing_criteria': 'is_edge_fit == False (peak inside sampled window)',
            'reason': None,
            'raw': {'is_edge_fit': False},
        })

    # 3. R2 Goodness of Fit
    r_squared = analysis.get('r_squared')
    min_r2 = float(min_r_squared)
    measured_metrics['r_squared'] = r_squared
    if r_squared is None or not np.isfinite(float(r_squared)):
        failures.append('r2_missing')
        details.append({
            'gate_name': 'r_squared',
            'label': 'R2 Goodness of Fit',
            'passed': False,
            'failure_code': 'r2_missing',
            'measured_value': 'R2 = None / non-finite',
            'passing_criteria': 'R2 > {0:.4f}'.format(min_r2),
            'reason': 'R2 goodness-of-fit could not be computed (missing or non-finite)',
            'raw': {'r_squared': None, 'min_r_squared': min_r2},
        })
    elif float(r_squared) <= min_r2:
        r2_val = float(r_squared)
        failures.append('r2_low')
        details.append({
            'gate_name': 'r_squared',
            'label': 'R2 Goodness of Fit',
            'passed': False,
            'failure_code': 'r2_low',
            'measured_value': 'R2 = {0:.4f}'.format(r2_val),
            'passing_criteria': 'R2 > {0:.4f}'.format(min_r2),
            'reason': 'R2 ({0:.4f}) is below minimum required threshold ({1:.4f})'.format(r2_val, min_r2),
            'raw': {'r_squared': r2_val, 'min_r_squared': min_r2},
        })
    else:
        r2_val = float(r_squared)
        details.append({
            'gate_name': 'r_squared',
            'label': 'R2 Goodness of Fit',
            'passed': True,
            'failure_code': None,
            'measured_value': 'R2 = {0:.4f}'.format(r2_val),
            'passing_criteria': 'R2 > {0:.4f}'.format(min_r2),
            'reason': None,
            'raw': {'r_squared': r2_val, 'min_r_squared': min_r2},
        })

    # 4. PSF Sigma Range (Width)
    sigma = analysis.get('sigma_m')
    sigma_min, sigma_max = float(sigma_range_m[0]), float(sigma_range_m[1])
    sigma_crit_str = '[{0:.1f}, {1:.1f}] nm ([{2:.3f}, {3:.3f}] um)'.format(
        sigma_min * 1e9, sigma_max * 1e9, sigma_min * 1e6, sigma_max * 1e6)
    if sigma is None or len(sigma) < 2:
        failures.append('sigma_missing')
        details.append({
            'gate_name': 'sigma',
            'label': 'PSF Sigma (Width)',
            'passed': False,
            'failure_code': 'sigma_missing',
            'measured_value': 'sigma = missing',
            'passing_criteria': sigma_crit_str,
            'reason': 'Fitted Gaussian PSF width (sigma) is missing',
            'raw': {'sigma_m': None, 'sigma_range_m': [sigma_min, sigma_max]},
        })
    else:
        try:
            sigma_x = float(sigma[0])
            sigma_y = float(sigma[1])
        except (TypeError, ValueError):
            failures.append('sigma_malformed')
            details.append({
                'gate_name': 'sigma',
                'label': 'PSF Sigma (Width)',
                'passed': False,
                'failure_code': 'sigma_malformed',
                'measured_value': 'sigma = {0}'.format(sigma),
                'passing_criteria': sigma_crit_str,
                'reason': 'Fitted Gaussian sigma values are non-numeric or malformed',
                'raw': {'sigma_m': sigma, 'sigma_range_m': [sigma_min, sigma_max]},
            })
        else:
            sx_nm, sy_nm = sigma_x * 1e9, sigma_y * 1e9
            sx_um, sy_um = sigma_x * 1e6, sigma_y * 1e6
            measured_metrics['sigma_x_m'] = sigma_x
            measured_metrics['sigma_y_m'] = sigma_y
            sig_val_str = 'sigma_x = {0:.1f} nm ({1:.3f} um), sigma_y = {2:.1f} nm ({3:.3f} um)'.format(
                sx_nm, sx_um, sy_nm, sy_um)
            if (not np.isfinite(sigma_x) or not np.isfinite(sigma_y) or
                    sigma_x < sigma_min or sigma_x > sigma_max or
                    sigma_y < sigma_min or sigma_y > sigma_max):
                failures.append('sigma_out_of_range')
                reasons = []
                if not np.isfinite(sigma_x) or not np.isfinite(sigma_y):
                    reasons.append('non-finite sigma values')
                else:
                    if sigma_x < sigma_min:
                        reasons.append('sigma_x ({0:.1f} nm) < min ({1:.1f} nm, too narrow/spike)'.format(sx_nm, sigma_min * 1e9))
                    if sigma_x > sigma_max:
                        reasons.append('sigma_x ({0:.1f} nm) > max ({1:.1f} nm, too broad/diffuse)'.format(sx_nm, sigma_max * 1e9))
                    if sigma_y < sigma_min:
                        reasons.append('sigma_y ({0:.1f} nm) < min ({1:.1f} nm, too narrow/spike)'.format(sy_nm, sigma_min * 1e9))
                    if sigma_y > sigma_max:
                        reasons.append('sigma_y ({0:.1f} nm) > max ({1:.1f} nm, too broad/diffuse)'.format(sy_nm, sigma_max * 1e9))
                details.append({
                    'gate_name': 'sigma',
                    'label': 'PSF Sigma (Width)',
                    'passed': False,
                    'failure_code': 'sigma_out_of_range',
                    'measured_value': sig_val_str,
                    'passing_criteria': sigma_crit_str,
                    'reason': 'PSF width out of range: {0}'.format(', '.join(reasons)),
                    'raw': {'sigma_x_m': sigma_x, 'sigma_y_m': sigma_y,
                            'sigma_range_m': [sigma_min, sigma_max]},
                })
            else:
                details.append({
                    'gate_name': 'sigma',
                    'label': 'PSF Sigma (Width)',
                    'passed': True,
                    'failure_code': None,
                    'measured_value': sig_val_str,
                    'passing_criteria': sigma_crit_str,
                    'reason': None,
                    'raw': {'sigma_x_m': sigma_x, 'sigma_y_m': sigma_y,
                            'sigma_range_m': [sigma_min, sigma_max]},
                })

    # 5. Sampled Support Bounds
    position = analysis.get('position_m')
    bounds = analysis.get('sampled_bounds_m')
    if position is None or bounds is None or len(bounds) < 4:
        failures.append('sampled_support_missing')
        details.append({
            'gate_name': 'sampled_support',
            'label': 'Sampled Support Bounds',
            'passed': False,
            'failure_code': 'sampled_support_missing',
            'measured_value': 'position or bounds missing',
            'passing_criteria': 'Fitted center inside acquired coordinate bounds',
            'reason': 'Position coordinates or sampled bounds are missing from analysis',
            'raw': {'position_m': position, 'sampled_bounds_m': bounds},
        })
    else:
        x_min, x_max, y_min, y_max = [float(v) for v in bounds[:4]]
        center_x, center_y = [float(v) for v in position[:2]]
        pos_str = 'center=({0:.3f}, {1:.3f}) um, bounds X=[{2:.3f}, {3:.3f}] um, Y=[{4:.3f}, {5:.3f}] um'.format(
            center_x * 1e6, center_y * 1e6, x_min * 1e6, x_max * 1e6, y_min * 1e6, y_max * 1e6)
        bounds_crit_str = 'center inside [{0:.3f}, {1:.3f}] um x [{2:.3f}, {3:.3f}] um'.format(
            x_min * 1e6, x_max * 1e6, y_min * 1e6, y_max * 1e6)
        if not (x_min <= center_x <= x_max and y_min <= center_y <= y_max):
            failures.append('outside_sampled_support')
            details.append({
                'gate_name': 'sampled_support',
                'label': 'Sampled Support Bounds',
                'passed': False,
                'failure_code': 'outside_sampled_support',
                'measured_value': pos_str,
                'passing_criteria': bounds_crit_str,
                'reason': 'Fitted center ({0:.3f}, {1:.3f}) um is extrapolated outside acquired scan window'.format(
                    center_x * 1e6, center_y * 1e6),
                'raw': {'position_m': [center_x, center_y], 'sampled_bounds_m': [x_min, x_max, y_min, y_max]},
            })
        else:
            details.append({
                'gate_name': 'sampled_support',
                'label': 'Sampled Support Bounds',
                'passed': True,
                'failure_code': None,
                'measured_value': pos_str,
                'passing_criteria': bounds_crit_str,
                'reason': None,
                'raw': {'position_m': [center_x, center_y], 'sampled_bounds_m': [x_min, x_max, y_min, y_max]},
            })

    # 6. Fluorescence Count Rate
    if min_fluorescence_cps is not None or max_fluorescence_cps is not None:
        amplitude = analysis.get('amplitude')
        offset = analysis.get('offset')
        if amplitude is not None and offset is not None:
            try:
                peak_cps = float(amplitude) + float(offset)
            except (TypeError, ValueError):
                peak_cps = None
            if peak_cps is not None and np.isfinite(peak_cps):
                measured_metrics['peak_fluorescence_cps'] = peak_cps
                peak_kcs = peak_cps / 1e3
                meas_fluor_str = 'peak = {0:.1f} kc/s ({1:.0f} c/s) [amplitude={2:.0f}, background={3:.0f}]'.format(
                    peak_kcs, peak_cps, float(amplitude), float(offset))
                min_f = float(min_fluorescence_cps) if min_fluorescence_cps is not None else None
                max_f = float(max_fluorescence_cps) if max_fluorescence_cps is not None else None
                if min_f is not None and max_f is not None:
                    fluor_crit_str = '[{0:.1f}, {1:.1f}] kc/s ([{2:.0f}, {3:.0f}] c/s)'.format(
                        min_f / 1e3, max_f / 1e3, min_f, max_f)
                elif min_f is not None:
                    fluor_crit_str = '>= {0:.1f} kc/s ({1:.0f} c/s)'.format(min_f / 1e3, min_f)
                else:
                    fluor_crit_str = '<= {0:.1f} kc/s ({1:.0f} c/s)'.format(max_f / 1e3, max_f)

                if min_f is not None and peak_cps < min_f:
                    failures.append('fluorescence_too_low')
                    details.append({
                        'gate_name': 'fluorescence',
                        'label': 'Peak Fluorescence Count Rate',
                        'passed': False,
                        'failure_code': 'fluorescence_too_low',
                        'measured_value': meas_fluor_str,
                        'passing_criteria': fluor_crit_str,
                        'reason': 'Peak fluorescence ({0:.1f} kc/s) is below minimum threshold ({1:.1f} kc/s)'.format(
                            peak_kcs, min_f / 1e3),
                        'raw': {'peak_cps': peak_cps, 'min_cps': min_f, 'max_cps': max_f},
                    })
                elif max_f is not None and peak_cps > max_f:
                    failures.append('fluorescence_too_high')
                    details.append({
                        'gate_name': 'fluorescence',
                        'label': 'Peak Fluorescence Count Rate',
                        'passed': False,
                        'failure_code': 'fluorescence_too_high',
                        'measured_value': meas_fluor_str,
                        'passing_criteria': fluor_crit_str,
                        'reason': 'Peak fluorescence ({0:.1f} kc/s) exceeds maximum threshold ({1:.1f} kc/s, likely aggregate/macro-cluster)'.format(
                            peak_kcs, max_f / 1e3),
                        'raw': {'peak_cps': peak_cps, 'min_cps': min_f, 'max_cps': max_f},
                    })
                else:
                    details.append({
                        'gate_name': 'fluorescence',
                        'label': 'Peak Fluorescence Count Rate',
                        'passed': True,
                        'failure_code': None,
                        'measured_value': meas_fluor_str,
                        'passing_criteria': fluor_crit_str,
                        'reason': None,
                        'raw': {'peak_cps': peak_cps, 'min_cps': min_f, 'max_cps': max_f},
                    })

    # 7. Stage 2 (Final State) POI-to-Gaussian Center Distance
    if stage == 'final_state':
        final_tol = float(poi_gaussian_center_tolerance_m) if poi_gaussian_center_tolerance_m is not None else 50e-9
        tol_str = '<= {0:.1f} nm ({1:.4f} um)'.format(final_tol * 1e9, final_tol * 1e6)
        if poi_gaussian_distance_m is None:
            failures.append('poi_gaussian_distance_missing')
            details.append({
                'gate_name': 'poi_gaussian_distance',
                'label': 'POI-to-Gaussian Center Distance',
                'passed': False,
                'failure_code': 'poi_gaussian_distance_missing',
                'measured_value': 'distance = None / missing',
                'passing_criteria': tol_str,
                'reason': 'POI-to-Gaussian center distance could not be computed (missing position coordinates)',
                'raw': {'distance_m': None, 'tolerance_m': final_tol},
            })
        else:
            dist_val = float(poi_gaussian_distance_m)
            dist_nm = dist_val * 1e9
            dist_um = dist_val * 1e6
            measured_metrics['poi_gaussian_distance_m'] = dist_val
            meas_dist_str = 'distance = {0:.1f} nm ({1:.4f} um)'.format(dist_nm, dist_um)
            if dist_val > final_tol:
                failures.append('poi_gaussian_distance_large')
                details.append({
                    'gate_name': 'poi_gaussian_distance',
                    'label': 'POI-to-Gaussian Center Distance',
                    'passed': False,
                    'failure_code': 'poi_gaussian_distance_large',
                    'measured_value': meas_dist_str,
                    'passing_criteria': tol_str,
                    'reason': 'POI center and Gaussian center have not converged: distance ({0:.1f} nm) exceeds tolerance ({1:.1f} nm)'.format(
                        dist_nm, final_tol * 1e9),
                    'raw': {'distance_m': dist_val, 'tolerance_m': final_tol},
                })
            else:
                details.append({
                    'gate_name': 'poi_gaussian_distance',
                    'label': 'POI-to-Gaussian Center Distance',
                    'passed': True,
                    'failure_code': None,
                    'measured_value': meas_dist_str,
                    'passing_criteria': tol_str,
                    'reason': None,
                    'raw': {'distance_m': dist_val, 'tolerance_m': final_tol},
                })

    return {
        'gate_failures': failures,
        'passed': len(failures) == 0,
        'details': details,
        'measured_metrics': measured_metrics,
    }


def analysis_gate_failures(analysis, min_r_squared=0.6,
                           sigma_range_m=(0.05e-6, 0.4e-6),
                           min_fluorescence_cps=None,
                           max_fluorescence_cps=None):
    """Return failed optical gates for one bounded XY analysis record.

    Parameters
    ----------
    analysis : dict
        An optimizer2 XY analysis record containing fit results.
    min_r_squared : float
        Minimum acceptable R² goodness of fit.
    sigma_range_m : tuple of float
        (min_sigma, max_sigma) acceptable PSF widths in metres.
    min_fluorescence_cps : float or None
        Minimum acceptable peak fluorescence in counts/s
        (amplitude + offset).  ``None`` disables this gate.
    max_fluorescence_cps : float or None
        Maximum acceptable peak fluorescence in counts/s
        (amplitude + offset).  ``None`` disables this gate.
    """
    result = analysis_gate_details(
        analysis, min_r_squared=min_r_squared,
        sigma_range_m=sigma_range_m,
        min_fluorescence_cps=min_fluorescence_cps,
        max_fluorescence_cps=max_fluorescence_cps)
    return result['gate_failures']


def is_worthy_analysis(analysis, min_r_squared=0.6,
                       sigma_range_m=(0.05e-6, 0.4e-6),
                       min_fluorescence_cps=None,
                       max_fluorescence_cps=None):
    """Return whether an XY analysis passes the configured worthy gates."""
    return not analysis_gate_failures(analysis, min_r_squared, sigma_range_m,
                                     min_fluorescence_cps, max_fluorescence_cps)


def format_gate_failures_text(gate_details_list):
    """Format a bulleted list of failed gates with measured values and reasons."""
    failed_entries = [d for d in gate_details_list if not d.get('passed', True)]
    if not failed_entries:
        return '    (None - all evaluated gates passed)'
    lines = []
    for index, entry in enumerate(failed_entries, 1):
        lines.append('    [{0}] {1}:'.format(index, entry.get('label', entry.get('gate_name'))))
        lines.append('        * Measured Value : {0}'.format(entry.get('measured_value', 'N/A')))
        if entry.get('reason'):
            lines.append('        * Failure Reason : {0}'.format(entry.get('reason')))
    return '\n'.join(lines)


def format_attempt_metrics_summary(candidate_id, stage, attempt_number, max_attempts, gate_details_dict):
    """Format a compact multi-line summary of an attempt for logging/printing."""
    details = gate_details_dict.get('details', [])
    lines = [
        '[NVCandidateVerifier] Candidate \'{0}\' | Stage: {1} | Attempt {2}/{3}:'.format(
            candidate_id, stage, attempt_number, max_attempts)
    ]
    for entry in details:
        status_tag = '[PASS]' if entry.get('passed') else '[FAIL]'
        lines.append('    {0:<6} {1:<30} : {2}'.format(
            status_tag, entry.get('label', ''), entry.get('measured_value', '')))
    return '\n'.join(lines)


def format_candidate_rejection_banner(candidate, stage, attempts_used, max_attempts,
                                      final_gate_details, attempt_history=None):
    """Format a prominent rejection report banner for logging and console output."""
    cand_id = candidate.get('candidate_id', 'unknown')
    cand_label = candidate.get('candidate_label', cand_id)
    region_id = candidate.get('region_id', 'N/A')
    score = candidate.get('overall_score')
    score_str = '{0:.3f}'.format(score) if score is not None else 'N/A'

    stage_desc = (
        'Stage 1 (WorthyCandidate Search)'
        if stage == 'stage1'
        else 'Stage 2 (Final State Convergence)')

    sep_thick = '=' * 80
    sep_thin = '-' * 80
    lines = [
        sep_thick,
        '[NVCandidateVerifier] >>> POI CANDIDATE REJECTED <<<',
        sep_thin,
        '  Candidate ID   : {0} (Label: {1}, Region: {2})'.format(cand_id, cand_label, region_id),
        '  Overall Score  : {0}'.format(score_str),
        '  Status         : REJECTED',
        '  Stage Stopped  : {0}'.format(stage_desc),
        '  Attempts Used  : {0} of {1} allowed attempts'.format(attempts_used, max_attempts),
        sep_thin,
        '  REJECTION REASON:',
        '    {0} attempt budget exhausted without meeting all optical quality gates.'.format(stage_desc),
        '',
        '  FAILED GATES ON FINAL ATTEMPT:',
    ]
    failed_entries = [d for d in final_gate_details.get('details', []) if not d.get('passed', True)]
    if failed_entries:
        lines.append(format_gate_failures_text(final_gate_details.get('details', [])))
    else:
        lines.append('    (No individual gate failed; non-scan / budget condition)')

    lines.append(sep_thick)
    return '\n'.join(lines)


def format_candidate_acceptance_banner(candidate, accepted_position, poi_name,
                                       final_gate_details, registration_status):
    """Format an acceptance banner for logging and console output."""
    cand_id = candidate.get('candidate_id', 'unknown')
    sep_thick = '=' * 80
    sep_thin = '-' * 80
    lines = [
        sep_thick,
        '[NVCandidateVerifier] >>> POI CANDIDATE ACCEPTED & OPTICALLY VERIFIED <<<',
        sep_thin,
        '  Candidate ID   : {0}'.format(cand_id),
        '  Status         : OPTICALLY VERIFIED',
        '  POI Name       : {0}'.format(poi_name),
        '  Position (XYZ) : [{0:.3f}, {1:.3f}, {2:.3f}] um'.format(
            accepted_position[0] * 1e6, accepted_position[1] * 1e6, accepted_position[2] * 1e6),
        '  Registration   : {0}'.format(registration_status),
        '  Final Optical Gate Verification:',
    ]
    for entry in final_gate_details.get('details', []):
        status_tag = '[PASS]' if entry.get('passed') else '[FAIL]'
        lines.append('    {0:<6} {1:<30} : {2}'.format(
            status_tag, entry.get('label', ''), entry.get('measured_value', '')))
    lines.append(sep_thick)
    return '\n'.join(lines)


def analyse_legacy_xy_scan(xy_refocus_image, x_values, y_values, seed_position_m,
                           optimization_channel=0):
    """Boundedly re-fit the raw legacy XY scan, returning a diagnostic record.

    This function does not treat the legacy coordinate as fit evidence.  A
    missing or malformed scan becomes an explicit failed analysis record.
    """
    try:
        image = np.asarray(xy_refocus_image, dtype=float)
        x_values = np.asarray(x_values, dtype=float)
        y_values = np.asarray(y_values, dtype=float)
        channel = 3 + int(optimization_channel)
        if image.ndim != 3 or channel >= image.shape[2]:
            raise ValueError('legacy XY image has no requested count channel')
        result = Optimizer2D().fit_local(
            image[:, :, channel], x_values, y_values,
            seed_position_m=seed_position_m[:2])
        record = optimizer2_result_record(result)
        record['fitted_offset_from_seed_xy_m'] = xy_offset_record(
            seed_position_m, result.position_m)
        return record
    except (TypeError, ValueError, IndexError) as error:
        return {
            'success': False,
            'position_m': None,
            'sigma_m': None,
            'amplitude': None,
            'offset': None,
            'r_squared': None,
            'sampled_bounds_m': None,
            'pitch_m': None,
            'sample_shape': None,
            'is_edge_fit': False,
            'fitted_offset_from_seed_xy_m': None,
            'error': 'legacy XY re-analysis unavailable: {0}'.format(error),
        }


class DiagnosticRetryPolicy:
    """Conservative 2--4 attempt policy used only for data collection.

    A normally bounded, non-edge re-analysis can complete after the minimum
    attempts.  Failures and edge results consume the whole budget and remain
    ``unresolved``.  The policy intentionally has no ``accepted`` or
    ``rejected`` action.
    """

    def __init__(self, minimum_attempts=2, maximum_attempts=4):
        if int(minimum_attempts) < 1 or int(maximum_attempts) < int(minimum_attempts):
            raise ValueError('require 1 <= minimum_attempts <= maximum_attempts')
        self.minimum_attempts = int(minimum_attempts)
        self.maximum_attempts = int(maximum_attempts)

    @staticmethod
    def is_normal_attempt(attempt):
        analysis = attempt.get('optimizer2_xy', {})
        return (attempt.get('outcome') == 'completed' and
                bool(analysis.get('success')) and
                not bool(analysis.get('is_edge_fit')))

    def next_action(self, attempts):
        """Return ``retry``, ``diagnostic_complete``, or ``unresolved``."""
        count = len(attempts)
        if count < self.minimum_attempts:
            return 'retry'
        if all(self.is_normal_attempt(attempt) for attempt in attempts):
            return 'diagnostic_complete'
        if count < self.maximum_attempts:
            return 'retry'
        return 'unresolved'


class VerificationAuditStore:
    """Persist a crash-resilient JSON manifest plus one NPZ per attempt."""

    def __init__(self, root_directory, run_id, metadata=None):
        self.run_directory = os.path.abspath(os.path.join(root_directory, run_id))
        os.makedirs(self.run_directory, exist_ok=True)
        self.manifest_path = os.path.join(self.run_directory, 'manifest.json')
        self.manifest = {
            'schema_version': 1,
            'run_id': run_id,
            'created_utc': _utc_timestamp(),
            'diagnostic_only': True,
            'metadata': _json_value(metadata or {}),
            'attempts': [],
            'terminal': False,
        }
        self._write_manifest()

    def _write_manifest(self):
        temporary_path = self.manifest_path + '.tmp'
        with open(temporary_path, 'w', encoding='utf-8') as stream:
            json.dump(_json_value(self.manifest), stream, indent=2, sort_keys=True)
        os.replace(temporary_path, self.manifest_path)

    def record_attempt(self, candidate_label, attempt_number, attempt, arrays=None):
        """Store an immutable raw capture and append its metadata to manifest."""
        record = dict(attempt)
        archive_name = None
        arrays = arrays or {}
        finite_arrays = {name: np.asarray(value) for name, value in arrays.items()
                         if value is not None}
        if finite_arrays:
            archive_name = 'attempt_{0}_a{1:02d}.npz'.format(
                _safe_slug(candidate_label, 'candidate'), int(attempt_number))
            np.savez_compressed(os.path.join(self.run_directory, archive_name), **finite_arrays)
        record['raw_archive'] = archive_name
        self.manifest['attempts'].append(_json_value(record))
        self._write_manifest()
        return archive_name

    def finish(self, result):
        self.manifest['terminal'] = True
        self.manifest['finished_utc'] = _utc_timestamp()
        self.manifest['result'] = _json_value(result)
        self._write_manifest()


class NVCandidateVerifier(GenericLogic):
    """Run repeat optical diagnostics through the legacy optimizer safely."""

    optimizerlogic = Connector(interface='OptimizerLogic')
    savelogic = Connector(interface='SaveLogic', optional=True)
    poimanagerlogic = Connector(interface='PoiManagerLogic', optional=True)

    operating_mode = StatusVar('operating_mode', 'diagnostic')
    minimum_attempts = StatusVar('minimum_attempts', 2)
    maximum_attempts = StatusVar('maximum_attempts', 4)
    stage1_max_attempts = StatusVar('stage1_max_attempts', 4)
    stage2_max_attempts = StatusVar('stage2_max_attempts', 5)
    worthy_min_xy_r_squared = StatusVar('worthy_min_xy_r_squared', 0.6)
    worthy_sigma_min_m = StatusVar('worthy_sigma_min_m', 0.05e-6)
    worthy_sigma_max_m = StatusVar('worthy_sigma_max_m', 0.4e-6)
    poi_gaussian_center_tolerance_m = StatusVar(
        'poi_gaussian_center_tolerance_m', 50e-9)
    update_seed_after_optimizer = StatusVar('update_seed_after_optimizer', True)
    auto_register_poi = StatusVar('auto_register_poi', True)
    attempt_timeout_s = StatusVar('attempt_timeout_s', 90.0)
    timeout_cleanup_s = StatusVar('timeout_cleanup_s', 10.0)
    audit_subdirectory = StatusVar('audit_subdirectory', 'NVCandidateVerifier')
    min_fluorescence_counts_per_s = StatusVar(
        'min_fluorescence_counts_per_s', 50e3)    # 50 kc/s
    max_fluorescence_counts_per_s = StatusVar(
        'max_fluorescence_counts_per_s', 8e6)     # 8 Mc/s

    sigVerificationProgress = QtCore.Signal(str, str, int, int)
    sigCandidateVerificationUpdated = QtCore.Signal(object)
    sigVerificationFinished = QtCore.Signal(object)
    sigVerificationError = QtCore.Signal(str, str)
    sigCandidateAccepted = QtCore.Signal(object)
    sigCandidateRejected = QtCore.Signal(object)

    def _log_and_print(self, message, level='info'):
        """Log to Qudi logger and print to stdout so messages are visible everywhere."""
        print(message)
        if hasattr(self, 'log'):
            if level == 'warning':
                self.log.warning(message)
            elif level == 'error':
                self.log.error(message)
            else:
                self.log.info(message)

    # ------------------------------------------------------------------
    # Backward-compatible property for configs still using diagnostic_only
    # ------------------------------------------------------------------
    @property
    def diagnostic_only(self):
        """Backward-compatible read accessor.

        Returns True when operating_mode is 'diagnostic', False otherwise.
        Existing code that checks ``self.diagnostic_only`` will continue to
        work without modification.
        """
        return self._effective_mode() == 'diagnostic'

    @diagnostic_only.setter
    def diagnostic_only(self, value):
        """Backward-compatible write accessor.

        If a Qudi config still contains ``diagnostic_only: True``, this maps
        it to ``operating_mode = 'diagnostic'``.  ``diagnostic_only: False``
        maps to ``operating_mode = 'hybrid'``.
        """
        if isinstance(value, bool):
            self.operating_mode = 'diagnostic' if value else 'hybrid'

    def _effective_mode(self):
        """Return the validated operating mode string."""
        mode = str(self.operating_mode).strip().lower()
        if mode not in ('diagnostic', 'hybrid', 'production'):
            self.log.warning(
                'Unknown operating_mode "{0}", falling back to "diagnostic".'
                .format(self.operating_mode))
            return 'diagnostic'
        return mode

    def on_activate(self):
        """Connect once to the optimizer and initialise the watchdog."""
        self.log.info('NVCandidateVerifier activating in "{0}" mode.'.format(
            self._effective_mode()))
        self._optimizer = self.optimizerlogic()
        self._active = False
        self._stop_requested = False
        self._current_index = -1
        self._attempt_started_at = None
        self._attempt_started_utc = None
        self._active_tag = None
        self._batch = None
        self._audit = None
        self._poi_logger = None
        self._timeout_requested = False
        self._current_stage = 'stage1'
        self._watchdog = QtCore.QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_attempt_timeout)
        self._timeout_cleanup = QtCore.QTimer(self)
        self._timeout_cleanup.setSingleShot(True)
        self._timeout_cleanup.timeout.connect(self._on_timeout_cleanup)
        self._optimizer.sigRefocusFinished.connect(
            self._on_refocus_finished, QtCore.Qt.QueuedConnection)
        return 0

    def on_deactivate(self):
        """Disconnect only this module's slot; never disturb other callers."""
        if hasattr(self, '_watchdog'):
            self._watchdog.stop()
            self._timeout_cleanup.stop()
        if hasattr(self, '_optimizer'):
            try:
                self._optimizer.sigRefocusFinished.disconnect(self._on_refocus_finished)
            except (TypeError, RuntimeError):
                pass
        return 0

    def _audit_root(self):
        save_logic = self.savelogic()
        if save_logic is not None:
            return save_logic.get_path_for_module(module_name=self.audit_subdirectory)
        return os.path.join(os.getcwd(), 'data', self.audit_subdirectory)

    def verify_batch(self, candidates, run_context=None):
        """Start a non-blocking staged verification batch and return run ID."""
        if self._active:
            raise RuntimeError('an NVCandidateVerifier batch is already active')

        records = [candidate_to_record(candidate, index)
                   for index, candidate in enumerate(candidates)]
        if not records:
            raise ValueError('verify_batch requires at least one candidate')
        labels = [record['candidate_label'] for record in records]
        if len(set(labels)) != len(labels):
            raise ValueError('candidate_id values must be unique within one verification batch')
        policy = self._policy_snapshot()
        run_id = 'nvverify_{0}_{1}'.format(_utc_timestamp(), uuid.uuid4().hex[:8])
        metadata = {
            'run_context': run_context or {},
            'policy': policy,
            'legacy_optimizer_settings': self._optimizer_settings_snapshot(),
        }
        self._poi_logger = POIVerificationLogger(
            self._audit_root(), run_id=run_id,
            run_context=run_context or {}, policy_snapshot=metadata)
        for index, candidate in enumerate(candidates):
            self._poi_logger.start_candidate(candidate, index=index)
        self._batch = {
            'run_id': run_id,
            'audit_directory': self._poi_logger.run_directory,
            'started_utc': _utc_timestamp(),
            'diagnostic_only': bool(self.diagnostic_only),
            'operating_mode': self._effective_mode(),
            'run_context': run_context or {},
            'candidates': records,
            'status': 'running',
            'policy': policy,
        }
        self._policy = policy
        self._active = True
        self._stop_requested = False
        self._current_index = 0
        self._current_stage = 'stage1'

        min_fl_str = '{0:.1f}'.format(float(self.min_fluorescence_counts_per_s) / 1e3) if self.min_fluorescence_counts_per_s is not None else 'None'
        max_fl_str = '{0:.1f}'.format(float(self.max_fluorescence_counts_per_s) / 1e3) if self.max_fluorescence_counts_per_s is not None else 'None'
        self._log_and_print(
            "[NVCandidateVerifier] Starting verification batch: {0} candidate(s) | Mode: '{1}'\n"
            "  Policy parameters: Stage 1 max attempts={2}, Stage 2 max attempts={3}, "
            "min R2={4:.2f}, sigma range=[{5:.1f}, {6:.1f}] nm, fluorescence range=[{7}, {8}] kc/s, "
            "center tolerance={9:.1f} nm".format(
                len(records), self._effective_mode(), int(self.stage1_max_attempts),
                int(self.stage2_max_attempts), float(self.worthy_min_xy_r_squared),
                float(self.worthy_sigma_min_m) * 1e9, float(self.worthy_sigma_max_m) * 1e9,
                min_fl_str, max_fl_str,
                float(self.poi_gaussian_center_tolerance_m) * 1e9))

        QtCore.QTimer.singleShot(0, self._start_current_attempt)
        return run_id

    def stop_verification(self):
        """Request a safe stop and retain an audit record for the active run."""
        if not self._active:
            return
        self._stop_requested = True
        if self._active_tag is not None:
            self._optimizer.stop_refocus()
            self._timeout_cleanup.start(max(1, int(float(self.timeout_cleanup_s) * 1000)))
        else:
            self._finish_batch('stopped')

    def _optimizer_settings_snapshot(self):
        names = ('refocus_XY_size', 'optimizer_XY_res', 'refocus_Z_size',
                 'optimizer_Z_res', 'optimization_sequence', 'opt_channel',
                 '_clock_frequency')
        return {name: _json_value(getattr(self._optimizer, name, None))
                for name in names}

    def _policy_snapshot(self):
        return {
            'operating_mode': self._effective_mode(),
            'diagnostic_only': bool(self.diagnostic_only),
            'stage1_max_attempts': int(self.stage1_max_attempts),
            'stage2_max_attempts': int(self.stage2_max_attempts),
            'worthy_min_xy_r_squared': float(self.worthy_min_xy_r_squared),
            'worthy_sigma_min_m': float(self.worthy_sigma_min_m),
            'worthy_sigma_max_m': float(self.worthy_sigma_max_m),
            'poi_gaussian_center_tolerance_m': float(
                self.poi_gaussian_center_tolerance_m),
            'update_seed_after_optimizer': bool(self.update_seed_after_optimizer),
            'auto_register_poi': bool(self.auto_register_poi),
            'attempt_timeout_s': float(self.attempt_timeout_s),
            'timeout_cleanup_s': float(self.timeout_cleanup_s),
            'min_fluorescence_counts_per_s': float(
                self.min_fluorescence_counts_per_s) if self.min_fluorescence_counts_per_s is not None else None,
            'max_fluorescence_counts_per_s': float(
                self.max_fluorescence_counts_per_s) if self.max_fluorescence_counts_per_s is not None else None,
        }

    def _sigma_range_m(self):
        return (float(self.worthy_sigma_min_m), float(self.worthy_sigma_max_m))

    def _start_current_attempt(self):
        if not self._active:
            return
        if self._stop_requested:
            self._finish_batch('stopped')
            return
        if self._current_index >= len(self._batch['candidates']):
            self._finish_batch('completed')
            return

        candidate = self._batch['candidates'][self._current_index]
        candidate.setdefault('current_seed_position_m',
                             list(candidate['seed_position_m']))
        candidate.setdefault('stage', 'stage1')
        candidate.setdefault('stage1_attempts', 0)
        candidate.setdefault('final_state_attempts', 0)
        self._current_stage = candidate['stage']
        attempt_number = (
            int(candidate['stage1_attempts']) + 1
            if self._current_stage == 'stage1'
            else int(candidate['final_state_attempts']) + 1)
        if self._optimizer.module_state() != 'idle':
            self._record_non_scan_attempt(candidate, attempt_number, 'hardware_busy',
                                          'legacy optimizer module is not idle')
            candidate['status'] = 'unresolved'
            self._advance_candidate()
            return

        self._active_tag = '{0}:{1}:a{2:02d}'.format(
            self._batch['run_id'], candidate['candidate_label'],
            len(candidate['attempts']) + 1)
        self._attempt_started_at = time.monotonic()
        self._attempt_started_utc = _utc_timestamp()
        self._timeout_requested = False
        candidate['status'] = 'scanning_{0}'.format(self._current_stage)
        if hasattr(self, 'log'):
            self.log.info(
                "Scanning Candidate '{0}' ({1}/{2}) | Stage: {3} | Attempt {4}/{5} | Seed: [{6:.3f}, {7:.3f}, {8:.3f}] um".format(
                    candidate['candidate_id'],
                    self._current_index + 1,
                    len(self._batch['candidates']),
                    self._current_stage,
                    attempt_number,
                    self._stage_max_attempts(self._current_stage),
                    candidate['current_seed_position_m'][0] * 1e6,
                    candidate['current_seed_position_m'][1] * 1e6,
                    candidate['current_seed_position_m'][2] * 1e6))

        self.sigVerificationProgress.emit(self._batch['run_id'], candidate['candidate_id'],
                                          attempt_number,
                                          self._stage_max_attempts(self._current_stage))
        self.sigCandidateVerificationUpdated.emit(dict(candidate))
        self._watchdog.start(max(1, int(float(self.attempt_timeout_s) * 1000)))
        try:
            self._optimizer.start_refocus(
                initial_pos=list(candidate['current_seed_position_m']),
                caller_tag=self._active_tag)
        except Exception as error:
            self._watchdog.stop()
            self._active_tag = None
            self._record_non_scan_attempt(candidate, attempt_number, 'hardware_error', str(error))
            candidate['status'] = 'unresolved'
            self._advance_candidate()

    def _stage_max_attempts(self, stage):
        if stage == 'stage1':
            return int(self.stage1_max_attempts)
        return int(self.stage2_max_attempts)

    def _on_attempt_timeout(self):
        """Ask the legacy optimizer to stop, then await its correlated signal."""
        if self._active and self._active_tag is not None:
            self._timeout_requested = True
            self._optimizer.stop_refocus()
            self._timeout_cleanup.start(max(1, int(float(self.timeout_cleanup_s) * 1000)))

    def _on_timeout_cleanup(self):
        """Persist a timeout if the legacy optimizer never emits completion.

        No further candidate is started because hardware ownership is unknown;
        a later legacy signal is ignored rather than mis-correlated to another
        candidate.
        """
        if not self._active or self._active_tag is None:
            return
        candidate = self._batch['candidates'][self._current_index]
        attempt_number = self._current_stage_attempt_number(candidate)
        elapsed = time.monotonic() - self._attempt_started_at
        attempt = {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'attempt_number': attempt_number,
            'caller_tag': self._active_tag,
            'started_utc': self._attempt_started_utc,
            'elapsed_s': elapsed,
            'outcome': 'stopped' if self._stop_requested else 'timeout',
            'stage': self._current_stage,
            'seed_position_m': candidate.get('current_seed_position_m',
                                             candidate['seed_position_m']),
            'error': 'no matching sigRefocusFinished after stop_refocus cleanup interval',
            'optimizer2_xy': {
                'success': False,
                'is_edge_fit': False,
                'error': 'no raw scan received before timeout cleanup',
            },
        }
        self._record_attempt(candidate, attempt_number, attempt, None)
        candidate['status'] = 'unresolved'
        self._finalize_logged_candidate(candidate, 'unresolved',
                                        rejection_reason=attempt['error'])
        self._log_and_print(
            "[NVCandidateVerifier] Candidate '{0}' TIMEOUT on attempt {1}: {2}".format(
                candidate['candidate_id'], attempt_number, attempt['error']),
            level='error')
        self.sigVerificationError.emit(self._batch['run_id'], attempt['error'])
        self._active_tag = None
        self._finish_batch('stopped' if self._stop_requested else 'timed_out')

    def _on_refocus_finished(self, caller_tag, optimal_position):
        if not self._active or caller_tag != self._active_tag:
            return
        self._watchdog.stop()
        self._timeout_cleanup.stop()
        candidate = self._batch['candidates'][self._current_index]
        attempt_number = self._current_stage_attempt_number(candidate)
        elapsed = time.monotonic() - self._attempt_started_at
        attempt, arrays = self._capture_attempt(candidate, attempt_number,
                                                optimal_position, elapsed)
        if self._stop_requested:
            attempt['outcome'] = 'stopped'
        elif self._timeout_requested:
            attempt['outcome'] = 'timeout'
        self._record_attempt(candidate, attempt_number, attempt, arrays)
        self._active_tag = None

        if self._stop_requested:
            candidate['status'] = 'stopped'
            self._finalize_logged_candidate(candidate, 'skipped',
                                            rejection_reason='stopped by user')
            self._finish_batch('stopped')
            return
        action = self._evaluate_after_attempt(candidate, attempt)
        candidate['status'] = action
        self.sigCandidateVerificationUpdated.emit(dict(candidate))
        if action == 'retry':
            QtCore.QTimer.singleShot(0, self._start_current_attempt)
        elif action == 'enter_final_state':
            candidate['stage'] = 'final_state'
            QtCore.QTimer.singleShot(0, self._start_current_attempt)
        else:
            self._advance_candidate()

    def _current_stage_attempt_number(self, candidate):
        if self._current_stage == 'stage1':
            return int(candidate.get('stage1_attempts', 0)) + 1
        return int(candidate.get('final_state_attempts', 0)) + 1

    def _capture_attempt(self, candidate, attempt_number, optimal_position, elapsed):
        xy_image = getattr(self._optimizer, 'xy_refocus_image', None)
        x_values = getattr(self._optimizer, '_X_values', None)
        y_values = getattr(self._optimizer, '_Y_values', None)
        opt_channel = getattr(self._optimizer, 'opt_channel', 0)
        seed_position = candidate.get('current_seed_position_m',
                                      candidate['seed_position_m'])
        analysis = analyse_legacy_xy_scan(xy_image, x_values, y_values,
                                          seed_position, opt_channel)
        legacy_sigma = [getattr(self._optimizer, 'optim_sigma_x', None),
                        getattr(self._optimizer, 'optim_sigma_y', None),
                        getattr(self._optimizer, 'optim_sigma_z', None)]
        legacy_xy_fit_evidence = all(value is not None and np.isfinite(value) and value > 0
                                     for value in legacy_sigma[:2])
        arrays = {
            'xy_refocus_image': xy_image,
            'xy_x_values_m': x_values,
            'xy_y_values_m': y_values,
            'z_refocus_line': getattr(self._optimizer, 'z_refocus_line', None),
            'z_values_m': getattr(self._optimizer, '_zimage_Z_values', None),
            'z_fit_data': getattr(self._optimizer, 'z_fit_data', None),
            'seed_position_m': seed_position,
            'legacy_return_position_m': optimal_position,
        }

        poi_gaussian_distance = xy_distance_m(optimal_position,
                                             analysis.get('position_m'))
        final_tolerance = float(self.poi_gaussian_center_tolerance_m)

        gate_details_dict = analysis_gate_details(
            analysis,
            min_r_squared=float(self.worthy_min_xy_r_squared),
            sigma_range_m=self._sigma_range_m(),
            min_fluorescence_cps=float(self.min_fluorescence_counts_per_s) if self.min_fluorescence_counts_per_s is not None else None,
            max_fluorescence_cps=float(self.max_fluorescence_counts_per_s) if self.max_fluorescence_counts_per_s is not None else None,
            poi_gaussian_distance_m=poi_gaussian_distance,
            poi_gaussian_center_tolerance_m=final_tolerance,
            stage=self._current_stage)

        final_gate_failures = gate_details_dict['gate_failures']
        gate_details = gate_details_dict['details']
        gate_failure_details = [d for d in gate_details if not d.get('passed', True)]

        if hasattr(self, 'log'):
            self.log.debug(
                "Candidate '{0}' attempt {1} gate failures: {2}".format(
                    candidate['candidate_id'], attempt_number,
                    ','.join(final_gate_failures) if final_gate_failures else 'none'))

        worthy_candidate = len(analysis_gate_failures(
            analysis, float(self.worthy_min_xy_r_squared), self._sigma_range_m(),
            min_fluorescence_cps=float(self.min_fluorescence_counts_per_s) if self.min_fluorescence_counts_per_s is not None else None,
            max_fluorescence_cps=float(self.max_fluorescence_counts_per_s) if self.max_fluorescence_counts_per_s is not None else None)) == 0

        final_state_fit = (self._current_stage == 'final_state' and len(final_gate_failures) == 0)

        return {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'stage': self._current_stage,
            'attempt_number': attempt_number,
            'caller_tag': self._active_tag,
            'started_utc': self._attempt_started_utc,
            'elapsed_s': elapsed,
            'outcome': 'completed',
            'seed_position_m': seed_position,
            'legacy_return_position_m': _json_value(optimal_position),
            'legacy_return_offset_from_seed_xy_m': xy_offset_record(
                seed_position, optimal_position),
            'legacy_sigma_m': _json_value(legacy_sigma),
            'legacy_xy_fit_evidence': bool(legacy_xy_fit_evidence),
            'legacy_xy_fit_note': ('positive legacy sigmas observed; raw bounded re-analysis remains authoritative'
                                   if legacy_xy_fit_evidence else
                                   'indeterminate: zero/absent legacy sigma can represent a fallback'),
            'optimizer2_xy': analysis,
            'gate_failures': final_gate_failures,
            'gate_details_dict': gate_details_dict,
            'gate_failure_details': gate_failure_details,
            'worthy_candidate': worthy_candidate,
            'final_state_fit': final_state_fit,
            'poi_gaussian_distance_xy_m': poi_gaussian_distance,
        }, arrays

    def _record_non_scan_attempt(self, candidate, attempt_number, outcome, error):
        attempt = {
            'run_id': self._batch['run_id'],
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'stage': self._current_stage,
            'attempt_number': attempt_number,
            'caller_tag': None,
            'started_utc': _utc_timestamp(),
            'elapsed_s': 0.0,
            'outcome': outcome,
            'seed_position_m': candidate.get('current_seed_position_m',
                                             candidate['seed_position_m']),
            'error': error,
            'gate_failures': [outcome],
            'optimizer2_xy': {'success': False, 'is_edge_fit': False, 'error': error},
        }
        self._log_and_print(
            "[NVCandidateVerifier] Candidate '{0}' Attempt {1} NON-SCAN ERROR: outcome='{2}', error='{3}'".format(
                candidate['candidate_id'], attempt_number, outcome, error),
            level='error')
        self._record_attempt(candidate, attempt_number, attempt, None)
        self._finalize_logged_candidate(candidate, 'unresolved',
                                        rejection_reason=error)
        self.sigVerificationError.emit(self._batch['run_id'], str(error))

    def _record_attempt(self, candidate, attempt_number, attempt, arrays):
        next_seed, next_seed_source = self._choose_next_seed(candidate, attempt)
        if bool(self.update_seed_after_optimizer) and next_seed is not None:
            attempt['next_seed_position_m'] = next_seed
            attempt['next_seed_source'] = next_seed_source
        else:
            attempt['next_seed_position_m'] = None
            attempt['next_seed_source'] = 'unchanged'
        if self._poi_logger is not None:
            analysis = attempt.get('optimizer2_xy', {})
            self._poi_logger.log_attempt(
                candidate['candidate_id'],
                stage=attempt.get('stage', self._current_stage),
                attempt_number=attempt_number,
                seed_position_m=attempt.get('seed_position_m'),
                optimizer_return_position_m=attempt.get('legacy_return_position_m'),
                gaussian_center_xy_m=analysis.get('position_m'),
                poi_center_xy_m=attempt.get('legacy_return_position_m'),
                next_seed_position_m=attempt.get('next_seed_position_m'),
                outcome=attempt.get('outcome', 'completed'),
                r_squared_xy=analysis.get('r_squared'),
                sigma_xy_m=analysis.get('sigma_m'),
                candidate_score=candidate.get('overall_score'),
                gate_failures=attempt.get('gate_failures', []),
                elapsed_s=attempt.get('elapsed_s'),
                raw_arrays=arrays,
                error=attempt.get('error'),
                metadata={
                    'caller_tag': attempt.get('caller_tag'),
                    'legacy_sigma_m': attempt.get('legacy_sigma_m'),
                    'legacy_xy_fit_evidence': attempt.get('legacy_xy_fit_evidence'),
                    'next_seed_source': attempt.get('next_seed_source'),
                    'raw_optimizer2_xy': analysis,
                })
        if attempt.get('stage') == 'stage1':
            candidate['stage1_attempts'] = int(candidate.get('stage1_attempts', 0)) + 1
        elif attempt.get('stage') == 'final_state':
            candidate['final_state_attempts'] = int(
                candidate.get('final_state_attempts', 0)) + 1
        candidate['attempts'].append(attempt)
        if bool(self.update_seed_after_optimizer) and next_seed is not None:
            candidate['current_seed_position_m'] = next_seed

    def _choose_next_seed(self, candidate, attempt):
        seed = attempt.get('seed_position_m') or candidate.get('current_seed_position_m')
        z_value = None
        legacy_return = attempt.get('legacy_return_position_m')
        if legacy_return is not None and len(legacy_return) >= 3:
            try:
                z_value = float(legacy_return[2])
            except (TypeError, ValueError):
                z_value = None
        if z_value is None and seed is not None and len(seed) >= 3:
            z_value = float(seed[2])
        analysis_position = attempt.get('optimizer2_xy', {}).get('position_m')
        if analysis_position is not None:
            try:
                return [float(analysis_position[0]), float(analysis_position[1]),
                        z_value], 'gaussian_center'
            except (TypeError, ValueError, IndexError):
                pass
        normalized_return = None
        if legacy_return is not None:
            try:
                normalized_return = [float(legacy_return[0]), float(legacy_return[1]),
                                     float(legacy_return[2])]
            except (TypeError, ValueError, IndexError):
                normalized_return = None
        if normalized_return is not None:
            return normalized_return, 'optimizer_return'
        return None, 'unchanged'

    def _advance_candidate(self):
        self._current_index += 1
        self._current_stage = 'stage1'
        QtCore.QTimer.singleShot(0, self._start_current_attempt)

    def _evaluate_after_attempt(self, candidate, attempt):
        if attempt.get('outcome') in ('timeout', 'hardware_error', 'hardware_busy'):
            rejection_reason = attempt.get('error') or attempt.get('outcome')
            self._log_and_print(
                "[NVCandidateVerifier] Candidate '{0}' marked UNRESOLVED: {1}".format(
                    candidate['candidate_id'], rejection_reason),
                level='warning')
            self._finalize_logged_candidate(
                candidate, 'unresolved',
                rejection_reason=rejection_reason)
            return 'unresolved'

        if attempt.get('stage') == 'stage1':
            if bool(attempt.get('worthy_candidate')):
                candidate['stage'] = 'final_state'
                candidate['status'] = 'enter_final_state'
                if hasattr(self, 'log'):
                    self.log.info(
                        "Candidate '{0}' passed Stage 1 WorthyCandidate; advancing to Stage 2...".format(
                            candidate['candidate_id']))
                return 'enter_final_state'
            if int(candidate.get('stage1_attempts', 0)) >= int(self.stage1_max_attempts):
                candidate['status'] = 'rejected'
                rejection_banner = format_candidate_rejection_banner(
                    candidate, 'stage1', int(candidate.get('stage1_attempts', 0)),
                    int(self.stage1_max_attempts), attempt.get('gate_details_dict', {}))
                self._log_and_print(rejection_banner, level='warning')
                rejection_reason = 'stage1_budget_exhausted:{0}'.format(
                    ','.join(attempt.get('gate_failures', [])))
                candidate['rejection_reason'] = rejection_reason
                candidate['rejection_details'] = attempt.get('gate_failure_details', [])
                self._finalize_logged_candidate(
                    candidate, 'rejected',
                    rejection_reason=rejection_reason)
                self.sigCandidateRejected.emit(dict(candidate))
                return 'rejected'
            failed_str = ', '.join(attempt.get('gate_failures', [])) or 'unresolved'
            if hasattr(self, 'log'):
                self.log.info(
                    "Candidate '{0}' Stage 1 attempt {1}/{2} failed gates [{3}]. Retrying next attempt...".format(
                        candidate['candidate_id'], int(candidate.get('stage1_attempts', 0)),
                        int(self.stage1_max_attempts), failed_str))
            return 'retry'

        if attempt.get('stage') == 'final_state':
            if bool(attempt.get('final_state_fit')):
                return self._accept_candidate(candidate, attempt)
            if int(candidate.get('final_state_attempts', 0)) >= int(self.stage2_max_attempts):
                candidate['status'] = 'rejected'
                rejection_banner = format_candidate_rejection_banner(
                    candidate, 'final_state', int(candidate.get('final_state_attempts', 0)),
                    int(self.stage2_max_attempts), attempt.get('gate_details_dict', {}))
                self._log_and_print(rejection_banner, level='warning')
                rejection_reason = 'final_state_budget_exhausted:{0}'.format(
                    ','.join(attempt.get('gate_failures', [])))
                candidate['rejection_reason'] = rejection_reason
                candidate['rejection_details'] = attempt.get('gate_failure_details', [])
                self._finalize_logged_candidate(
                    candidate, 'rejected',
                    rejection_reason=rejection_reason)
                self.sigCandidateRejected.emit(dict(candidate))
                return 'rejected'
            failed_str = ', '.join(attempt.get('gate_failures', [])) or 'unresolved'
            if hasattr(self, 'log'):
                self.log.info(
                    "Candidate '{0}' Stage 2 attempt {1}/{2} failed gates [{3}]. Retrying next attempt...".format(
                        candidate['candidate_id'], int(candidate.get('final_state_attempts', 0)),
                        int(self.stage2_max_attempts), failed_str))
            return 'retry'

        self._finalize_logged_candidate(candidate, 'unresolved',
                                        rejection_reason='unknown verifier stage')
        return 'unresolved'

    def _accept_candidate(self, candidate, attempt):
        analysis = attempt.get('optimizer2_xy', {})
        gaussian_xy = analysis.get('position_m')
        if gaussian_xy is None:
            self._finalize_logged_candidate(
                candidate, 'unresolved',
                rejection_reason='missing Gaussian centre at acceptance')
            return 'unresolved'
        z_value = candidate.get('current_seed_position_m',
                                candidate['seed_position_m'])[2]
        accepted_position = [float(gaussian_xy[0]), float(gaussian_xy[1]),
                             float(z_value)]
        candidate['accepted_position_m'] = accepted_position
        candidate['status'] = 'optically_verified'
        poi_name = self._candidate_poi_name(candidate)

        mode = self._effective_mode()
        registration_status = 'diagnostic_not_registered'

        # In hybrid and production modes, register the POI
        if mode in ('hybrid', 'production'):
            if bool(self.auto_register_poi) and self.poimanagerlogic() is not None:
                try:
                    self.poimanagerlogic().add_poi(
                        position=np.array(accepted_position), name=poi_name)
                    registration_status = 'registered'
                except Exception as error:
                    registration_status = 'registration_failed:{0}'.format(error)
                    candidate['status'] = 'registration_failed'
            else:
                registration_status = 'poi_manager_unavailable'

        self._finalize_logged_candidate(
            candidate, 'accepted',
            accepted_position_m=accepted_position,
            poi_name=poi_name,
            registration_status=registration_status)

        # Emit acceptance signal and log banner
        acceptance_banner = format_candidate_acceptance_banner(
            candidate, accepted_position, poi_name,
            attempt.get('gate_details_dict', {}), registration_status)
        self._log_and_print(acceptance_banner)

        opt2 = attempt.get('optimizer2_result')
        r2 = getattr(opt2, 'r_squared', None) if opt2 is not None else None
        if r2 is None and isinstance(opt2, dict):
            r2 = opt2.get('r_squared')
        sigma = getattr(opt2, 'sigma_m', None) if opt2 is not None else None
        if sigma is None and isinstance(opt2, dict):
            sigma = opt2.get('sigma_m')
        amp = getattr(opt2, 'amplitude', None) if opt2 is not None else None
        if amp is None and isinstance(opt2, dict):
            amp = opt2.get('amplitude')

        optical_stats = {
            'r_squared': r2,
            'sigma_m': sigma,
            'peak_fluorescence_cps': amp,
            'gate_details': attempt.get('gate_details_dict', {}),
        }

        accepted_record = {
            'candidate_id': candidate['candidate_id'],
            'candidate_label': candidate['candidate_label'],
            'accepted_position_m': accepted_position,
            'poi_name': poi_name,
            'registration_status': registration_status,
            'operating_mode': mode,
            'region_id': candidate.get('region_id', ''),
            'overall_score': candidate.get('overall_score'),
            'optical_stats': optical_stats,
            'stage1_attempts': candidate.get('stage1_attempts', 0),
            'final_state_attempts': candidate.get('final_state_attempts', 0),
        }
        self.sigCandidateAccepted.emit(accepted_record)
        return candidate['status']


    def _candidate_poi_name(self, candidate):
        region = _safe_slug(candidate.get('region_id') or 'R000', 'R000')
        token = _safe_slug(candidate.get('candidate_label') or
                           candidate.get('candidate_id'), 'candidate')
        return 'NV_{0}_{1}'.format(region, token)

    def _finalize_logged_candidate(self, candidate, final_status,
                                   accepted_position_m=None,
                                   rejection_reason=None, poi_name=None,
                                   registration_status=None):
        if candidate.get('_logged_final_decision'):
            return
        candidate['_logged_final_decision'] = True
        if self._poi_logger is not None:
            self._poi_logger.finalize_candidate(
                candidate['candidate_id'],
                final_status=final_status,
                accepted_position_m=accepted_position_m,
                rejection_reason=rejection_reason,
                poi_name=poi_name,
                registration_status=registration_status,
                metadata={
                    'stage1_attempts': candidate.get('stage1_attempts', 0),
                    'final_state_attempts': candidate.get('final_state_attempts', 0),
                    'diagnostic_only': bool(self.diagnostic_only),
                })

    def _finish_batch(self, status):
        if not self._active:
            return
        self._watchdog.stop()
        self._timeout_cleanup.stop()
        if status in ('stopped', 'timed_out'):
            for candidate in self._batch['candidates'][self._current_index + 1:]:
                if candidate['status'] == 'queued':
                    candidate['status'] = 'skipped'
                    self._finalize_logged_candidate(
                        candidate, 'skipped',
                        rejection_reason='batch {0}'.format(status))
        self._batch['status'] = status
        self._batch['finished_utc'] = _utc_timestamp()
        if self._poi_logger is not None:
            self._poi_logger.finish_run(status, metadata={'batch': self._batch})
        result = dict(self._batch)
        self._active = False
        self._active_tag = None
        self.sigVerificationFinished.emit(result)
