# ============================================================
# src/ingestion/gee_download.py
# Downloads Sentinel-1 SAR composites from Google Earth Engine
# ============================================================

import ee
import geemap
import yaml
import os
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# Initialise GEE
# ============================================================

def init_gee() -> None:
    """Authenticate and initialise Google Earth Engine."""
    project = os.getenv("GEE_PROJECT")
    if not project:
        raise EnvironmentError(
            "GEE_PROJECT not set. Add it to your .env file.\n"
            "Example: GEE_PROJECT=ee-yourname-nigeria-monitor"
        )
    try:
        ee.Initialize(project=project)
        logger.success(f"GEE initialised — project: {project}")
    except Exception:
        logger.warning("GEE credentials not found. Running authentication...")
        ee.Authenticate()
        ee.Initialize(project=project)
        logger.success("GEE authenticated and initialised.")


# ============================================================
# Load config
# ============================================================

def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# Build Area of Interest
# ============================================================

def build_aoi(bbox: list) -> ee.Geometry.Rectangle:
    """
    Build a GEE geometry from a [lon_min, lat_min, lon_max, lat_max] bbox.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    aoi = ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max])
    logger.info(f"AOI: lon [{lon_min}, {lon_max}] lat [{lat_min}, {lat_max}]")
    return aoi


# ============================================================
# Fetch Sentinel-1 collection
# ============================================================

def get_s1_collection(
    aoi: ee.Geometry,
    start_date: str,
    end_date: str,
    config: dict
) -> ee.ImageCollection:
    """
    Filter Sentinel-1 GRD collection by AOI, date, mode, and polarisation.
    Returns a collection with VV and VH bands in dB scale.
    """
    s1_cfg = config["sentinel1"]

    collection = (
        ee.ImageCollection(s1_cfg["collection"])
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq("instrumentMode", s1_cfg["instrument_mode"]))
        .filter(ee.Filter.eq("orbitProperties_pass", s1_cfg["orbit_pass"]))
        .filter(ee.Filter.listContains(
            "transmitterReceiverPolarisation", "VV"
        ))
        .filter(ee.Filter.listContains(
            "transmitterReceiverPolarisation", "VH"
        ))
        .select(["VV", "VH"])
    )

    count = collection.size().getInfo()
    logger.info(
        f"Found {count} Sentinel-1 images | "
        f"{start_date} → {end_date}"
    )
    return collection


# ============================================================
# Build monthly composites
# ============================================================

def build_monthly_composites(
    collection: ee.ImageCollection,
    start_date: str,
    end_date: str,
    aoi: ee.Geometry
) -> list[dict]:
    """
    Build median monthly composites over the AOI.
    Returns a list of dicts: {year, month, image}.
    Median composite reduces speckle better than single-image.
    """
    composites = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end     = datetime.strptime(end_date,   "%Y-%m-%d")

    while current < end:
        month_start = current.strftime("%Y-%m-%d")
        month_end   = (current + relativedelta(months=1)).strftime("%Y-%m-%d")

        monthly = collection.filterDate(month_start, month_end)
        count   = monthly.size().getInfo()

        if count == 0:
            logger.warning(f"No images for {current.strftime('%Y-%m')} — skipping")
            current += relativedelta(months=1)
            continue

        composite = monthly.median().clip(aoi)
        composites.append({
            "year":  current.year,
            "month": current.month,
            "label": current.strftime("%Y-%m"),
            "image": composite,
            "count": count
        })
        logger.info(f"Composite {current.strftime('%Y-%m')} built from {count} images")
        current += relativedelta(months=1)

    logger.success(f"Built {len(composites)} monthly composites")
    return composites


# ============================================================
# Export composite to Google Drive
# ============================================================

def export_composite_to_drive(
    composite: ee.Image,
    label: str,
    aoi: ee.Geometry,
    config: dict,
    folder: str = "nigeria_sar"
) -> ee.batch.Task:
    """
    Export a composite to Google Drive as a GeoTIFF.
    Returns the export task (non-blocking — check status separately).
    """
    res = config["sentinel1"]["resolution_m"]
    task = ee.batch.Export.image.toDrive(
        image       = composite,
        description = f"s1_composite_{label}",
        folder      = folder,
        fileNamePrefix = f"s1_{label}",
        region      = aoi,
        scale       = res,
        crs         = "EPSG:4326",
        maxPixels   = 1e10,
        fileFormat  = "GeoTIFF"
    )
    task.start()
    logger.info(f"Export started: s1_{label} → Drive/{folder}/")
    return task


# ============================================================
# Quick visualisation (for notebooks)
# ============================================================

def preview_composite(
    composite: ee.Image,
    aoi: ee.Geometry,
    label: str = "SAR Preview"
) -> geemap.Map:
    """
    Returns an interactive geemap Map with VV band visualised.
    Use inside a Jupyter notebook cell.
    """
    vis_params = {
        "bands": ["VV"],
        "min": -25,
        "max": 0,
        "palette": ["black", "white"]
    }

    m = geemap.Map()
    m.centerObject(aoi, zoom=9)
    m.addLayer(composite, vis_params, label)
    m.addLayer(aoi, {}, "AOI boundary")
    return m


# ============================================================
# Convenience: build baseline mosaic
# ============================================================

def build_baseline_mosaic(
    aoi: ee.Geometry,
    config: dict
) -> ee.Image:
    """
    Build the full baseline mosaic (median over the entire baseline period).
    This is the stable reference image for change detection.
    """
    s1_cfg = config["sentinel1"]
    collection = get_s1_collection(
        aoi,
        s1_cfg["baseline_start"],
        s1_cfg["baseline_end"],
        config
    )
    mosaic = collection.median().clip(aoi)
    count  = collection.size().getInfo()
    logger.success(
        f"Baseline mosaic built from {count} images | "
        f"{s1_cfg['baseline_start']} → {s1_cfg['baseline_end']}"
    )
    return mosaic


# ============================================================
# CLI entry point
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Download Sentinel-1 SAR composites from GEE"
    )
    parser.add_argument(
        "--config", default="configs/config.yaml",
        help="Path to config.yaml"
    )
    parser.add_argument(
        "--mode", choices=["baseline", "monitor", "single"],
        default="baseline",
        help="Which period to download"
    )
    parser.add_argument(
        "--zone", default="full",
        help="Zone from config (full | old_oyo_core | kwara_border | kainji_link)"
    )
    parser.add_argument(
        "--export", action="store_true",
        help="Export composites to Google Drive"
    )
    args = parser.parse_args()

    # --- Setup ---
    init_gee()
    config = load_config(args.config)
    s1_cfg = config["sentinel1"]

    # --- AOI ---
    if args.zone == "full":
        bbox = config["aoi"]["bbox"]
    else:
        bbox = config["aoi"]["zones"][args.zone]["bbox"]
    aoi = build_aoi(bbox)

    # --- Date range ---
    if args.mode == "baseline":
        start, end = s1_cfg["baseline_start"], s1_cfg["baseline_end"]
    elif args.mode == "monitor":
        start, end = s1_cfg["monitor_start"], s1_cfg["monitor_end"]
    else:
        # Single: last 30 days
        end   = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    # --- Fetch + composite ---
    collection = get_s1_collection(aoi, start, end, config)
    composites = build_monthly_composites(collection, start, end, aoi)

    # --- Optionally export ---
    if args.export:
        logger.info("Exporting composites to Google Drive...")
        tasks = []
        for c in composites:
            task = export_composite_to_drive(
                c["image"], c["label"], aoi, config
            )
            tasks.append(task)
        logger.success(
            f"{len(tasks)} export tasks started. "
            "Check progress at: https://code.earthengine.google.com/tasks"
        )
    else:
        logger.info(
            f"Built {len(composites)} composites in memory. "
            "Pass --export to send to Google Drive."
        )