# -*- coding: utf-8 -*-
"""
Z-Surface Finder Module (Stub).

Purpose
-------
Find the sample surface line (Z_SL) from a Z-scan intensity profile in diamond/NV confocal
experiments and compute target acquisition focal depth.

Algorithm Outline (Planned for Next Iteration)
----------------------------------------------
1. Acquire a full or partial Z-scan along the optical depth axis across the sample thickness.
2. Extract the 1D fluorescence intensity profile I(z) vs Z position.
3. Identify the bright layer peak corresponding to the top ~2% intensity threshold – the 'cream'
   layer at the diamond-sample surface boundary (using the 'cake -> cream' analogy, where the
   surface fluorescent layer sits atop the substrate cake).
4. Extract and refine the exact Z position of this surface line (Z_SL) based on selection criteria
   to be defined using experimental calibration data.
5. Automated Z-scanning at the start of each cell ROI scan queue item will also be integrated
   in the next step.

Target Depth Calculation
------------------------
Target focal position Z is computed as an offset relative to the surface line:
    Z_target = Z_SL - z_depth_from_surface

Note
----
This module is a stub documenting the interface and algorithm design for the next iteration.
The current active automation pipeline uses existing Z position settings.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np


@dataclass
class SurfaceFinderResult:
    """Dataclass holding the results of a Z-surface finding operation.

    Attributes
    ----------
    z_surface_m : float
        Extracted sample surface Z position in meters (Z_SL).
    confidence : float
        Confidence score of the detected surface position (0.0 to 1.0).
    bright_layer_intensity : float
        Peak fluorescence intensity of the detected surface bright layer ('cream' layer).
    profile_peak_index : int
        Index in the 1D Z-profile corresponding to the detected surface peak.
    method : str
        Algorithm or method identifier used for surface detection (e.g. 'bright_layer_top2pct').
    """
    z_surface_m: float = 0.0
    confidence: float = 0.0
    bright_layer_intensity: float = 0.0
    profile_peak_index: int = -1
    method: str = "bright_layer_top2pct"


class ZSurfaceFinder:
    """Class for Z-scan surface finding and depth targeting.

    This class provides the interface for finding the sample surface line (Z_SL)
    from a 1D Z-scan intensity profile and computing target focal depths relative to it.

    Note
    ----
    This is a plain Python class (not inheriting from GenericLogic).
    The algorithm is documented for the next iteration step; functional methods raise
    NotImplementedError in this stub module while current pipelines rely on existing Z positions.
    """

    def __init__(self) -> None:
        """Initialize the ZSurfaceFinder instance."""
        pass

    def is_implemented(self) -> bool:
        """Check if the surface-finding algorithm is implemented.

        Returns
        -------
        bool
            False for this stub implementation.
        """
        return False

    def find_surface(
        self,
        z_profile: np.ndarray,
        z_values: np.ndarray,
        z_scan_range_m: Optional[Tuple[float, float]] = None,
    ) -> SurfaceFinderResult:
        """Find the sample surface line position (Z_SL) from a 1D Z-scan intensity profile.

        Planned Algorithm Outline
        --------------------------
        1. Full/partial Z-scan acquisition over the sample surface region.
        2. Extract 1D intensity profile I(z) vs z coordinates.
        3. Peak detection using the 'cake -> cream' analogy: find the bright layer peak
           in the top ~2% intensity threshold range of the profile.
        4. Apply selection criteria (to be defined with experimental calibration data)
           to determine the exact surface line position Z_SL.
        5. Return a SurfaceFinderResult with Z_SL, confidence, and peak details.

        Note
        ----
        Automated Z-scanning at the start of each cell ROI will be automated as the next step.

        Parameters
        ----------
        z_profile : np.ndarray
            1D array of fluorescence intensity values I(z) along the Z axis.
        z_values : np.ndarray
            1D array of corresponding Z position coordinates in meters.
        z_scan_range_m : tuple of float, optional
            Tuple of (z_min, z_max) specifying the scan bounds in meters.

        Returns
        -------
        SurfaceFinderResult
            Result dataclass containing surface Z position and detection metadata.

        Raises
        ------
        NotImplementedError
            Always raised in this stub module.
        """
        raise NotImplementedError(
            "Z-scan surface finding algorithm (bright layer top ~2% method) is planned "
            "for the next iteration and is currently not implemented. The pipeline currently "
            "uses existing Z position settings."
        )

    def compute_target_depth(self, z_surface: float, z_depth_from_surface_m: float) -> float:
        """Compute target focal Z position relative to the detected sample surface.

        Target Z position formula:
            Z = Z_SL - z_depth_from_surface_m

        Parameters
        ----------
        z_surface : float
            Detected surface line Z position (Z_SL) in meters.
        z_depth_from_surface_m : float
            Desired depth below the surface in meters (positive offset into sample).

        Returns
        -------
        float
            Target Z position in meters (z_surface - z_depth_from_surface_m).
        """
        return z_surface - z_depth_from_surface_m
