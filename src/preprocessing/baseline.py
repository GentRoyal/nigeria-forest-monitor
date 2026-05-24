# ============================================================
# src/preprocessing/baseline.py
#
# Builds the "normal" reference image for the corridor.
# Change detection compares every new image against this.
#
# A good baseline = median of many cloud-free images over
# a stable period (2020-2022) AFTER speckle filtering.
# ============================================================

import ee
import yaml
import json
from pathlib import Path
from loguru import logger
from datetime import datetime


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# Build baseline mosaic
# ============================================================

def build_baseline(
    aoi:    ee.Geometry,
    config: dict,
    filter_method: str = "lee"
) -> ee.Image:
    """
    Build a filtered baseline mosaic over the full baseline period.

    Steps:
      1. Pull all Sentinel-1 images in the baseline window
      2. Apply speckle filter to each image
      3. Take the median across all images → stable reference
      4. Clip to AOI

    The median is more robust than the mean — it ignores
    temporary anomalies (floods, agriculture cycles, fires)
    that happened during the baseline period.
    """
    from src.ingestion.gee_download import get_s1_collection
    from src.preprocessing.speckle_filter import apply_filter_to_collection

    s1_cfg = config["sentinel1"]

    logger.info(
        f"Building baseline: "
        f"{s1_cfg['baseline_start']} → {s1_cfg['baseline_end']}"
    )

    # 1. Fetch raw collection
    collection = get_s1_collection(
        aoi,
        s1_cfg["baseline_start"],
        s1_cfg["baseline_end"],
        config
    )

    count = collection.size().getInfo()
    if count == 0:
        raise ValueError(
            "No Sentinel-1 images found for the baseline period. "
            "Check your AOI and config dates."
        )
    logger.info(f"Baseline collection: {count} images")

    # 2. Apply speckle filter to every image
    filtered = apply_filter_to_collection(collection, method=filter_method)

    # 3. Median composite → robust against outliers
    baseline = filtered.median().clip(aoi)

    logger.success(
        f"Baseline mosaic built from {count} images | "
        f"filter: {filter_method}"
    )
    return baseline


# ============================================================
# Build per-month baselines (seasonal correction)
# ============================================================

def build_monthly_baselines(
    aoi:    ee.Geometry,
    config: dict,
    filter_method: str = "lee"
) -> dict:
    """
    Build one baseline image per calendar month (Jan–Dec).
    Captures seasonal vegetation patterns so the change detector
    doesn't flag dry-season vs wet-season differences as anomalies.

    Returns: dict of {month_int: ee.Image}
    e.g. {1: ee.Image, 2: ee.Image, ..., 12: ee.Image}
    """
    from src.ingestion.gee_download import get_s1_collection
    from src.preprocessing.speckle_filter import apply_filter_to_collection

    s1_cfg = config["sentinel1"]

    logger.info("Building monthly seasonal baselines (Jan–Dec)...")

    collection = get_s1_collection(
        aoi,
        s1_cfg["baseline_start"],
        s1_cfg["baseline_end"],
        config
    )
    filtered = apply_filter_to_collection(collection, method=filter_method)

    monthly_baselines = {}

    for month in range(1, 13):
        monthly = filtered.filter(ee.Filter.calendarRange(month, month, "month"))
        count   = monthly.size().getInfo()

        if count == 0:
            logger.warning(f"No images for month {month:02d} — skipping")
            continue

        baseline = monthly.median().clip(aoi)
        monthly_baselines[month] = baseline
        logger.info(f"Month {month:02d}: baseline from {count} images")

    logger.success(f"Built {len(monthly_baselines)} monthly baselines")
    return monthly_baselines


# ============================================================
# Compute baseline statistics (mean + std per pixel)
# ============================================================

def compute_baseline_stats(
    baseline:   ee.Image,
    collection: ee.ImageCollection,
    aoi:        ee.Geometry,
    scale:      int = 100
) -> dict:
    """
    Compute mean and standard deviation of the baseline over the AOI.
    Used to set the change detection threshold (mean ± N * std).

    scale: spatial resolution for stats in metres (100m is faster than 10m)
    """
    stats = baseline.reduceRegion(
        reducer  = ee.Reducer.mean().combine(
                       ee.Reducer.stdDev(), sharedInputs=True
                   ),
        geometry = aoi,
        scale    = scale,
        maxPixels= 1e9
    ).getInfo()

    logger.info("Baseline statistics:")
    for key, val in stats.items():
        if val is not None:
            logger.info(f"  {key}: {val:.4f} dB")

    return stats


# ============================================================
# Export baseline to Google Drive
# ============================================================

def export_baseline(
    baseline: ee.Image,
    aoi:      ee.Geometry,
    config:   dict,
    label:    str = "baseline_2020_2022",
    folder:   str = "nigeria_sar"
) -> ee.batch.Task:
    """
    Export the baseline mosaic to Google Drive as a GeoTIFF.
    This is a one-time operation — baseline doesn't change.
    """
    task = ee.batch.Export.image.toDrive(
        image          = baseline,
        description    = f"s1_{label}",
        folder         = folder,
        fileNamePrefix = f"s1_{label}",
        region         = aoi,
        scale          = config["sentinel1"]["resolution_m"],
        crs            = "EPSG:4326",
        maxPixels      = 1e10,
        fileFormat     = "GeoTIFF"
    )
    task.start()
    logger.success(
        f"Baseline export started → Drive/{folder}/s1_{label}.tif\n"
        f"Track at: https://code.earthengine.google.com/tasks"
    )
    return task


# ============================================================
# Preview baseline in notebook
# ============================================================

def preview_baseline(
    baseline: ee.Image,
    aoi:      ee.Geometry,
    label:    str = "Baseline Mosaic (2020-2022)"
) -> "geemap.Map":
    """
    Render baseline mosaic on an interactive map.
    VV = structure sensitivity, VH = vegetation sensitivity.
    """
    import geemap

    vis_vv = {"bands": ["VV"], "min": -20, "max": -5,  "palette": ["black", "white"]}
    vis_vh = {"bands": ["VH"], "min": -25, "max": -10, "palette": ["black", "white"]}
    vis_rgb = {
        "bands": ["VV", "VH", "VV"],
        "min":   [-20, -25, -20],
        "max":   [-5,  -10, -5],
    }

    m = geemap.Map()
    m.centerObject(aoi, zoom=9)
    m.addLayer(baseline, vis_vv,  "VV band (structures)")
    m.addLayer(baseline, vis_vh,  "VH band (vegetation)")
    m.addLayer(baseline, vis_rgb, "RGB composite (VV/VH/VV)")
    m.addLayer(aoi, {}, "AOI")

    logger.info("Baseline map ready — toggle VV / VH / RGB layers")
    return m


# ============================================================
# Save baseline metadata locally
# ============================================================

def save_baseline_meta(
    config:     dict,
    stats:      dict,
    filter_method: str,
    image_count: int
) -> Path:
    """
    Save baseline metadata to disk so we know exactly how
    the baseline was built (reproducibility).
    """
    s1_cfg   = config["sentinel1"]
    out_dir  = Path(config["paths"]["processed_sar"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "baseline_meta.json"

    meta = {
        "baseline_start":  s1_cfg["baseline_start"],
        "baseline_end":    s1_cfg["baseline_end"],
        "filter_method":   filter_method,
        "image_count":     image_count,
        "orbit_pass":      s1_cfg["orbit_pass"],
        "polarisations":   s1_cfg["polarisation"],
        "resolution_m":    s1_cfg["resolution_m"],
        "stats":           stats,
        "built_at":        datetime.utcnow().isoformat()
    }

    with open(out_path, "w") as f:
        json.dump(meta, f, indent=2)

    logger.success(f"Baseline metadata saved → {out_path}")
    return out_path


# ============================================================
# CLI entry point
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[2]))

    import ee
    from src.ingestion.gee_download import init_gee, build_aoi

    init_gee()
    config = load_config()

    # Build baseline for Old Oyo core zone
    aoi      = build_aoi(config["aoi"]["zones"]["old_oyo_core"]["bbox"])
    baseline = build_baseline(aoi, config, filter_method="lee")

    # Print stats
    from src.ingestion.gee_download import get_s1_collection
    from src.preprocessing.speckle_filter import apply_filter_to_collection

    s1_cfg     = config["sentinel1"]
    collection = get_s1_collection(
        aoi, s1_cfg["baseline_start"], s1_cfg["baseline_end"], config
    )
    stats = compute_baseline_stats(baseline, collection, aoi)
    save_baseline_meta(config, stats, "lee", collection.size().getInfo())