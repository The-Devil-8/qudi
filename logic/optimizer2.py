# -*- coding: utf-8 -*-
"""Bounded, offline 2-D Gaussian localization for optimizer development.

This module deliberately does not control scanner hardware and does not modify
``OptimizerLogic``.  It provides a testable analysis step for recorded
confocal images and for a future hardware adapter.  In particular, fitted
centres are bounded to the coordinates actually sampled in the supplied image
window, so an extrapolated Gaussian centre cannot be reported as a local
maximum.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy.optimize import curve_fit


@dataclass(frozen=True)
class Optimizer2DResult:
    """Result of one bounded 2-D Gaussian localization attempt.

    All coordinates and widths are in metres.  A failed fit has
    ``success=False`` and ``position_m=None``; callers must not interpret the
    seed as a fallback optimized position.
    """

    success: bool
    position_m: Optional[Tuple[float, float]]
    sigma_m: Optional[Tuple[float, float]]
    amplitude: Optional[float]
    offset: Optional[float]
    r_squared: Optional[float]
    sampled_bounds_m: Tuple[float, float, float, float]
    pitch_m: Tuple[float, float]
    sample_shape: Tuple[int, int]
    is_edge_fit: bool
    error: Optional[str] = None


class Optimizer2D:
    """Fit a local, axis-aligned Gaussian without moving any hardware.

    ``fit_local`` accepts a confocal count image in NumPy ``(row, column)``
    order, plus its one-dimensional physical X and Y coordinate arrays.  It is
    intentionally independent of Qt and Qudi connectors so it can be replayed
    against archived data before a future live optimizer adapter is trusted.
    """

    @staticmethod
    def _gaussian_model(coordinates, amplitude, center_x, center_y,
                        sigma_x, sigma_y, offset):
        x_values, y_values = coordinates
        exponent = (((x_values - center_x) / sigma_x) ** 2 +
                    ((y_values - center_y) / sigma_y) ** 2)
        return offset + amplitude * np.exp(-0.5 * exponent)

    @staticmethod
    def _result_failure(bounds, pitch, shape, error):
        return Optimizer2DResult(
            success=False,
            position_m=None,
            sigma_m=None,
            amplitude=None,
            offset=None,
            r_squared=None,
            sampled_bounds_m=bounds,
            pitch_m=pitch,
            sample_shape=shape,
            is_edge_fit=False,
            error=error,
        )

    @staticmethod
    def _validate_coordinates(image, x_coordinates, y_coordinates):
        image = np.asarray(image, dtype=float)
        x_coordinates = np.asarray(x_coordinates, dtype=float)
        y_coordinates = np.asarray(y_coordinates, dtype=float)

        if image.ndim != 2:
            raise ValueError('image must be a 2-D array in (row, column) order')
        if x_coordinates.ndim != 1 or y_coordinates.ndim != 1:
            raise ValueError('x_coordinates and y_coordinates must be 1-D arrays')
        if image.shape != (len(y_coordinates), len(x_coordinates)):
            raise ValueError('image shape must match y_coordinates, x_coordinates')
        if len(x_coordinates) < 2 or len(y_coordinates) < 2:
            raise ValueError('at least two coordinates are required per axis')
        if np.any(np.diff(x_coordinates) <= 0) or np.any(np.diff(y_coordinates) <= 0):
            raise ValueError('coordinate arrays must be strictly increasing')
        return image, x_coordinates, y_coordinates

    def fit_local(self, image, x_coordinates, y_coordinates, seed_position_m,
                  window_size_m=None, edge_margin_m=None):
        """Fit a bounded local Gaussian around a physical seed position.

        Parameters
        ----------
        image : numpy.ndarray
            Count-rate image with shape ``(len(y_coordinates), len(x_coordinates))``.
        x_coordinates, y_coordinates : numpy.ndarray
            Strictly increasing physical coordinates in metres.
        seed_position_m : tuple(float, float)
            Requested local-search centre in metres.
        window_size_m : float or tuple(float, float), optional
            Requested X/Y window size.  The selected window is clipped to data
            support; the returned bounds always describe that actual support.
        edge_margin_m : float, optional
            Distance from a sampled edge that flags ``is_edge_fit``.  Defaults
            to one sample pitch on the tighter axis.
        """
        image, x_coordinates, y_coordinates = self._validate_coordinates(
            image, x_coordinates, y_coordinates)

        if len(seed_position_m) != 2:
            raise ValueError('seed_position_m must contain x and y coordinates')
        seed_x, seed_y = (float(seed_position_m[0]), float(seed_position_m[1]))

        if window_size_m is None:
            window_x = x_coordinates[-1] - x_coordinates[0]
            window_y = y_coordinates[-1] - y_coordinates[0]
        elif np.isscalar(window_size_m):
            window_x = window_y = float(window_size_m)
        else:
            if len(window_size_m) != 2:
                raise ValueError('window_size_m must be scalar or a two-element iterable')
            window_x, window_y = (float(window_size_m[0]), float(window_size_m[1]))

        if window_x <= 0 or window_y <= 0:
            raise ValueError('window_size_m values must be positive')

        x_mask = np.abs(x_coordinates - seed_x) <= window_x / 2.0
        y_mask = np.abs(y_coordinates - seed_y) <= window_y / 2.0
        if not np.any(x_mask) or not np.any(y_mask):
            raise ValueError('seed window does not overlap image support')

        local_x = x_coordinates[x_mask]
        local_y = y_coordinates[y_mask]
        local_image = image[np.ix_(y_mask, x_mask)]
        bounds = (float(local_x[0]), float(local_x[-1]),
                  float(local_y[0]), float(local_y[-1]))
        pitch = (float(np.median(np.diff(local_x))),
                 float(np.median(np.diff(local_y))))
        shape = tuple(int(value) for value in local_image.shape)

        if min(shape) < 5:
            return self._result_failure(bounds, pitch, shape,
                                        'fewer than five samples in one fit axis')
        if not np.all(np.isfinite(local_image)):
            return self._result_failure(bounds, pitch, shape,
                                        'non-finite count data in fit window')

        x_grid, y_grid = np.meshgrid(local_x, local_y)
        x_data = x_grid.ravel()
        y_data = y_grid.ravel()
        count_data = local_image.ravel()

        background = float(np.percentile(count_data, 20.0))
        signal = np.clip(count_data - background, 0.0, None)
        signal_sum = float(np.sum(signal))
        amplitude_guess = float(np.max(count_data) - background)
        if signal_sum <= 0 or amplitude_guess <= 0:
            return self._result_failure(bounds, pitch, shape,
                                        'no positive signal above robust background')

        center_x_guess = float(np.sum(x_data * signal) / signal_sum)
        center_y_guess = float(np.sum(y_data * signal) / signal_sum)
        sigma_x_guess = max(pitch[0], (local_x[-1] - local_x[0]) / 4.0)
        sigma_y_guess = max(pitch[1], (local_y[-1] - local_y[0]) / 4.0)

        lower_bounds = (0.0, local_x[0], local_y[0],
                        pitch[0] / 2.0, pitch[1] / 2.0, 0.0)
        upper_bounds = (np.inf, local_x[-1], local_y[-1],
                        local_x[-1] - local_x[0],
                        local_y[-1] - local_y[0], np.inf)
        initial_values = (amplitude_guess, center_x_guess, center_y_guess,
                          sigma_x_guess, sigma_y_guess, max(background, 0.0))

        try:
            parameters, _ = curve_fit(
                self._gaussian_model,
                (x_data, y_data),
                count_data,
                p0=initial_values,
                bounds=(lower_bounds, upper_bounds),
                maxfev=20000,
            )
        except (RuntimeError, ValueError, FloatingPointError) as error:
            return self._result_failure(bounds, pitch, shape, str(error))

        fitted = self._gaussian_model((x_data, y_data), *parameters)
        residual_sum = float(np.sum((count_data - fitted) ** 2))
        total_sum = float(np.sum((count_data - np.mean(count_data)) ** 2))
        r_squared = None if total_sum <= 0 else 1.0 - residual_sum / total_sum

        _, center_x, center_y, sigma_x, sigma_y, _ = parameters
        if edge_margin_m is None:
            edge_margin_m = max(pitch)
        edge_margin_m = float(edge_margin_m)
        is_edge_fit = (
            center_x - local_x[0] <= edge_margin_m or
            local_x[-1] - center_x <= edge_margin_m or
            center_y - local_y[0] <= edge_margin_m or
            local_y[-1] - center_y <= edge_margin_m
        )

        return Optimizer2DResult(
            success=True,
            position_m=(float(center_x), float(center_y)),
            sigma_m=(float(sigma_x), float(sigma_y)),
            amplitude=float(parameters[0]),
            offset=float(parameters[5]),
            r_squared=r_squared,
            sampled_bounds_m=bounds,
            pitch_m=pitch,
            sample_shape=shape,
            is_edge_fit=is_edge_fit,
        )
