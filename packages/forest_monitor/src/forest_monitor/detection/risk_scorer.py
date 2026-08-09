"""Fuse change, classifier, and incident signals into cell-level risk."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from loguru import logger

from src.config import load_config, resolve_path

SIGNAL_COLUMNS = {
    "change_detection": "change_score",
    "classifier": "classifier_score",
    "acled_proximity": "acled_score",
}


def _normalise_signal(series: pd.Series, name: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if ((values < 0) | (values > 1)).any():
        logger.warning(f"{name} contained values outside [0, 1]; clipping")
    return values.clip(0.0, 1.0)


def score_risk(grid: gpd.GeoDataFrame, config: dict) -> gpd.GeoDataFrame:
    """Return a copy of ``grid`` with risk_score, risk_level, and alert."""
    if "cell_id" not in grid:
        raise ValueError("grid must contain cell_id")
    result = grid.copy()
    weights = config["risk"]["weights"]

    for weight_name, column in SIGNAL_COLUMNS.items():
        if column not in result:
            logger.warning(f"Missing {column}; using zero")
            result[column] = 0.0
        result[column] = _normalise_signal(result[column], column)

    result["risk_score"] = sum(
        float(weights[weight_name]) * result[column]
        for weight_name, column in SIGNAL_COLUMNS.items()
    ).clip(0.0, 1.0)

    alert_threshold = float(config["risk"]["alert_threshold"])
    high_threshold = float(config["risk"]["high_risk_threshold"])
    if not 0 <= alert_threshold <= high_threshold <= 1:
        raise ValueError("risk thresholds must satisfy 0 <= alert <= high <= 1")

    result["risk_level"] = pd.cut(
        result["risk_score"],
        bins=[-np.inf, alert_threshold * 0.5, alert_threshold, high_threshold, np.inf],
        labels=["low", "moderate", "high", "critical"],
        right=False,
    ).astype("string")
    result["alert"] = result["risk_score"] >= alert_threshold
    result["risk_rank"] = result["risk_score"].rank(method="dense", ascending=False).astype(int)
    logger.success(f"Risk scoring complete: {int(result['alert'].sum())} alert cells")
    return result


def classifier_scores_by_cell(
    cell_ids: np.ndarray,
    probabilities: np.ndarray,
) -> pd.DataFrame:
    """Aggregate suspicious-class probabilities to one score per cell."""
    ids = np.asarray(cell_ids)
    probs = np.asarray(probabilities, dtype=float)
    if probs.ndim != 2 or probs.shape[1] != 4 or len(ids) != len(probs):
        raise ValueError("probabilities must have shape (N, 4) and match cell_ids")
    suspicious_score = probs[:, 1:].max(axis=1)
    frame = pd.DataFrame({"cell_id": ids, "classifier_score": suspicious_score})
    return frame.groupby("cell_id", as_index=False)["classifier_score"].max()


def top_alerts(scored_grid: gpd.GeoDataFrame, limit: int = 20) -> gpd.GeoDataFrame:
    if "risk_score" not in scored_grid:
        raise ValueError("Run score_risk before requesting alerts")
    return scored_grid.sort_values(["risk_score", "cell_id"], ascending=[False, True]).head(limit).copy()


def save_risk_scores(scored_grid: gpd.GeoDataFrame, config: dict, name: str = "risk_scores") -> Path:
    output_dir = resolve_path(config, "processed_sar", create=True).parent
    output = output_dir / f"{name}.gpkg"
    scored_grid.to_file(output, driver="GPKG")
    logger.success(f"Risk scores saved -> {output}")
    return output


__all__ = ["score_risk", "classifier_scores_by_cell", "top_alerts", "save_risk_scores", "load_config"]