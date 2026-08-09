"""End-to-end monitoring orchestration used by notebooks and the CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from src.config import load_config, resolve_path
from src.dashboard.alert_report import generate_alert_report
from src.dashboard.map_builder import build_risk_map, save_risk_map
from src.detection.change_detection import detect_changes, score_grid_cells
from src.detection.classifier import classify_grid_cells, load_model
from src.detection.risk_scorer import save_risk_scores, score_risk
from src.ingestion.acled_fetch import compute_proximity_scores, filter_to_aoi, load_acled, tag_incidents_to_grid
from src.ingestion.gee_download import build_aoi, get_s1_collection, init_gee, median_composite
from src.ingestion.grid import create_grid, tag_zones
from src.preprocessing.baseline import build_baseline
from src.preprocessing.speckle_filter import filter_composite


def run_monitoring(
    *,
    start_date: str,
    end_date: str,
    zone: str = "old_oyo_core",
    config_path: str | Path | None = None,
    filter_method: str = "lee",
) -> dict:
    """Run detection, grid scoring, risk fusion, map, and PDF generation."""
    config = load_config(config_path)
    init_gee()
    if zone == "full":
        bbox = config["aoi"]["bbox"]
    else:
        try:
            bbox = config["aoi"]["zones"][zone]["bbox"]
        except KeyError as exc:
            raise ValueError(f"Unknown zone: {zone}") from exc
    aoi = build_aoi(bbox)

    baseline = build_baseline(aoi, config, filter_method=filter_method)
    monitor = get_s1_collection(aoi, start_date, end_date, config)
    current = filter_composite(median_composite(monitor, aoi), method=filter_method)
    detection = detect_changes(baseline, current, aoi, config)

    grid = tag_zones(create_grid(config, zone=zone), config)
    grid = score_grid_cells(detection["change_mask"], detection["log_ratio"], grid, config)

    model_path = resolve_path(config, "models") / "sar_classifier_v1.pt"
    if model_path.exists():
        try:
            classifier_scores = classify_grid_cells(load_model(model_path), current, grid, config)
            grid = grid.merge(classifier_scores, on="cell_id", how="left")
        except Exception as error:
            logger.warning(f"Classifier model could not be used ({error}); classifier score set to zero")
            grid["classifier_score"] = 0.0
    else:
        logger.warning(f"No classifier model at {model_path}; classifier score set to zero")
        grid["classifier_score"] = 0.0
    grid["classifier_score"] = grid["classifier_score"].fillna(0.0)

    incidents = None
    acled_path = resolve_path(config, "acled") / "acled_nigeria.parquet"
    if acled_path.exists():
        incidents = filter_to_aoi(load_acled(config), config, zone=zone)
        if not incidents.empty:
            grid = tag_incidents_to_grid(incidents, grid, config)
            grid = compute_proximity_scores(grid, incidents, config)
        else:
            grid["acled_score"] = 0.0
    else:
        logger.warning(f"No cached ACLED data at {acled_path}; ACLED score set to zero")
        grid["acled_score"] = 0.0

    scored = score_risk(grid, config)
    scores_path = save_risk_scores(scored, config)
    map_path = save_risk_map(build_risk_map(scored, config, incidents), config)
    report_path = generate_alert_report(scored, config)
    return {
        "config": config,
        "aoi": aoi,
        "baseline": baseline,
        "current": current,
        "detection": detection,
        "incidents": incidents,
        "grid": scored,
        "scores_path": scores_path,
        "map_path": map_path,
        "report_path": report_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Nigeria Forest Monitor")
    parser.add_argument("--start", required=True, help="Monitoring start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Monitoring end date (YYYY-MM-DD)")
    parser.add_argument("--zone", default="old_oyo_core")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    result = run_monitoring(start_date=args.start, end_date=args.end, zone=args.zone, config_path=args.config)
    print(f"Risk map: {result['map_path']}")
    print(f"Alert report: {result['report_path']}")


if __name__ == "__main__":
    main()