# ============================================================
# src/ingestion/grid.py
# Builds a regular grid over the AOI for cell-level analysis
# ============================================================

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, Point
import yaml
import ee
from pathlib import Path
from loguru import logger


# ============================================================
# Load config
# ============================================================

def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# Build grid
# ============================================================

def create_grid(config: dict, zone: str = "full") -> gpd.GeoDataFrame:
    """
    Build a regular lat/lon grid over the AOI.
    Each cell is resolution_deg x resolution_deg degrees.

    Returns a GeoDataFrame with columns:
        cell_id, lat, lon, geometry, zone
    """
    res = config["grid"]["resolution_deg"]
    crs = config["grid"]["crs"]

    # Select bbox
    if zone == "full":
        lon_min, lat_min, lon_max, lat_max = config["aoi"]["bbox"]
        zone_label = "full"
    else:
        lon_min, lat_min, lon_max, lat_max = config["aoi"]["zones"][zone]["bbox"]
        zone_label = zone

    # Generate grid cell origins (bottom-left corners)
    lons = np.arange(lon_min, lon_max, res)
    lats = np.arange(lat_min, lat_max, res)

    records = []
    cell_id = 0

    for lat in lats:
        for lon in lons:
            centroid_lat = round(lat + res / 2, 6)
            centroid_lon = round(lon + res / 2, 6)
            cell_geom = box(lon, lat, lon + res, lat + res)

            records.append({
                "cell_id":      cell_id,
                "lat":          centroid_lat,   # centroid (not corner)
                "lon":          centroid_lon,
                "lat_min":      round(lat, 6),
                "lon_min":      round(lon, 6),
                "lat_max":      round(lat + res, 6),
                "lon_max":      round(lon + res, 6),
                "zone":         zone_label,
                "geometry":     cell_geom
            })
            cell_id += 1

    gdf = gpd.GeoDataFrame(records, crs=crs)

    logger.success(
        f"Grid created: {len(gdf)} cells | "
        f"res={res}° (~{res * 111:.1f} km) | zone={zone_label}"
    )
    return gdf


# ============================================================
# Tag cells by named zone
# ============================================================

def tag_zones(grid: gpd.GeoDataFrame, config: dict) -> gpd.GeoDataFrame:
    """
    Tag each grid cell with the named sub-zone it falls in.
    Cells can overlap multiple zones — the most specific wins.
    """
    grid = grid.copy()
    grid["zone"] = "outside"

    for zone_key, zone_cfg in config["aoi"]["zones"].items():
        lon_min, lat_min, lon_max, lat_max = zone_cfg["bbox"]
        zone_box = box(lon_min, lat_min, lon_max, lat_max)

        mask = grid.geometry.centroid.within(zone_box)
        grid.loc[mask, "zone"] = zone_key

    counts = grid["zone"].value_counts().to_dict()
    logger.info(f"Zone tagging: {counts}")
    return grid


# ============================================================
# Convert grid cell to GEE geometry
# ============================================================

def cell_to_ee_geometry(row: pd.Series) -> ee.Geometry.Rectangle:
    """Convert a single grid cell (GeoDataFrame row) to a GEE Rectangle."""
    return ee.Geometry.Rectangle([
        row["lon_min"], row["lat_min"],
        row["lon_max"], row["lat_max"]
    ])


def grid_to_ee_feature_collection(grid: gpd.GeoDataFrame) -> ee.FeatureCollection:
    """
    Convert the full grid to a GEE FeatureCollection.
    Useful for server-side operations in GEE.
    """
    features = []
    for _, row in grid.iterrows():
        geom = cell_to_ee_geometry(row)
        feat = ee.Feature(geom, {
            "cell_id": int(row["cell_id"]),
            "lat":     float(row["lat"]),
            "lon":     float(row["lon"]),
            "zone":    str(row["zone"])
        })
        features.append(feat)

    fc = ee.FeatureCollection(features)
    logger.info(f"Converted {len(grid)} cells to GEE FeatureCollection")
    return fc


# ============================================================
# Forest mask — keep only forested cells
# ============================================================

def apply_forest_mask(
    grid: gpd.GeoDataFrame,
    config: dict,
    tree_cover_threshold: int = 30
) -> gpd.GeoDataFrame:
    """
    Filter grid cells to only those with significant tree cover.
    Uses Hansen Global Forest Change dataset (GEE).
    Removes urban, agricultural, and water cells from analysis.

    tree_cover_threshold: minimum % canopy cover to keep cell (default 30%)
    """
    logger.info(f"Applying forest mask (tree cover >= {tree_cover_threshold}%)...")

    hansen = ee.Image("UMD/hansen/global_forest_change_2023_v1_11")
    tree_cover = hansen.select("treecover2000")

    keep_ids = []
    for _, row in grid.iterrows():
        cell_geom = cell_to_ee_geometry(row)
        mean_cover = (
            tree_cover
            .reduceRegion(
                reducer  = ee.Reducer.mean(),
                geometry = cell_geom,
                scale    = 30,
                maxPixels= 1e6
            )
            .get("treecover2000")
            .getInfo()
        )
        if mean_cover is not None and mean_cover >= tree_cover_threshold:
            keep_ids.append(row["cell_id"])

    forest_grid = grid[grid["cell_id"].isin(keep_ids)].copy()
    removed = len(grid) - len(forest_grid)

    logger.success(
        f"Forest mask applied: {len(forest_grid)} forest cells kept, "
        f"{removed} non-forest cells removed"
    )
    return forest_grid


# ============================================================
# Save / load grid
# ============================================================

def save_grid(grid: gpd.GeoDataFrame, config: dict, name: str = "grid") -> Path:
    """Save grid to processed data folder as GeoPackage."""
    out_dir = Path(config["paths"]["processed_sar"]).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.gpkg"
    grid.to_file(out_path, driver="GPKG")
    logger.success(f"Grid saved → {out_path}")
    return out_path


def load_grid(config: dict, name: str = "grid") -> gpd.GeoDataFrame:
    """Load a previously saved grid."""
    path = Path(config["paths"]["processed_sar"]).parent / f"{name}.gpkg"
    if not path.exists():
        raise FileNotFoundError(f"Grid not found at {path}. Run create_grid() first.")
    grid = gpd.read_file(path)
    logger.info(f"Grid loaded: {len(grid)} cells from {path}")
    return grid


# ============================================================
# Visualise grid on geemap
# ============================================================

def preview_grid(
    grid: gpd.GeoDataFrame,
    config: dict,
    color_by_zone: bool = True
) -> "geemap.Map":
    """
    Overlay the grid on an interactive geemap Map.
    Color cells by zone if color_by_zone=True.
    """
    import geemap

    zone_colors = {
        "old_oyo_core":  "#e74c3c",   # red
        "kwara_border":  "#f39c12",   # orange
        "kainji_link":   "#2ecc71",   # green
        "outside":       "#95a5a6",   # grey
        "full":          "#3498db",   # blue
    }

    center = config["dashboard"]["map_center"]
    zoom   = config["dashboard"]["map_zoom"]

    m = geemap.Map(center=center, zoom=zoom)

    if color_by_zone:
        for zone_key, color in zone_colors.items():
            subset = grid[grid["zone"] == zone_key]
            if len(subset) == 0:
                continue
            m.add_gdf(
                subset,
                layer_name = zone_key,
                style = {
                    "color":       color,
                    "fillColor":   color,
                    "opacity":     0.8,
                    "fillOpacity": 0.2,
                    "weight":      1
                }
            )
    else:
        m.add_gdf(grid, layer_name="grid")

    logger.info(f"Grid map rendered: {len(grid)} cells")
    return m


# ============================================================
# CLI entry point
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[2]))

    config = load_config()

    # Build full grid
    grid = create_grid(config, zone="full")

    # Tag with named zones
    grid = tag_zones(grid, config)

    # Save
    save_grid(grid, config)

    # Summary
    print("\nGrid summary:")
    print(grid.groupby("zone")["cell_id"].count().rename("cells"))
    print(f"\nTotal cells: {len(grid)}")
    print(f"Cell size  : {config['grid']['resolution_deg']}° "
          f"(~{config['grid']['resolution_deg'] * 111:.1f} km)")