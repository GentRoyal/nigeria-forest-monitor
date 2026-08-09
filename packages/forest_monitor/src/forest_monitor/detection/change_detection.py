# ============================================================
# src/detection/change_detection.py
#
# Detects unusual changes in SAR backscatter between the
# baseline mosaic and new monitoring images.
#
# Core method: Log-Ratio
#   change = log(current / baseline) = current_dB - baseline_dB
#
# A strongly positive value = sudden increase in backscatter
#   → new structures, cleared ground, vehicle tracks
# A strongly negative value = sudden decrease in backscatter
#   → new water, heavy vegetation loss
#
# Both directions are suspicious in a forest monitoring context.
# ============================================================

import ee
import json
import numpy as np
from pathlib import Path
from loguru import logger
from datetime import datetime
from src.config import load_config, resolve_path


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# Core: Log-ratio change detection
# ============================================================

def compute_log_ratio(
    baseline: ee.Image,
    current:  ee.Image,
    band:     str = "VV"
) -> ee.Image:
    """
    Compute log-ratio between current and baseline image.

    log_ratio = current_dB - baseline_dB

    Since both images are already in dB scale:
        dB = 10 * log10(linear)
    Subtracting dB images = dividing linear images = log-ratio.

    Positive values → backscatter increased (new structure/clearing)
    Negative values → backscatter decreased (vegetation loss/water)
    """
    ratio = current.select(band).subtract(baseline.select(band))
    return ratio.rename("log_ratio")


# ============================================================
# Threshold: flag significant change
# ============================================================

def threshold_change(
    log_ratio:     ee.Image,
    threshold_sigma: float = None,
    config:        dict = None,
    aoi:           ee.Geometry = None,
    scale:         int = 100
) -> ee.Image:
    """
    Flag pixels where |log_ratio| exceeds threshold_sigma * std_dev.

    Returns a binary image:
        1 = significant change detected
        0 = no significant change

    If threshold_sigma is None, reads from config.
    """
    if threshold_sigma is None and config is not None:
        threshold_sigma = config["change_detection"]["threshold_sigma"]
    elif threshold_sigma is None:
        threshold_sigma = 2.0

    if aoi is not None:
        # Compute std dev from the ratio image itself
        stats = log_ratio.reduceRegion(
            reducer  = ee.Reducer.stdDev(),
            geometry = aoi,
            scale    = scale,
            maxPixels= 1e9
        ).getInfo()
        std_val = stats.get("log_ratio", 2.0)
        if std_val is None:
            std_val = 2.0
        threshold = float(std_val) * threshold_sigma
        logger.info(
            f"Adaptive threshold: {threshold_sigma}σ × {std_val:.4f} "
            f"= ±{threshold:.4f} dB"
        )
    else:
        # Fixed fallback threshold (dB)
        threshold = threshold_sigma * 2.0
        logger.info(f"Fixed threshold: ±{threshold:.4f} dB")

    # Flag pixels exceeding threshold in either direction
    changed = log_ratio.abs().gt(threshold).rename("change_mask")
    return changed


# ============================================================
# Directional change: increase vs decrease
# ============================================================

def directional_change(
    log_ratio: ee.Image,
    threshold: float = 3.0
) -> ee.Image:
    """
    Split change into two directional layers:
        increase: backscatter went UP   → structure, clearing, vehicles
        decrease: backscatter went DOWN → vegetation loss, water expansion

    Both are suspicious. Increase is higher priority for this project.

    Returns a multi-band image with 'increase' and 'decrease' bands.
    """
    increase = log_ratio.gt(threshold).rename("increase")
    decrease = log_ratio.lt(-threshold).rename("decrease")
    return ee.Image.cat([increase, decrease])


# ============================================================
# Morphological cleaning: remove tiny noise patches
# ============================================================

def clean_change_mask(
    change_mask: ee.Image,
    min_pixels:  int = None,
    config:      dict = None
) -> ee.Image:
    """
    Remove isolated changed pixels smaller than min_pixels.
    Real forest clearings or structures span multiple pixels.
    Single-pixel changes are almost always noise.

    Uses connected component labelling to filter by patch size.
    """
    if min_pixels is None and config is not None:
        min_pixels = config["change_detection"]["min_patch_size_px"]
    elif min_pixels is None:
        min_pixels = 10

    # Count connected changed pixels directly. Using min_pixels as maxSize is
    # sufficient because only the >= min_pixels decision is required and it
    # avoids materialising a full component-label raster.
    binary = change_mask.gt(0).selfMask()
    area = binary.connectedPixelCount(
        maxSize=max(2, min_pixels), eightConnected=True
    )
    cleaned = binary.updateMask(area.gte(min_pixels)).unmask(0).rename("change_mask")

    logger.info(f"Change mask cleaned (min patch: {min_pixels} pixels)")
    return cleaned


# ============================================================
# Run full change detection for one monitoring image
# ============================================================

def detect_changes(
    baseline:    ee.Image,
    current:     ee.Image,
    aoi:         ee.Geometry,
    config:      dict,
    band:        str = "VV",
    clean:       bool = True
) -> dict:
    """
    Full change detection pipeline for a single monitoring image.

    Returns a dict with:
        log_ratio     : raw log-ratio image
        change_mask   : binary change map (cleaned)
        directional   : increase / decrease bands
        stats         : area and intensity statistics
    """
    logger.info(f"Running change detection (band: {band})...")

    # 1. Log-ratio
    log_ratio = compute_log_ratio(baseline, current, band)

    # 2. Threshold → binary mask
    change_mask = threshold_change(log_ratio, config=config, aoi=aoi)

    # 3. Clean small patches
    if clean:
        change_mask = clean_change_mask(change_mask, config=config)

    # 4. Directional split
    threshold  = config["change_detection"]["threshold_sigma"] * 2.0
    directional = directional_change(log_ratio, threshold=threshold)

    # 5. Compute change statistics
    stats = compute_change_stats(change_mask, log_ratio, aoi)

    logger.success(
        f"Change detection complete | "
        f"changed area: {stats.get('changed_pct', 'N/A'):.2f}% of AOI"
    )

    return {
        "log_ratio":   log_ratio,
        "change_mask": change_mask,
        "directional": directional,
        "stats":       stats
    }


# ============================================================
# Run change detection across a full monitoring collection
# ============================================================

def detect_changes_collection(
    baseline:   ee.Image,
    collection: ee.ImageCollection,
    aoi:        ee.Geometry,
    config:     dict,
    band:       str = "VV"
) -> ee.ImageCollection:
    """
    Apply change detection to every image in a collection.
    Returns a collection of log-ratio images (one per date).
    Efficient — runs entirely server-side on GEE.
    """
    def detect_single(image):
        ratio = compute_log_ratio(baseline, image, band)
        return ratio.copyProperties(image, ["system:time_start"])

    logger.info("Running change detection across monitoring collection...")
    ratios = collection.map(detect_single)
    logger.success("Collection change detection complete")
    return ratios


# ============================================================
# Statistics: how much changed, how intensely
# ============================================================

def compute_change_stats(
    change_mask: ee.Image,
    log_ratio:   ee.Image,
    aoi:         ee.Geometry,
    scale:       int = 100
) -> dict:
    """
    Compute summary statistics for a change detection result.

    Returns:
        changed_px      : number of changed pixels
        changed_pct     : % of AOI that changed
        mean_ratio      : mean log-ratio in changed areas
        max_ratio       : max log-ratio (most intense change)
    """
    # Mask log_ratio to changed pixels only
    ratio_in_changes = log_ratio.updateMask(change_mask)

    stats = ratio_in_changes.reduceRegion(
        reducer  = ee.Reducer.mean()
                     .combine(ee.Reducer.max(),   sharedInputs=True)
                     .combine(ee.Reducer.count(), sharedInputs=True),
        geometry = aoi,
        scale    = scale,
        maxPixels= 1e9
    ).getInfo()

    # Total pixels in AOI
    total_stats = change_mask.reduceRegion(
        reducer  = ee.Reducer.sum().combine(ee.Reducer.count(), sharedInputs=True),
        geometry = aoi,
        scale    = scale,
        maxPixels= 1e9
    ).getInfo()

    changed_px  = total_stats.get("change_mask_sum",   0) or 0
    total_px    = total_stats.get("change_mask_count", 1) or 1
    changed_pct = (changed_px / total_px) * 100

    result = {
        "changed_px":  int(changed_px),
        "total_px":    int(total_px),
        "changed_pct": round(changed_pct, 4),
        "mean_ratio":  stats.get("log_ratio_mean"),
        "max_ratio":   stats.get("log_ratio_max"),
    }
    return result


# ============================================================
# Grid-level change scores
# ============================================================

def score_grid_cells(
    change_mask: ee.Image,
    log_ratio: ee.Image,
    grid: "gpd.GeoDataFrame",
    config: dict,
    scale: int = 100,
) -> "gpd.GeoDataFrame":
    """Compute cell scores with one server-side reduceRegions request."""
    import pandas as pd
    from src.ingestion.grid import grid_to_ee_feature_collection

    if grid.empty:
        result = grid.copy()
        for column in ("changed_frac", "mean_intensity", "change_score"):
            result[column] = 0.0
        return result

    logger.info(f"Scoring {len(grid)} grid cells for change...")
    metrics = ee.Image.cat([
        change_mask.gt(0).rename("changed_frac"),
        log_ratio.abs().rename("mean_intensity"),
    ])
    reduced = metrics.reduceRegions(
        collection=grid_to_ee_feature_collection(grid),
        reducer=ee.Reducer.mean(),
        scale=max(scale, int(config["sentinel1"]["resolution_m"])),
        tileScale=8,
    ).getInfo()

    records = []
    for feature in reduced.get("features", []):
        props = feature.get("properties", {})
        changed = float(props.get("changed_frac") or 0.0)
        intensity = float(props.get("mean_intensity") or 0.0)
        records.append({
            "cell_id": int(props["cell_id"]),
            "changed_frac": changed,
            "mean_intensity": intensity,
            "change_score": changed * min(intensity / 10.0, 1.0),
        })

    scores = pd.DataFrame.from_records(records)
    if scores.empty:
        scores = pd.DataFrame({"cell_id": grid["cell_id"], "changed_frac": 0.0, "mean_intensity": 0.0, "change_score": 0.0})
    maximum = float(scores["change_score"].max())
    if maximum > 0:
        scores["change_score"] = (scores["change_score"] / maximum).clip(0, 1)

    result = grid.merge(scores, on="cell_id", how="left")
    for column in ("changed_frac", "mean_intensity", "change_score"):
        result[column] = result[column].fillna(0.0)
    logger.success(f"Grid scoring complete | {int((result['change_score'] > 0.3).sum())} high-change cells")
    return result

# ============================================================
# Preview in notebook
# ============================================================

def preview_change(
    baseline:    ee.Image,
    current:     ee.Image,
    change_mask: ee.Image,
    log_ratio:   ee.Image,
    aoi:         ee.Geometry
) -> "geemap.Map":
    """
    Interactive map showing baseline, current, log-ratio, and change mask.
    Four toggleable layers — tells the full story in one view.
    """
    import geemap

    vis_sar    = {"bands": ["VV"], "min": -20, "max": -5,  "palette": ["black", "white"]}
    vis_ratio  = {"min": -5, "max": 5, "palette": ["blue", "white", "red"]}
    vis_change = {"min": 0,  "max": 1, "palette": ["000000", "FF0000"]}

    m = geemap.Map()
    m.centerObject(aoi, zoom=10)
    m.addLayer(baseline,    vis_sar,    "Baseline SAR (2020-2022)")
    m.addLayer(current,     vis_sar,    "Current SAR")
    m.addLayer(log_ratio,   vis_ratio,  "Log-Ratio (blue=decrease, red=increase)")
    m.addLayer(change_mask, vis_change, "Change Mask (red=changed)")
    m.addLayer(aoi, {}, "AOI")

    logger.info(
        "Change map ready:\n"
        "  Red pixels  = significant increase (structures/clearings)\n"
        "  Blue pixels = significant decrease (vegetation loss)\n"
        "  Toggle layers to compare baseline vs current"
    )
    return m


# ============================================================
# Save change detection results
# ============================================================

def save_change_stats(
    stats:  dict,
    label:  str,
    config: dict
) -> Path:
    """Save change detection stats to disk for tracking over time."""
    out_dir  = resolve_path(config, "processed_sar", create=True) / "change_stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"change_{label}.json"

    stats["label"]      = label
    stats["computed_at"] = datetime.utcnow().isoformat()

    with open(out_path, "w") as f:
        json.dump(stats, f, indent=2)

    logger.success(f"Change stats saved → {out_path}")
    return out_path


# ============================================================
# CLI entry point
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[2]))

    import ee
    from src.ingestion.gee_download import (
        init_gee, build_aoi, get_s1_collection
    )
    from src.preprocessing.baseline  import build_baseline
    from src.preprocessing.speckle_filter import filter_composite

    init_gee()
    config = load_config()
    aoi    = build_aoi(config["aoi"]["zones"]["old_oyo_core"]["bbox"])

    # Build baseline
    baseline = build_baseline(aoi, config, filter_method="lee")

    # Get a recent monitoring image (last 30 days)
    monitor = get_s1_collection(aoi, "2025-01-01", "2025-02-01", config)
    current = filter_composite(monitor.median().clip(aoi), method="lee")

    # Run change detection
    result = detect_changes(baseline, current, aoi, config)

    print("\nChange Detection Results:")
    print(f"  Changed area : {result['stats']['changed_pct']:.3f}% of AOI")
    print(f"  Mean ratio   : {result['stats']['mean_ratio']} dB")
    print(f"  Max ratio    : {result['stats']['max_ratio']} dB")

    save_change_stats(result["stats"], "2025-01", config)