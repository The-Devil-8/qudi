# -*- coding: utf-8 -*-
"""
SampleCharacterizationEngine — Intelligent Pre-Segmentation Algorithm Router

This module sits BEFORE cell segmentation in the automation pipeline. It
analyzes raw confocal image statistics to classify the sample type (sparse
vs. dense) and selects the optimal segmentation algorithm.

Architecture
------------
1. Compute 6 statistical metrics from the raw image.
2. Each metric votes 'sparse' or 'dense'.
3. Classification confidence = fraction of agreeing votes.
4. If confidence > 0.85: fast-path (run only recommended algorithm).
5. If confidence ≤ 0.85: run BOTH algorithms, score results, pick winner.

Algorithms
----------
- **AlgoA** (``CellSegmentationSparse``): Seeded hysteresis + MAD noise floor.
  Best for sparse/well-separated samples (Confocal1, Confocal2).
- **AlgoB** (``CellSegmentationLogic``): Global-gated local adaptive.
  Best for dense/cluttered samples (Confocal3, Confocal4).

Integration
-----------
Replaces the direct ``ROISegmentationLogic.segment_roi()`` call in
``MultiScaleAutoNVFinderLogic._on_macro_scan_complete()`` (line ~407).
Output is adapted to the same dict format expected by
``ScanRegionQueue.extract_regions_from_segmentation()``.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple

from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    grey_opening,
    label,
    find_objects,
    binary_fill_holes,
    binary_opening,
)

try:
    from skimage.filters import threshold_otsu, sobel
    from skimage.measure import regionprops
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

# Import both segmentation algorithms
from logic.cell_segmentation_sparse import CellSegmentationSparse
from logic.cell_segmentation_logic import CellSegmentationLogic

logger = logging.getLogger(__name__)


# ======================================================================
# Enums & Data Classes
# ======================================================================

class SampleType(Enum):
    """Classification of sample density."""
    SPARSE = 'sparse'
    DENSE = 'dense'
    AMBIGUOUS = 'ambiguous'


class AlgorithmChoice(Enum):
    """Available segmentation algorithms."""
    ALGO_A_SPARSE = 'algo_a'
    ALGO_B_DENSE = 'algo_b'


@dataclass
class SampleCharacterization:
    """Statistical fingerprint of a confocal sample image.

    Attributes
    ----------
    foreground_fraction : float
        Fraction of image pixels above noise floor (0.0–1.0).
    estimated_cell_count : int
        Approximate number of distinct bright regions.
    dominant_component_fraction : float
        Fraction of total foreground area occupied by the largest component.
    inter_cell_gap_ratio : float
        Mean nearest-neighbor centroid distance / mean cell equiv diameter.
    histogram_bimodality : float
        Ashman's D coefficient (>2.0 = bimodal → sparse).
    intensity_dynamic_range : float
        log10(P99/P10) — measures spike severity.
    edge_density : float
        Fraction of pixels with strong gradients.
    sample_type : SampleType
        Classification result.
    confidence : float
        Classification confidence (0.0–1.0).
    recommended_algorithm : AlgorithmChoice
        The algorithm recommended by classification.
    metric_votes : dict
        Per-metric vote tally for transparency.
    """
    foreground_fraction: float = 0.0
    estimated_cell_count: int = 0
    dominant_component_fraction: float = 0.0
    inter_cell_gap_ratio: float = 0.0
    histogram_bimodality: float = 0.0
    intensity_dynamic_range: float = 0.0
    edge_density: float = 0.0
    sample_type: SampleType = SampleType.AMBIGUOUS
    confidence: float = 0.0
    recommended_algorithm: AlgorithmChoice = AlgorithmChoice.ALGO_A_SPARSE
    metric_votes: Dict[str, str] = field(default_factory=dict)


@dataclass
class SegmentationResult:
    """Unified result from any segmentation algorithm.

    Attributes
    ----------
    mask : numpy.ndarray
        Boolean 2D mask of all cell bodies.
    labeled : numpy.ndarray
        Integer-labeled instance map.
    cell_boxes : list of dict
        Per-cell bounding box dictionaries.
    algorithm_used : AlgorithmChoice
        Which algorithm produced this result.
    quality_score : float
        Quality score (0.0–1.0) from the result evaluator.
    """
    mask: np.ndarray = None
    labeled: np.ndarray = None
    cell_boxes: List[Dict] = field(default_factory=list)
    algorithm_used: AlgorithmChoice = AlgorithmChoice.ALGO_A_SPARSE
    quality_score: float = 0.0


@dataclass
class CharacterizationResult:
    """Complete output of the SampleCharacterizationEngine.

    Attributes
    ----------
    characterization : SampleCharacterization
        Statistical fingerprint and classification.
    segmentation : SegmentationResult
        The best segmentation result.
    duel_performed : bool
        Whether both algorithms were run and compared.
    all_scores : dict or None
        Quality scores from both algorithms if duel was performed.
    segmentation_dict : dict
        ROI-compatible output for ScanRegionQueue.
    """
    characterization: SampleCharacterization = None
    segmentation: SegmentationResult = None
    duel_performed: bool = False
    all_scores: Optional[Dict[str, float]] = None
    segmentation_dict: Dict = field(default_factory=dict)


# ======================================================================
# Main Engine
# ======================================================================

class SampleCharacterizationEngine:
    """Intelligent pre-segmentation engine for confocal microscopy.

    Analyzes raw confocal images to classify sample density and select
    the optimal cell segmentation algorithm.

    Parameters
    ----------
    confidence_threshold : float, optional
        Minimum classification confidence for fast-path bypass.
        Below this threshold, both algorithms are run and compared.
        Default: 0.85.
    min_cell_area_um2 : float, optional
        Minimum cell area in µm² for segmentation algorithms.
        Default: 30.0.

    Examples
    --------
    >>> engine = SampleCharacterizationEngine()
    >>> result = engine.characterize_and_segment(image)
    >>> print(result.characterization.sample_type)
    SampleType.SPARSE
    >>> roi_dict = result.segmentation_dict
    """

    # Metric decision boundaries (calibrated for Confocal1–4)
    FOREGROUND_FRACTION_BOUNDARY = 0.30
    DOMINANT_COMPONENT_BOUNDARY = 0.50
    BIMODALITY_BOUNDARY = 2.0
    GAP_RATIO_BOUNDARY = 0.5
    EDGE_DENSITY_BOUNDARY = 0.065
    CELL_COUNT_BOUNDARY = 15

    def __init__(self, confidence_threshold=0.85, min_cell_area_um2=30.0):
        self._confidence_threshold = confidence_threshold
        self._min_cell_area_um2 = min_cell_area_um2
        self._algo_a = CellSegmentationSparse()
        self._algo_b = CellSegmentationLogic()

    # ==================================================================
    # Public API
    # ==================================================================

    def characterize_and_segment(self, image, min_cell_area_um2=None,
                                 **kwargs):
        """Characterize the sample and run optimal segmentation.

        This is the main entry point. It computes image statistics,
        classifies the sample, and runs the best algorithm (or both
        if classification confidence is low).

        Parameters
        ----------
        image : numpy.ndarray
            3D array (ny, nx, 4) from parse_dat_file. Channel 3 is
            fluorescence intensity.
        min_cell_area_um2 : float, optional
            Override minimum cell area. Defaults to constructor value.
        **kwargs
            Additional keyword arguments passed to segmentation algorithms.

        Returns
        -------
        CharacterizationResult
            Complete result with characterization, segmentation, and
            ROI-compatible dict for ScanRegionQueue.
        """
        if min_cell_area_um2 is None:
            min_cell_area_um2 = self._min_cell_area_um2

        # Step 1: Characterize the sample
        characterization = self.characterize_sample(image)

        logger.info(
            'Sample classified as %s (confidence=%.2f, algo=%s). '
            'Votes: %s',
            characterization.sample_type.value,
            characterization.confidence,
            characterization.recommended_algorithm.value,
            characterization.metric_votes
        )

        # Step 2: Decide execution strategy
        duel_performed = False
        all_scores = None

        if characterization.confidence >= self._confidence_threshold:
            # Fast path: run only the recommended algorithm
            logger.info('High confidence (%.2f >= %.2f). Fast-path: %s only.',
                        characterization.confidence,
                        self._confidence_threshold,
                        characterization.recommended_algorithm.value)

            if characterization.recommended_algorithm == AlgorithmChoice.ALGO_A_SPARSE:
                best = self._run_algo_a(image, min_cell_area_um2, **kwargs)
            else:
                best = self._run_algo_b(image, min_cell_area_um2, **kwargs)
        else:
            # Duel: run both algorithms and pick the winner
            logger.info(
                'Low confidence (%.2f < %.2f). Running algorithm duel.',
                characterization.confidence, self._confidence_threshold)

            result_a = self._run_algo_a(image, min_cell_area_um2, **kwargs)
            result_b = self._run_algo_b(image, min_cell_area_um2, **kwargs)

            # Score both results
            result_a.quality_score = self._score_result(
                result_a.mask, result_a.labeled, result_a.cell_boxes, image)
            result_b.quality_score = self._score_result(
                result_b.mask, result_b.labeled, result_b.cell_boxes, image)

            all_scores = {
                'algo_a': result_a.quality_score,
                'algo_b': result_b.quality_score,
            }
            duel_performed = True

            if result_a.quality_score >= result_b.quality_score:
                best = result_a
                logger.info(
                    'Duel winner: AlgoA (score=%.3f vs %.3f)',
                    result_a.quality_score, result_b.quality_score)
            else:
                best = result_b
                logger.info(
                    'Duel winner: AlgoB (score=%.3f vs %.3f)',
                    result_b.quality_score, result_a.quality_score)

        # Step 3: Adapt result format for ScanRegionQueue compatibility
        seg_dict = self._adapt_result_format(
            best.mask, best.labeled, best.cell_boxes, image)

        return CharacterizationResult(
            characterization=characterization,
            segmentation=best,
            duel_performed=duel_performed,
            all_scores=all_scores,
            segmentation_dict=seg_dict,
        )

    def characterize_sample(self, image):
        """Compute all classification metrics from the raw image.

        Parameters
        ----------
        image : numpy.ndarray
            3D array (ny, nx, 4). Channel 3 is fluorescence.

        Returns
        -------
        SampleCharacterization
            Statistical fingerprint with classification result.
        """
        fluor = image[:, :, 3].astype(float)
        ny, nx = fluor.shape

        # --- Shared preprocessing ---
        # Log transform to compress NV spike dynamic range
        fluor_clean = np.maximum(fluor, 0.0)
        log_fluor = np.log10(fluor_clean + 1.0)

        # P92 Winsorization to cap extreme NV spikes
        p92 = np.percentile(log_fluor, 92)
        clipped = np.minimum(log_fluor, p92)

        # Background floor flattening via Morphological White Top-Hat
        # grey_opening captures substrate baseline without erasing dense cell plateaus
        bg_floor = grey_opening(clipped, size=(51, 51))
        subtracted = np.maximum(clipped - bg_floor, 0.0)

        # Quick preliminary binary mask (for CCA-based metrics)
        nonzero = subtracted[subtracted > 0]
        if len(nonzero) < 20:
            # Nearly empty image — classify as sparse
            return SampleCharacterization(
                sample_type=SampleType.SPARSE,
                confidence=1.0,
                recommended_algorithm=AlgorithmChoice.ALGO_A_SPARSE,
                metric_votes={'all': 'sparse (empty image)'},
            )

        if HAS_SKIMAGE:
            try:
                thresh = threshold_otsu(nonzero)
            except Exception:
                thresh = np.percentile(nonzero, 50)
        else:
            thresh = np.percentile(nonzero, 50)

        binary_mask = subtracted > thresh
        binary_mask = binary_fill_holes(binary_mask)
        binary_mask = binary_opening(binary_mask, iterations=2)

        # --- Compute metrics ---
        fg_frac = self._compute_foreground_fraction(binary_mask)
        cell_count, labeled_mask = self._estimate_cell_count(binary_mask)
        dom_frac = self._compute_dominant_component_fraction(
            labeled_mask, binary_mask)
        gap_ratio = self._compute_inter_cell_gap_ratio(
            labeled_mask, binary_mask)
        bimodality = self._compute_histogram_bimodality(clipped)
        dyn_range = self._compute_dynamic_range(fluor)
        # Edge density computed on background-subtracted image to avoid
        # counting smooth substrate gradients as cell boundaries.
        edge_dens = self._compute_edge_density(subtracted)

        # --- Vote-based classification ---
        sample_type, confidence, algo, votes = self._classify(
            fg_frac, cell_count, dom_frac, gap_ratio,
            bimodality, edge_dens)

        return SampleCharacterization(
            foreground_fraction=fg_frac,
            estimated_cell_count=cell_count,
            dominant_component_fraction=dom_frac,
            inter_cell_gap_ratio=gap_ratio,
            histogram_bimodality=bimodality,
            intensity_dynamic_range=dyn_range,
            edge_density=edge_dens,
            sample_type=sample_type,
            confidence=confidence,
            recommended_algorithm=algo,
            metric_votes=votes,
        )

    # ==================================================================
    # Metric Computation Methods
    # ==================================================================

    @staticmethod
    def _compute_foreground_fraction(binary_mask):
        """Fraction of image pixels classified as foreground.

        Parameters
        ----------
        binary_mask : numpy.ndarray
            Boolean mask or 2D/3D image array.

        Returns
        -------
        float
            Foreground fraction (0.0–1.0).
        """
        if binary_mask.ndim == 3 and binary_mask.shape[2] == 4:
            fluor = binary_mask[:, :, 3].astype(float)
            nz = fluor[fluor > 0]
            if len(nz) < 20:
                return 0.0
            if HAS_SKIMAGE:
                try:
                    t = threshold_otsu(nz)
                except Exception:
                    t = np.percentile(nz, 50)
            else:
                t = np.percentile(nz, 50)
            binary_mask = fluor > t

        total = binary_mask.size
        if total == 0:
            return 0.0
        return float(np.sum(binary_mask > 0)) / total

    @staticmethod
    def _estimate_cell_count(binary_mask):
        """Count connected components in the preliminary mask.

        Parameters
        ----------
        binary_mask : numpy.ndarray
            Boolean mask from preliminary thresholding.

        Returns
        -------
        tuple of (int, numpy.ndarray)
            (cell_count, labeled_mask)
        """
        if binary_mask.ndim == 3 and binary_mask.shape[2] == 4:
            binary_mask = binary_mask[:, :, 3] > 0
        labeled_mask, n_features = label(binary_mask > 0)
        return n_features, labeled_mask

    @staticmethod
    def _compute_dominant_component_fraction(labeled_mask, binary_mask):
        """Fraction of total foreground in the largest connected component.

        In sparse samples, cells are separate and no single one dominates.
        In dense samples, overlapping cells coalesce into one mega-blob.

        Parameters
        ----------
        labeled_mask : numpy.ndarray
            Integer-labeled connected components.
        binary_mask : numpy.ndarray
            Boolean foreground mask.

        Returns
        -------
        float
            Dominant component fraction (0.0–1.0).
        """
        if binary_mask.ndim == 3 and binary_mask.shape[2] == 4:
            binary_mask = binary_mask[:, :, 3] > 0
        total_fg = np.sum(binary_mask > 0)
        if total_fg == 0:
            return 0.0

        # Count pixels per label (skip label 0 = background)
        unique_labels = np.unique(labeled_mask)
        unique_labels = unique_labels[unique_labels > 0]
        if len(unique_labels) == 0:
            return 0.0

        max_area = 0
        for lbl in unique_labels:
            area = np.sum(labeled_mask == lbl)
            if area > max_area:
                max_area = area

        return float(max_area) / float(total_fg)

    @staticmethod
    def _compute_inter_cell_gap_ratio(labeled_mask, binary_mask):
        """Ratio of mean inter-cell gap to mean cell diameter.

        Parameters
        ----------
        labeled_mask : numpy.ndarray
            Integer-labeled connected components.
        binary_mask : numpy.ndarray
            Boolean foreground mask.

        Returns
        -------
        float
            Gap ratio. High (>1.0) = well-separated, Low (<0.5) = dense.
        """
        unique_labels = np.unique(labeled_mask)
        unique_labels = unique_labels[unique_labels > 0]

        if len(unique_labels) < 2:
            # Single or no cells: treat as sparse (large gap)
            return 5.0

        # Compute centroids and equivalent diameters
        centroids = []
        diameters = []
        for lbl in unique_labels:
            component = (labeled_mask == lbl)
            area = float(np.sum(component))
            rows, cols = np.where(component)
            centroid = (float(np.mean(rows)), float(np.mean(cols)))
            centroids.append(centroid)
            # Equivalent circle diameter: d = 2 * sqrt(A / pi)
            diameters.append(2.0 * np.sqrt(area / np.pi))

        centroids = np.array(centroids)
        mean_diameter = np.mean(diameters)
        if mean_diameter < 1e-9:
            return 5.0

        # Compute nearest-neighbor distances between centroids
        nn_dists = []
        for i in range(len(centroids)):
            dists = np.sqrt(np.sum((centroids - centroids[i]) ** 2, axis=1))
            dists[i] = np.inf  # exclude self
            nn_dists.append(np.min(dists))

        mean_nn_dist = np.mean(nn_dists)
        return float(mean_nn_dist / mean_diameter)

    @staticmethod
    def _compute_histogram_bimodality(clipped_log):
        """Ashman's D bimodality coefficient.

        Measures the separation between two peaks (substrate vs. cell)
        in the intensity histogram. High D (>2.0) indicates clear
        bimodality (sparse). Low D (<1.5) indicates continuous
        distribution (dense).

        Parameters
        ----------
        clipped_log : numpy.ndarray
            Log-transformed, Winsorized 2D intensity image or 3D scan array.

        Returns
        -------
        float
            Ashman's D coefficient.
        """
        if clipped_log.ndim == 3 and clipped_log.shape[2] == 4:
            clipped_log = clipped_log[:, :, 3]
        values = clipped_log.ravel().astype(float)
        values = values[values > 0]  # exclude exact zeros (dead pixels)

        if len(values) < 20:
            return 0.0

        # Split at Otsu threshold
        if HAS_SKIMAGE:
            try:
                thresh = threshold_otsu(values)
            except Exception:
                thresh = np.median(values)
        else:
            thresh = np.median(values)

        low = values[values <= thresh]
        high = values[values > thresh]

        if len(low) < 5 or len(high) < 5:
            return 0.0

        mu1 = np.mean(low)
        mu2 = np.mean(high)
        sig1 = np.std(low)
        sig2 = np.std(high)

        pooled = np.sqrt(0.5 * (sig1 ** 2 + sig2 ** 2))
        if pooled < 1e-9:
            return 0.0

        return float(abs(mu2 - mu1) / pooled)

    @staticmethod
    def _compute_dynamic_range(fluor):
        """Intensity dynamic range: log10(P99 / P10).

        Parameters
        ----------
        fluor : numpy.ndarray
            Raw 2D fluorescence array or 3D scan array.

        Returns
        -------
        float
            Dynamic range in decades.
        """
        if fluor.ndim == 3 and fluor.shape[2] == 4:
            fluor = fluor[:, :, 3]
        p10 = np.percentile(fluor, 10)
        p99 = np.percentile(fluor, 99)
        if p10 <= 0:
            p10 = 1.0
        return float(np.log10(max(p99, 1.0) / max(p10, 1.0)))

    @staticmethod
    def _compute_edge_density(clipped_log):
        """Fraction of pixels with strong intensity gradients.

        Dense samples have many cell boundaries packed together,
        producing high edge density. Sparse samples have few boundaries.

        Parameters
        ----------
        clipped_log : numpy.ndarray
            Log-transformed, Winsorized 2D intensity image or 3D scan array.

        Returns
        -------
        float
            Edge density (0.0–1.0).
        """
        if clipped_log.ndim == 3 and clipped_log.shape[2] == 4:
            clipped_log = clipped_log[:, :, 3]
        if HAS_SKIMAGE:
            edges = sobel(clipped_log)
        else:
            # Manual Sobel approximation using numpy
            gy = np.diff(clipped_log, axis=0, prepend=0)
            gx = np.diff(clipped_log, axis=1, prepend=0)
            edges = np.sqrt(gx ** 2 + gy ** 2)

        # Threshold at 3× median of edge magnitudes.
        med_edge = np.median(edges[edges > 0]) if np.any(edges > 0) else 1.0
        strong_edges = edges > (3.0 * med_edge)
        return float(np.sum(strong_edges)) / max(edges.size, 1)

    # ==================================================================
    # Classification Logic
    # ==================================================================

    def _classify(self, fg_frac, cell_count, dom_frac, gap_ratio,
                  bimodality, edge_density):
        """Vote-based classification using metric decision boundaries.

        Each metric votes 'sparse' or 'dense'. Classification confidence
        is the fraction of metrics agreeing with the majority.

        Parameters
        ----------
        fg_frac : float
            Foreground fraction.
        cell_count : int
            Estimated cell count.
        dom_frac : float
            Dominant component fraction.
        gap_ratio : float
            Inter-cell gap ratio.
        bimodality : float
            Ashman's D coefficient.
        edge_density : float
            Edge density fraction.

        Returns
        -------
        tuple of (SampleType, float, AlgorithmChoice, dict)
            (sample_type, confidence, recommended_algorithm, metric_votes)
        """
        votes = {}
        sparse_count = 0
        dense_count = 0

        # Metric 1: Foreground fraction
        is_high_fg = fg_frac >= self.FOREGROUND_FRACTION_BOUNDARY
        if not is_high_fg:
            votes['foreground_fraction'] = 'sparse'
            sparse_count += 1
        else:
            votes['foreground_fraction'] = 'dense'
            dense_count += 1

        # Metric 2: Cell count
        if is_high_fg and cell_count <= 2:
            # Confluent sheet / mega-blob covering > 30% of FOV
            votes['cell_count'] = 'dense (confluent sheet)'
            dense_count += 1
        elif cell_count <= self.CELL_COUNT_BOUNDARY:
            votes['cell_count'] = 'sparse'
            sparse_count += 1
        else:
            votes['cell_count'] = 'dense'
            dense_count += 1

        # Metric 3: Dominant component fraction
        if is_high_fg and cell_count <= 2:
            votes['dominant_component'] = 'dense (confluent sheet)'
            dense_count += 1
        elif cell_count <= 2:
            # 1-2 small isolated cells
            votes['dominant_component'] = 'sparse (exempt: ≤2 small cells)'
            sparse_count += 1
        elif dom_frac < self.DOMINANT_COMPONENT_BOUNDARY:
            votes['dominant_component'] = 'sparse'
            sparse_count += 1
        else:
            votes['dominant_component'] = 'dense'
            dense_count += 1

        # Metric 4: Gap ratio
        if is_high_fg and cell_count <= 2:
            votes['gap_ratio'] = 'dense (continuous coverage)'
            dense_count += 1
        elif gap_ratio > self.GAP_RATIO_BOUNDARY:
            votes['gap_ratio'] = 'sparse'
            sparse_count += 1
        else:
            votes['gap_ratio'] = 'dense'
            dense_count += 1

        # Metric 5: Bimodality
        if bimodality > self.BIMODALITY_BOUNDARY:
            votes['bimodality'] = 'sparse'
            sparse_count += 1
        else:
            votes['bimodality'] = 'dense'
            dense_count += 1

        # Metric 6: Edge density
        if edge_density < self.EDGE_DENSITY_BOUNDARY:
            votes['edge_density'] = 'sparse'
            sparse_count += 1
        else:
            votes['edge_density'] = 'dense'
            dense_count += 1

        # Determine majority
        total_votes = sparse_count + dense_count
        if total_votes == 0:
            return (SampleType.AMBIGUOUS, 0.0,
                    AlgorithmChoice.ALGO_A_SPARSE, votes)

        if sparse_count > dense_count:
            sample_type = SampleType.SPARSE
            confidence = float(sparse_count) / total_votes
            algo = AlgorithmChoice.ALGO_A_SPARSE
        elif dense_count > sparse_count:
            sample_type = SampleType.DENSE
            confidence = float(dense_count) / total_votes
            algo = AlgorithmChoice.ALGO_B_DENSE
        else:
            # Perfect tie
            sample_type = SampleType.AMBIGUOUS
            confidence = 0.5
            algo = AlgorithmChoice.ALGO_A_SPARSE  # default to sparse

        # Downgrade to AMBIGUOUS only if confidence is very low.
        # With 6 binary votes, 4/6 = 0.667 is a clear majority.
        # Use 0.60 as the lower bound for clear classification.
        if confidence < 0.60:
            sample_type = SampleType.AMBIGUOUS

        return sample_type, confidence, algo, votes

    # ==================================================================
    # Algorithm Wrappers
    # ==================================================================

    def _run_algo_a(self, image, min_cell_area_um2, **kwargs):
        """Run AlgoA (CellSegmentationSparse) — seeded hysteresis.

        Parameters
        ----------
        image : numpy.ndarray
            3D array (ny, nx, 4).
        min_cell_area_um2 : float
            Minimum cell area in µm².

        Returns
        -------
        SegmentationResult
        """
        try:
            mask, smoothed, labeled, cell_boxes = \
                self._algo_a.segment_cells_with_instances(
                    image, min_cell_area_um2=min_cell_area_um2)
        except Exception as e:
            logger.warning('AlgoA failed: %s. Returning empty result.', e)
            ny, nx = image.shape[:2]
            return SegmentationResult(
                mask=np.zeros((ny, nx), dtype=bool),
                labeled=np.zeros((ny, nx), dtype=int),
                cell_boxes=[],
                algorithm_used=AlgorithmChoice.ALGO_A_SPARSE,
                quality_score=0.0,
            )

        return SegmentationResult(
            mask=mask,
            labeled=labeled,
            cell_boxes=cell_boxes,
            algorithm_used=AlgorithmChoice.ALGO_A_SPARSE,
            quality_score=0.0,  # scored later in duel
        )

    def _run_algo_b(self, image, min_cell_area_um2, **kwargs):
        """Run AlgoB (CellSegmentationLogic) — gated local adaptive.

        Parameters
        ----------
        image : numpy.ndarray
            3D array (ny, nx, 4).
        min_cell_area_um2 : float
            Minimum cell area in µm².

        Returns
        -------
        SegmentationResult
        """
        try:
            mask, smoothed, labeled, cell_boxes = \
                self._algo_b.segment_cells_with_instances(
                    image, min_cell_area_um2=min_cell_area_um2)
        except Exception as e:
            logger.warning('AlgoB failed: %s. Returning empty result.', e)
            ny, nx = image.shape[:2]
            return SegmentationResult(
                mask=np.zeros((ny, nx), dtype=bool),
                labeled=np.zeros((ny, nx), dtype=int),
                cell_boxes=[],
                algorithm_used=AlgorithmChoice.ALGO_B_DENSE,
                quality_score=0.0,
            )

        return SegmentationResult(
            mask=mask,
            labeled=labeled,
            cell_boxes=cell_boxes,
            algorithm_used=AlgorithmChoice.ALGO_B_DENSE,
            quality_score=0.0,  # scored later in duel
        )

    # ==================================================================
    # Quality Scoring (Algorithm Duel)
    # ==================================================================

    @staticmethod
    def _score_result(mask, labeled, cell_boxes, image):
        """Score a segmentation result on [0, 1].

        Five weighted dimensions:
        1. Coverage sanity (25%): mask covers 5%–65% of image.
        2. FG/BG contrast (30%): high intensity contrast.
        3. Cell count plausibility (20%): 1–30 cells.
        4. Instance regularity (15%): low CV of cell areas.
        5. Boundary cleanness (10%): compact, smooth masks.

        Parameters
        ----------
        mask : numpy.ndarray
            Boolean 2D cell mask.
        labeled : numpy.ndarray
            Integer-labeled instance map.
        cell_boxes : list of dict
            Per-cell bounding box dicts.
        image : numpy.ndarray
            3D array (ny, nx, 4).

        Returns
        -------
        float
            Weighted quality score (0.0–1.0).
        """
        fluor = image[:, :, 3].astype(float)

        # 1. Coverage sanity (penalize < 3% or > 80%)
        coverage = float(np.sum(mask)) / max(mask.size, 1)
        if coverage < 0.03:
            coverage_score = coverage / 0.03  # ramp up from 0
        elif coverage <= 0.55:
            coverage_score = 1.0  # sweet spot for both sparse and dense
        elif coverage <= 0.80:
            coverage_score = 1.0 - (coverage - 0.55) / 0.25  # ramp down
        else:
            coverage_score = 0.0
        coverage_score = max(0.0, min(1.0, coverage_score))

        # 2. FG/BG contrast
        if mask.any() and (~mask).any():
            fg_median = float(np.median(fluor[mask]))
            bg_median = float(np.median(fluor[~mask]))
            if bg_median > 0:
                contrast_ratio = fg_median / bg_median
            else:
                contrast_ratio = fg_median if fg_median > 0 else 0.0
            # Score: ratio of 2.5+ gets full marks
            contrast_score = min(1.0, contrast_ratio / 2.5)
        else:
            contrast_score = 0.0

        # 3. Cell count plausibility
        n_cells = len(cell_boxes)
        if n_cells == 0:
            count_score = 0.0
        elif n_cells <= 3:
            count_score = 0.5 + 0.5 * (n_cells / 3.0)
        elif n_cells <= 65:
            count_score = 1.0
        elif n_cells <= 120:
            count_score = 1.0 - (n_cells - 65) / 55.0
        else:
            count_score = 0.0
        count_score = max(0.0, min(1.0, count_score))

        # 4. Instance regularity (low CV of cell areas)
        if n_cells >= 2:
            areas = []
            for b in cell_boxes:
                area = b.get('area_um2', b.get('area_px', 0))
                if area > 0:
                    areas.append(area)
            if len(areas) >= 2:
                cv = float(np.std(areas)) / max(float(np.mean(areas)), 1e-9)
                regularity_score = max(0.0, 1.0 - cv / 2.0)
            else:
                regularity_score = 0.5
        else:
            regularity_score = 0.5

        # 5. Boundary cleanness (compactness: 4*pi*A / P^2)
        if mask.any():
            total_area = float(np.sum(mask))
            # Estimate perimeter by counting boundary pixels
            padded = np.pad(mask.astype(np.uint8), 1, mode='constant',
                            constant_values=0)
            interior = (
                padded[1:-1, 1:-1]
                & padded[:-2, 1:-1]   # up
                & padded[2:, 1:-1]    # down
                & padded[1:-1, :-2]   # left
                & padded[1:-1, 2:]    # right
            )
            perimeter = float(np.sum(mask) - np.sum(interior))
            if perimeter > 0:
                compactness = (4.0 * np.pi * total_area) / (perimeter ** 2)
                # Cells have compactness 0.1–0.8 typically
                cleanness_score = min(1.0, compactness / 0.5)
            else:
                cleanness_score = 1.0
        else:
            cleanness_score = 0.0

        # Weighted average
        score = (
            0.25 * coverage_score
            + 0.30 * contrast_score
            + 0.20 * count_score
            + 0.15 * regularity_score
            + 0.10 * cleanness_score
        )

        return float(max(0.0, min(1.0, score)))

    # ==================================================================
    # Result Format Adapter
    # ==================================================================

    def _adapt_result_format(self, mask, labeled, cell_boxes, image):
        """Adapt segmentation result to ROI-compatible dict.

        Produces a dict with keys compatible with
        ``ScanRegionQueue.extract_regions_from_segmentation()``:
        'roi_mask', 'diffuse_region_mask', 'raw_bright_spots',
        'component_labels', 'stats'.

        Parameters
        ----------
        mask : numpy.ndarray
            Boolean 2D cell mask.
        labeled : numpy.ndarray
            Integer-labeled instance map.
        cell_boxes : list of dict
            Per-cell bounding box dicts.
        image : numpy.ndarray
            3D array (ny, nx, 4).

        Returns
        -------
        dict
            ROI-compatible result dictionary.
        """
        fluor = image[:, :, 3].astype(float)

        # Build stats list compatible with ROISegmentationLogic output
        stats = []
        for box in cell_boxes:
            lbl = box.get('cell_id', box.get('label', 0))
            area = box.get('area_px', 0)
            mean_int = box.get('mean_intensity', 0.0)

            # Compute centroid
            centroid_px = box.get('centroid_px', None)
            if centroid_px is not None:
                c_row, c_col = centroid_px
            else:
                bbox = box.get('bbox_px', (0, 0, 0, 0))
                c_row = (bbox[0] + bbox[2]) / 2.0
                c_col = (bbox[1] + bbox[3]) / 2.0

            stats.append({
                'label': int(lbl),
                'area': int(area),
                'perimeter': 0,
                'compactness': 0.5,
                'solidity': 1.0,
                'mean_intensity': float(mean_int),
                'centroid_row': float(c_row),
                'centroid_col': float(c_col),
            })

        return {
            'roi_mask': mask,
            'diffuse_region_mask': mask,  # same as roi_mask for compat
            'raw_bright_spots': np.zeros_like(mask),
            'component_labels': labeled,
            'stats': stats,
        }
