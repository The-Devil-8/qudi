# -*- coding: utf-8 -*-
"""
POI Extractor module for NV centre candidate detection and narrowing.

This module bridges the gap between ``CellRegionProcessor`` (which
identifies *where* to look — the processable cytoplasm zone) and the
``NVCandidateVerifier`` (which wraps ``OptimizerLogic`` to refine and
confirm NV positions).

Pipeline
--------
1. **Stage A** — CIP detection within the processable zone mask.
2. **Stage B** — Multi-metric scoring (SNR, circularity, contrast,
   fit quality, isolation, zone consistency).
3. **Stage C** — Adaptive narrowing via multi-gate filtering
   (quality floor, Otsu on scores, density cap).
4. **Stage D** — Spatial deconfliction (remove overlapping candidates).
5. **Stage E** — Ranking and result assembly.

Design decisions
----------------
* Plain Python class (like ``CellRegionProcessor``), **not** a Qudi
  ``GenericLogic`` subclass.  Will be promoted later when live hardware
  integration requires connectors and ``StatusVar``s.
* All physical units are metres (consistent with Qudi).
* Pixel coordinates use NumPy ``(row, col)`` convention.
* Leverages existing ``ConfocalImageAnalysis`` for CIP stages.

See documentation/automation/20_poi_extractor_module.md for full design.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at
<https://github.com/Ulm-IQO/qudi/>
"""

import time
import uuid
import numpy as np
from scipy.spatial.distance import cdist

from logic.image_analysis import ConfocalImageAnalysis

try:
    from skimage.filters import threshold_otsu
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


# ======================================================================
# Data containers
# ======================================================================

class POICandidate:
    """A scored, narrowed-down NV centre candidate ready for optimization.

    Attributes
    ----------
    candidate_id : str
        Unique identifier (e.g. ``'POI-a1b2c3'``).
    region_id : str
        Parent ``ScanRegion`` ID (empty if not provided).
    x, y, z_estimate : float
        Physical position in metres (sub-pixel refined).
    pixel_row, pixel_col : int
        Position in the scan image pixel grid.
    intensity : float
        Peak fluorescence count rate (counts/s).
    snr : float
        Signal-to-noise ratio relative to zone noise.
    circularity : float
        Spot shape score [0, 1].
    fit_quality : float
        Sub-pixel Gaussian refinement quality [0, 1].
    contrast : float
        Peak / local-background ratio.
    detection_confidence : float
        Weighted composite detection confidence [0, 1].
    isolation_score : float
        Spatial isolation from neighboring candidates [0, 1].
    zone_consistency : float
        Intensity consistency with zone statistics [0, 1].
    overall_score : float
        Final composite score for ranking [0, 1].
    rank : int
        Rank among strong candidates (1 = highest).
    classification : str
        ``'strong_candidate'`` | ``'marginal'`` | ``'rejected'``.
    rejection_reason : str
        Human-readable reason if classified as ``'rejected'``.
    edge_candidate : bool
        True if candidate is near the processable mask boundary.
    extraction_method : str
        Label for the method used (``'cip_zone_adaptive'``).
    """

    def __init__(self, candidate_id='', region_id='',
                 x=0.0, y=0.0, z_estimate=0.0,
                 pixel_row=0, pixel_col=0,
                 intensity=0.0, snr=0.0, circularity=0.0,
                 fit_quality=0.0, contrast=0.0):
        # Identity
        self.candidate_id = candidate_id or 'POI-{}'.format(
            uuid.uuid4().hex[:6])
        self.region_id = region_id

        # Physical position
        self.x = float(x)
        self.y = float(y)
        self.z_estimate = float(z_estimate)

        # Pixel position
        self.pixel_row = int(pixel_row)
        self.pixel_col = int(pixel_col)

        # Raw detection metrics
        self.intensity = float(intensity)
        self.snr = float(snr)
        self.circularity = float(circularity)
        self.fit_quality = float(fit_quality)
        self.contrast = float(contrast)

        # Composite scores (Stage B)
        self.detection_confidence = 0.0
        self.isolation_score = 0.0
        self.zone_consistency = 0.0

        # Overall ranking (Stage E)
        self.overall_score = 0.0
        self.rank = 0

        # Classification (Stage C)
        self.classification = 'pending'
        self.rejection_reason = ''
        self.edge_candidate = False
        self.extraction_method = 'cip_zone_adaptive'

    def to_dict(self):
        """Serialize to a plain dictionary."""
        return {
            'candidate_id': self.candidate_id,
            'region_id': self.region_id,
            'x': self.x,
            'y': self.y,
            'z_estimate': self.z_estimate,
            'pixel_row': self.pixel_row,
            'pixel_col': self.pixel_col,
            'intensity': self.intensity,
            'snr': self.snr,
            'circularity': self.circularity,
            'fit_quality': self.fit_quality,
            'contrast': self.contrast,
            'detection_confidence': self.detection_confidence,
            'isolation_score': self.isolation_score,
            'zone_consistency': self.zone_consistency,
            'overall_score': self.overall_score,
            'rank': self.rank,
            'classification': self.classification,
            'rejection_reason': self.rejection_reason,
            'edge_candidate': self.edge_candidate,
            'extraction_method': self.extraction_method,
        }


class POIExtractionResult:
    """Complete output of the POIExtractor pipeline.

    Attributes
    ----------
    candidates : list[POICandidate]
        All detected candidates (before narrowing).
    strong_candidates : list[POICandidate]
        High-confidence candidates to send to the Optimizer.
    marginal_candidates : list[POICandidate]
        Lower-confidence candidates kept for optional review.
    rejected_candidates : list[POICandidate]
        Candidates that failed one or more quality gates.
    stats : dict
        Summary statistics for the extraction.
    diagnostics : dict
        Internal pipeline diagnostics for debugging.
    """

    def __init__(self):
        self.candidates = []
        self.strong_candidates = []
        self.marginal_candidates = []
        self.rejected_candidates = []
        self.stats = {
            'total_detected': 0,
            'n_strong': 0,
            'n_marginal': 0,
            'n_rejected': 0,
            'detection_density_per_um2': 0.0,
            'mean_score': 0.0,
            'zone_coverage': 0.0,
        }
        self.diagnostics = {
            'noise_sigma': 0.0,
            'threshold_used': 0.0,
            'background_method': 'median_filter',
            'narrowing_method': 'otsu',
            'score_threshold': 0.0,
            'processing_time_s': 0.0,
        }


# ======================================================================
# Main module
# ======================================================================

class POIExtractor:
    """Extract and narrow down NV centre POI candidates from a cell region.

    Takes the output of ``CellRegionProcessor.process()`` (a
    ``CellProcessingResult`` containing the processable-zone mask and
    zone statistics) together with the raw close-scan image, and
    produces a ranked list of ``POICandidate`` objects.

    Example usage::

        from logic.poi_extractor import POIExtractor

        extractor = POIExtractor()
        result = extractor.extract(
            cell_result=cell_processing_result,
            image=close_scan_image,
        )
        for c in result.strong_candidates:
            print(c.candidate_id, c.x, c.y, c.overall_score)

    Parameters
    ----------
    **config
        Override any default configuration parameter.
    """

    # Default configuration
    _DEFAULTS = {
        # --- Detection (Stage A) ---
        'detection_threshold_sigma': 5.0,
        'min_spot_intensity': 1000.0,
        'spot_diameter_m': 1.5e-6,
        'background_filter_size': 15,
        'use_zone_adaptive_threshold': True,
        'max_candidates': 50,

        # --- Scoring weights (Stage B) ---
        'w_snr': 0.25,
        'w_shape': 0.15,
        'w_contrast': 0.20,
        'w_fit': 0.10,
        'w_isolation': 0.15,
        'w_consistency': 0.15,

        # --- Narrowing (Stage C) ---
        'min_snr': 3.0,
        'min_circularity': 0.4,
        'min_overall_score': 0.25,
        'narrowing_method': 'otsu',        # 'otsu' | 'percentile' | 'fixed'
        'percentile_threshold': 50,
        'fixed_score_threshold': 0.5,
        'max_strong_per_cell': 30,
        'max_density_per_um2': 0.5,

        # --- Spatial deconfliction (Stage D) ---
        'min_separation_factor': 1.0,      # × spot_diameter

        # --- Edge handling ---
        'edge_penalty': 0.2,
    }

    def __init__(self, **config):
        self._config = dict(self._DEFAULTS)
        self._config.update(config)
        self._cip = ConfocalImageAnalysis()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_config(self):
        """Return a copy of the current configuration."""
        return dict(self._config)

    def set_config(self, **kwargs):
        """Update configuration parameters."""
        for key, value in kwargs.items():
            if key in self._DEFAULTS:
                self._config[key] = value
            else:
                raise KeyError(
                    'Unknown config key: {!r}. '
                    'Valid keys: {}'.format(key, list(self._DEFAULTS.keys())))

    # ==================================================================
    #  PUBLIC API — main entry point
    # ==================================================================

    def extract(self, cell_result, image,
                x_coords=None, y_coords=None, z_current=0.0,
                scan_region=None, **kwargs):
        """Run the full POI extraction pipeline.

        Parameters
        ----------
        cell_result : CellProcessingResult
            Output of ``CellRegionProcessor.process()``.
        image : numpy.ndarray
            Close-scan image ``(ny, nx, 4)`` with channels
            ``[x, y, z, fluorescence]``.
        x_coords : numpy.ndarray, optional
            1-D array of X coordinates (metres) for each column.
            If *None*, derived from ``image[0, :, 0]``.
        y_coords : numpy.ndarray, optional
            1-D array of Y coordinates (metres) for each row.
            If *None*, derived from ``image[:, 0, 1]``.
        z_current : float
            Current Z focus plane (metres).
        scan_region : ScanRegion, optional
            Parent region for metadata (region_id).
        **kwargs
            Per-call configuration overrides.

        Returns
        -------
        POIExtractionResult
        """
        t0 = time.time()

        # Merge per-call overrides
        cfg = dict(self._config)
        for k, v in kwargs.items():
            if k in cfg:
                cfg[k] = v

        result = POIExtractionResult()
        region_id = ''
        if scan_region is not None:
            region_id = getattr(scan_region, 'region_id', '')

        # ------ Short-circuit if no processable zone ------
        zone_stats = cell_result.zone_stats
        if not zone_stats.get('processable', False):
            result.stats['total_detected'] = 0
            result.diagnostics['reason'] = zone_stats.get(
                'reason', 'no_processable_zone')
            result.diagnostics['processing_time_s'] = time.time() - t0
            return result

        processable_mask = cell_result.processable_mask

        # ------ Derive coordinates ------
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape

        if x_coords is None:
            x_coords = image[0, :, 0]
        if y_coords is None:
            y_coords = image[:, 0, 1]

        # Pixel sizes
        pixel_size_x = (abs(x_coords[-1] - x_coords[0]) / max(nx - 1, 1)
                        if nx > 1 and abs(x_coords[-1] - x_coords[0]) > 0
                        else cfg['spot_diameter_m'] / 6.0)
        pixel_size_y = (abs(y_coords[-1] - y_coords[0]) / max(ny - 1, 1)
                        if ny > 1 and abs(y_coords[-1] - y_coords[0]) > 0
                        else cfg['spot_diameter_m'] / 6.0)
        pixel_size = min(pixel_size_x, pixel_size_y)
        if pixel_size <= 0:
            pixel_size = cfg['spot_diameter_m'] / 6.0
        pixel_size_um = pixel_size * 1e6

        # Spot diameter in pixels
        spot_px = max(3, int(cfg['spot_diameter_m'] / pixel_size))
        if spot_px % 2 == 0:
            spot_px += 1

        # ------ Stage A: CIP detection within processable zone ------
        raw_candidates = self._detect_in_zone(
            fluor, processable_mask, x_coords, y_coords,
            z_current, spot_px, cfg, region_id)

        result.candidates = list(raw_candidates)
        result.stats['total_detected'] = len(raw_candidates)

        if len(raw_candidates) == 0:
            result.diagnostics['reason'] = 'no_detections_in_processable_zone'
            result.diagnostics['processing_time_s'] = time.time() - t0
            return result

        # ------ Stage B: Multi-metric scoring ------
        self._score_candidates(raw_candidates, zone_stats, fluor,
                               processable_mask, spot_px, cfg)

        # ------ Stage C: Adaptive narrowing ------
        strong, marginal, rejected = self._narrow_candidates(
            raw_candidates, zone_stats, pixel_size_um, cfg)

        # ------ Stage D: Spatial deconfliction ------
        min_sep_px = cfg['min_separation_factor'] * spot_px
        strong, deconf_rejected = self._spatial_deconflict(
            strong, min_sep_px)
        rejected.extend(deconf_rejected)

        # ------ Stage E: Ranking ------
        strong.sort(key=lambda c: c.overall_score, reverse=True)
        for rank, c in enumerate(strong, 1):
            c.rank = rank
            c.classification = 'strong_candidate'

        for c in marginal:
            c.classification = 'marginal'
        for c in rejected:
            if c.classification != 'rejected':
                c.classification = 'rejected'

        # ------ Populate result ------
        result.strong_candidates = strong
        result.marginal_candidates = marginal
        result.rejected_candidates = rejected

        proc_area_um2 = zone_stats.get('area_px', 1) * pixel_size_um ** 2
        n_strong = len(strong)
        result.stats.update({
            'n_strong': n_strong,
            'n_marginal': len(marginal),
            'n_rejected': len(rejected),
            'detection_density_per_um2': (
                n_strong / max(proc_area_um2, 1.0)),
            'mean_score': (
                float(np.mean([c.overall_score for c in strong]))
                if n_strong > 0 else 0.0),
            'zone_coverage': 0.0,  # placeholder — can be computed later
        })

        result.diagnostics['processing_time_s'] = time.time() - t0
        return result

    # ==================================================================
    #  STAGE A — CIP detection within processable zone
    # ==================================================================

    def _detect_in_zone(self, fluor, processable_mask, x_coords, y_coords,
                        z_current, spot_px, cfg, region_id):
        """Run the CIP pipeline on the full image, then post-filter
        to keep only detections inside the processable zone.

        This avoids edge artefacts from zeroing non-processable pixels
        before running the CIP filters.
        """
        cip = self._cip
        ny, nx = fluor.shape
        radius = max(1, spot_px // 2)

        # Stage A.1 — Background estimation & subtraction
        background = cip.estimate_background(
            fluor, kernel_size=cfg['background_filter_size'])
        corrected = cip.subtract_background(fluor, background)

        # Stage A.2 — Zone-adaptive noise estimation
        if cfg['use_zone_adaptive_threshold']:
            zone_corrected = corrected[processable_mask]
            if len(zone_corrected) > 0:
                zone_median = float(np.median(zone_corrected))
                zone_noise = float(
                    1.4826 * np.median(np.abs(zone_corrected - zone_median)))
            else:
                zone_median = 0.0
                zone_noise = float(cip.estimate_noise_level(corrected))
        else:
            zone_noise = float(cip.estimate_noise_level(corrected))
            zone_median = 0.0

        # Stage A.3 — Zone-adaptive threshold
        if zone_noise > 0:
            # Normal case: sigma-based threshold
            threshold = cfg['detection_threshold_sigma'] * zone_noise
        else:
            # Near-zero noise: background subtraction was very effective.
            # Use a percentile-based fallback on the corrected zone values
            # to only detect truly exceptional peaks.
            zone_corrected = corrected[processable_mask]
            if len(zone_corrected) > 0:
                threshold = float(np.percentile(zone_corrected, 99.5))
            else:
                threshold = cfg['min_spot_intensity']

        # Apply absolute minimum
        threshold = max(threshold, cfg['min_spot_intensity'])

        if cfg['use_zone_adaptive_threshold'] and zone_median > 0:
            min_threshold = zone_median + 2.0 * max(zone_noise, 1.0)
            threshold = max(threshold, min_threshold)

        mask = cip.threshold_intensity(corrected, threshold)

        if not np.any(mask):
            return []

        # Stage A.4 — Local maxima detection
        maxima_positions = cip.detect_local_maxima(
            corrected, mask, neighborhood_size=spot_px)

        if len(maxima_positions) == 0:
            return []

        # Stage A.5 — Post-filter: keep only within processable zone
        zone_maxima = []
        for pos in maxima_positions:
            r, c = int(pos[0]), int(pos[1])
            if 0 <= r < ny and 0 <= c < nx and processable_mask[r, c]:
                zone_maxima.append(pos)

        if len(zone_maxima) == 0:
            return []

        # Stage A.6 — Shape validation
        valid = []
        for pos in zone_maxima:
            r, c = int(pos[0]), int(pos[1])
            is_ok, circ = cip.validate_spot_shape(corrected, r, c, radius)
            if is_ok:
                valid.append((r, c, circ))

        if len(valid) == 0:
            return []

        # Stage A.7 — Spatial clustering
        positions = np.array([(r, c) for r, c, _ in valid])
        intensities = np.array([corrected[r, c] for r, c, _ in valid])
        circ_map = {(r, c): ci for r, c, ci in valid}

        clustered = cip.cluster_detections(
            positions, intensities, min_distance=spot_px)

        # Stage A.8 — Sub-pixel Gaussian refinement + candidate creation
        candidates = []
        for idx, (pos, intensity) in enumerate(
                clustered[:cfg['max_candidates']]):
            r, c = int(pos[0]), int(pos[1])
            refined = cip.refine_position_gaussian_2d(
                corrected, r, c, radius,
                x_coords=x_coords, y_coords=y_coords)

            x_phys = (refined['x'] if refined['x'] is not None
                      else float(x_coords[min(c, nx - 1)]))
            y_phys = (refined['y'] if refined['y'] is not None
                      else float(y_coords[min(r, ny - 1)]))

            circ = circ_map.get((r, c), 0.5)
            snr_val = float(intensity / zone_noise) if zone_noise > 0 else 0.0
            contrast_val = float(
                cip.compute_intensity_contrast(corrected, r, c, radius))

            cand = POICandidate(
                region_id=region_id,
                x=x_phys, y=y_phys, z_estimate=z_current,
                pixel_row=r, pixel_col=c,
                intensity=float(intensity),
                snr=snr_val,
                circularity=circ,
                fit_quality=refined['quality'],
                contrast=contrast_val,
            )
            candidates.append(cand)

        return candidates

    # ==================================================================
    #  STAGE B — Multi-metric scoring
    # ==================================================================

    def _score_candidates(self, candidates, zone_stats, fluor,
                          processable_mask, spot_px, cfg):
        """Compute composite scores for each candidate in-place."""
        if len(candidates) == 0:
            return

        # Gather all positions for isolation scoring
        all_positions = np.array(
            [[c.pixel_row, c.pixel_col] for c in candidates], dtype=float)

        for c in candidates:
            # B.1 — SNR score (saturates at SNR=20)
            snr_score = min(1.0, max(0.0, c.snr / 20.0))

            # B.2 — Shape / circularity score
            shape_score = max(0.0, min(1.0, c.circularity))

            # B.3 — Contrast score
            # For a broad Gaussian (sigma ~2px) at radius=2, border intensity is ~40-60% of peak,
            # so contrast is ~1.5 - 2.5. Very high contrast means it's a hot pixel.
            if c.contrast < 1.1:
                contrast_score = 0.0
            elif c.contrast > 1.5:
                contrast_score = 1.0
            else:
                contrast_score = (c.contrast - 1.1) / 0.4

            # B.4 — Fit quality score
            # The simple metric in refine_position_gaussian_2d often yields ~0.15-0.3 for
            # broad NVs because the patch edges are still bright.
            if c.fit_quality < 0.1:
                fit_score = 0.0
            elif c.fit_quality > 0.4:
                fit_score = 1.0
            else:
                fit_score = (c.fit_quality - 0.1) / 0.3

            # B.5 — Isolation score
            iso = self._compute_isolation_score(
                np.array([c.pixel_row, c.pixel_col]),
                all_positions, spot_px)
            c.isolation_score = iso

            # B.6 — Zone consistency score
            raw_intensity = float(fluor[c.pixel_row, c.pixel_col])
            zc = self._compute_zone_consistency(raw_intensity, zone_stats)
            c.zone_consistency = zc

            # B.7 — Detection confidence (existing formula)
            c.detection_confidence = float(
                self._cip.compute_detection_confidence(
                    snr=c.snr, circularity=c.circularity,
                    fit_quality=c.fit_quality))

            # B.8 — Edge penalty
            edge = self._is_edge_candidate(
                c.pixel_row, c.pixel_col, processable_mask, spot_px)
            c.edge_candidate = edge

            # B.9 — Weighted composite score
            score = (
                cfg['w_snr'] * snr_score
                + cfg['w_shape'] * shape_score
                + cfg['w_contrast'] * contrast_score
                + cfg['w_fit'] * fit_score
                + cfg['w_isolation'] * iso
                + cfg['w_consistency'] * zc
            )
            if edge:
                score *= (1.0 - cfg['edge_penalty'])

            c.overall_score = float(score)

    @staticmethod
    def _compute_isolation_score(candidate_pos, all_positions, spot_px):
        """Score how isolated a candidate is from its neighbors.

        Returns a value in [0, 1] where 1 = perfectly isolated.
        """
        distances = cdist([candidate_pos], all_positions)[0]
        # Exclude self (distance ≈ 0)
        distances = distances[distances > 0.5]

        if len(distances) == 0:
            return 1.0

        nearest = float(np.min(distances))
        isolation = min(1.0, max(0.0,
                                 (nearest - spot_px) / (3.0 * spot_px)))
        return isolation

    @staticmethod
    def _compute_zone_consistency(candidate_intensity, zone_stats):
        """Score how consistent a candidate's intensity is with the zone.

        A single NV should be brighter than zone median but not absurdly
        bright (which would suggest an unresolved cluster fragment).

        Returns a value in [0, 1].
        """
        z_median = zone_stats.get('median_intensity', 0.0)
        z_std = zone_stats.get('std_intensity', 0.0)

        if z_std <= 0:
            return 0.5

        z_score = (candidate_intensity - z_median) / z_std

        if z_score < 1.5:
            return 0.2   # too dim — probably background fluctuation
        elif z_score < 3.0:
            return 0.6   # marginal
        elif z_score <= 30.0:
            return 1.0   # ideal range for single NV
        elif z_score <= 100.0:
            return 0.8   # very bright
        else:
            return 0.5   # extremely bright, maybe unresolved cluster

    @staticmethod
    def _is_edge_candidate(row, col, processable_mask, spot_px):
        """Check if a candidate is near the processable mask boundary."""
        half = max(1, spot_px // 2)
        ny, nx = processable_mask.shape
        r1, r2 = max(0, row - half), min(ny, row + half + 1)
        c1, c2 = max(0, col - half), min(nx, col + half + 1)
        patch = processable_mask[r1:r2, c1:c2]
        # If any pixel in the local neighbourhood is outside the mask,
        # the candidate is near the boundary.
        return bool(not np.all(patch))

    # ==================================================================
    #  STAGE C — Adaptive narrowing
    # ==================================================================

    def _narrow_candidates(self, candidates, zone_stats, pixel_size_um, cfg):
        """Apply multi-gate adaptive narrowing.

        Returns (strong, marginal, rejected) lists.
        """
        strong = []
        marginal = []
        rejected = []

        # ------- Gate 1: Absolute quality floor -------
        surviving = []
        for c in candidates:
            reasons = []
            if c.snr < cfg['min_snr']:
                reasons.append('snr={:.1f}<{}'.format(c.snr, cfg['min_snr']))
            if c.circularity < cfg['min_circularity']:
                reasons.append('circ={:.2f}<{}'.format(
                    c.circularity, cfg['min_circularity']))
            if c.overall_score < cfg['min_overall_score']:
                reasons.append('score={:.3f}<{}'.format(
                    c.overall_score, cfg['min_overall_score']))

            if reasons:
                c.classification = 'rejected'
                c.rejection_reason = 'gate1_quality_floor: ' + '; '.join(
                    reasons)
                rejected.append(c)
            else:
                surviving.append(c)

        if len(surviving) == 0:
            return strong, marginal, rejected

        # ------- Gate 2: Score separation -------
        method = cfg['narrowing_method']
        scores = np.array([c.overall_score for c in surviving])

        if method == 'otsu' and HAS_SKIMAGE and len(scores) >= 4:
            try:
                score_threshold = float(threshold_otsu(scores))
            except (ValueError, RuntimeError):
                score_threshold = float(np.median(scores))
        elif method == 'percentile':
            score_threshold = float(
                np.percentile(scores, cfg['percentile_threshold']))
        elif method == 'fixed':
            score_threshold = float(cfg['fixed_score_threshold'])
        else:
            # Fallback: use median
            score_threshold = float(np.median(scores))

        for c in surviving:
            if c.overall_score >= score_threshold:
                strong.append(c)
            else:
                c.classification = 'marginal'
                c.rejection_reason = (
                    'gate2_{}: score={:.3f}<threshold={:.3f}'.format(
                        method, c.overall_score, score_threshold))
                marginal.append(c)

        # ------- Gate 3: Density cap -------
        max_abs = cfg['max_strong_per_cell']
        if len(strong) > max_abs:
            strong.sort(key=lambda c: c.overall_score, reverse=True)
            overflow = strong[max_abs:]
            strong = strong[:max_abs]
            for c in overflow:
                c.classification = 'marginal'
                c.rejection_reason = (
                    'gate3_density_cap: >{}'.format(max_abs))
            marginal.extend(overflow)

        return strong, marginal, rejected

    # ==================================================================
    #  STAGE D — Spatial deconfliction
    # ==================================================================

    def _spatial_deconflict(self, candidates, min_separation_px):
        """Remove spatially redundant candidates (keep highest score).

        Returns (kept, rejected) lists.
        """
        if len(candidates) <= 1:
            return list(candidates), []

        # Sort by overall_score descending
        candidates = sorted(
            candidates, key=lambda c: c.overall_score, reverse=True)
        kept = []
        kept_positions = []
        removed = []

        for c in candidates:
            pos = np.array([c.pixel_row, c.pixel_col], dtype=float)
            if len(kept_positions) == 0:
                kept.append(c)
                kept_positions.append(pos)
                continue

            dists = cdist([pos], np.array(kept_positions))[0]
            if float(np.min(dists)) >= min_separation_px:
                kept.append(c)
                kept_positions.append(pos)
            else:
                c.classification = 'rejected'
                c.rejection_reason = (
                    'gate4_deconflict: '
                    'too_close_to_higher_scored_candidate '
                    '(dist={:.1f}<{:.1f}px)'.format(
                        float(np.min(dists)), min_separation_px))
                removed.append(c)

        return kept, removed

    # ==================================================================
    #  Visualization helper
    # ==================================================================

    def get_candidate_overlay(self, result, image_shape):
        """Generate RGBA overlay showing candidate classifications.

        Parameters
        ----------
        result : POIExtractionResult
            The extraction result.
        image_shape : tuple
            ``(ny, nx)`` of the image.

        Returns
        -------
        numpy.ndarray
            ``(ny, nx, 4)`` float RGBA overlay.
            - Green circles: strong candidates
            - Yellow circles: marginal candidates
            - Red markers: rejected candidates
        """
        ny, nx = image_shape
        overlay = np.zeros((ny, nx, 4), dtype=float)

        def _draw_circle(overlay, row, col, radius, color):
            """Draw a filled circle on the overlay."""
            rr, cc = np.ogrid[-radius:radius + 1, -radius:radius + 1]
            mask = rr ** 2 + cc ** 2 <= radius ** 2
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    if dr ** 2 + dc ** 2 <= radius ** 2:
                        r, c = row + dr, col + dc
                        if 0 <= r < ny and 0 <= c < nx:
                            overlay[r, c] = color

        # Strong → green
        for c in result.strong_candidates:
            _draw_circle(overlay, c.pixel_row, c.pixel_col, 3,
                         [0.0, 1.0, 0.0, 0.6])

        # Marginal → yellow
        for c in result.marginal_candidates:
            _draw_circle(overlay, c.pixel_row, c.pixel_col, 2,
                         [1.0, 1.0, 0.0, 0.5])

        # Rejected → red
        for c in result.rejected_candidates:
            _draw_circle(overlay, c.pixel_row, c.pixel_col, 2,
                         [1.0, 0.0, 0.0, 0.4])

        return overlay
