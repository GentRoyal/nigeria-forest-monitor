"""Deterministic normalisation helpers for SAR images and feature tables."""

from __future__ import annotations

import ee
import numpy as np


def normalise_sar_db(image: ee.Image, min_db: float = -30.0, max_db: float = 0.0) -> ee.Image:
    """Map dB backscatter to [0, 1] while preserving band names."""
    if min_db >= max_db:
        raise ValueError("min_db must be smaller than max_db")
    return image.clamp(min_db, max_db).unitScale(min_db, max_db).rename(image.bandNames())


def fit_standardiser(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return column means and safe standard deviations."""
    values = np.asarray(features, dtype=np.float32)
    if values.ndim != 2 or len(values) == 0 or not np.isfinite(values).all():
        raise ValueError("features must be a finite, non-empty 2D array")
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale[scale < 1e-6] = 1.0
    return mean, scale


def apply_standardiser(features: np.ndarray, mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
    values = np.asarray(features, dtype=np.float32)
    mean = np.asarray(mean, dtype=np.float32)
    scale = np.asarray(scale, dtype=np.float32)
    if values.ndim != 2 or mean.shape != (values.shape[1],) or scale.shape != mean.shape:
        raise ValueError("standardiser dimensions do not match features")
    if (scale <= 0).any():
        raise ValueError("standard deviations must be positive")
    return (values - mean) / scale


__all__ = ["normalise_sar_db", "fit_standardiser", "apply_standardiser"]