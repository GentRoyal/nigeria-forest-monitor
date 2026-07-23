# ============================================================
# src/ingestion/grid.py
# Builds a regular grid over the AOI for cell-level analysis
# ============================================================

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, Point
import ee
from pathlib import Path
from loguru import logger
from src.config import load_config, resolve_path



# ============================================================
# Build grid
# ============================================================

def create_grid(config: dict, zone: str = "full") -> gpd.GeoDataFrame:
    """Build a bounded regular grid over the configured AOI."""
    res = float(config["grid"]["resolution_deg"])
    crs = config["grid"]["crs"]
    if res <= 0:
        raise ValueError("grid.resolution_deg must be positive")
    if zone == "full":
        lon_min, lat_min, lon_max, lat_max = map(float, config["aoi"]["bbox"])
        zone_label = "full"
    else:
        try:
            lon_min, lat_min, lon_max, lat_max = map(float, config["aoi"]["zones"][zone]["bbox"])
        except KeyError as exc:
            raise ValueError(f"Unknown zone: {zone}") from exc
        zone_label = zone

    lon_count = int(np.ceil((lon_max - lon_min) / res - 1e-12))
    lat_count = int(np.ceil((lat_max - lat_min) / res - 1e-12))
    records = []
    cell_id = 0
    for lat_index in range(lat_count):
        bottom = lat_min + lat_index * res
        top = min(bottom + res, lat_max)
        for lon_index in range(lon_count):
            left = lon_min + lon_index * res
            right = min(left + res, lon_max)
            records.append({
                "cell_id": cell_id,
                "lat": round((bottom + top) / 2, 6),
                "lon": round((left + right) / 2, 6),
                "lat_min": round(bottom, 6),
                "lon_min": round(left, 6),
                "lat_max": round(top, 6),
                "lon_max": round(right, 6),
                "zone": zone_label,
                "geometry": box(left, bottom, right, top),
            })
            cell_id += 1

    grid = gpd.GeoDataFrame(records, crs=crs)
    logger.success(
        f"Grid created: {len(grid)} cells | res={res}° (~{res * 111:.1f} km) | zone={zone_label}"
    )
    return grid

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

        centroids = gpd.GeoSeries(
            gpd.points_from_xy(grid["lon"], grid["lat"]), crs=grid.crs
        )
        mask = centroids.within(zone_box)
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
    tree_cover_threshold: int = 30,
) -> gpd.GeoDataFrame:
    """Keep forest cells using one server-side Hansen reduction."""
    if grid.empty:
        return grid.copy()
    if not 0 <= tree_cover_threshold <= 100:
        raise ValueError("tree_cover_threshold must be between 0 and 100")

    logger.info(f"Applying forest mask (tree cover >= {tree_cover_threshold}%)...")
    tree_cover = ee.Image("UMD/hansen/global_forest_change_2023_v1_11").select("treecover2000")
    reduced = tree_cover.reduceRegions(
        collection=grid_to_ee_feature_collection(grid),
        reducer=ee.Reducer.mean(),
        scale=30,
        tileScale=8,
    ).getInfo()
    keep_ids = {
        int(feature["properties"]["cell_id"])
        for feature in reduced.get("features", [])
        if feature.get("properties", {}).get("mean") is not None
        and float(feature["properties"]["mean"]) >= tree_cover_threshold
    }
    forest_grid = grid[grid["cell_id"].isin(keep_ids)].copy()
    logger.success(
        f"Forest mask applied: {len(forest_grid)} forest cells kept, "
        f"{len(grid) - len(forest_grid)} non-forest cells removed"
    )
    return forest_grid

# ============================================================
# Save / load grid
# ============================================================

def save_grid(grid: gpd.GeoDataFrame, config: dict, name: str = "grid") -> Path:
    """Save grid to processed data folder as GeoPackage."""
    out_dir = resolve_path(config, "processed_sar", create=True).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.gpkg"
    grid.to_file(out_path, driver="GPKG")
    logger.success(f"Grid saved → {out_path}")
    return out_path


def load_grid(config: dict, name: str = "grid") -> gpd.GeoDataFrame:
    """Load a previously saved grid."""
    path = resolve_path(config, "processed_sar").parent / f"{name}.gpkg"
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