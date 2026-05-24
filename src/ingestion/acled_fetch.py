# ============================================================
# src/ingestion/acled_fetch.py
# Fetches and processes ACLED incident data for Nigeria
# Updated to use OAuth 2.0 authentication (2025 API change)
# ============================================================

import os
import requests
import pandas as pd
import geopandas as gpd
from pathlib import Path
from loguru import logger
import yaml
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# Load config
# ============================================================

def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# OAuth: get access token
# ============================================================

def get_acled_token() -> str:
    """
    Authenticate with ACLED via OAuth and return a Bearer token.
    Requires ACLED_EMAIL and ACLED_PASSWORD in .env

    Add to your .env:
        ACLED_EMAIL=your@email.com
        ACLED_PASSWORD=yourpassword
    """
    email    = os.getenv("ACLED_EMAIL")
    password = os.getenv("ACLED_PASSWORD")

    if not email or not password:
        raise EnvironmentError(
            "ACLED_EMAIL and ACLED_PASSWORD must be set in your .env file."
        )

    logger.info("Requesting ACLED OAuth token...")

    response = requests.post(
        "https://acleddata.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "username":   email,
            "password":   password,
            "grant_type": "password",
            "client_id":  "acled",
            "scope":      "authenticated"
        },
        timeout=30
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    logger.success("ACLED token obtained (valid 24h)")
    return token


def refresh_acled_token(refresh_token: str) -> str:
    """Use a refresh token to get a new access token (avoids re-login)."""
    response = requests.post(
        "https://acleddata.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
            "client_id":     "acled"
        },
        timeout=30
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    logger.success("ACLED token refreshed")
    return token


# ============================================================
# Fetch from ACLED API
# ============================================================

def fetch_acled(
    config:     dict,
    start_year: int = 2019,
    end_year:   int = 2025,
    limit:      int = 5000
) -> pd.DataFrame:
    """
    Fetch Nigeria incident data from the ACLED API using OAuth.
    Returns a DataFrame of all incidents.
    """
    token       = get_acled_token()
    acled_cfg   = config["acled"]
    event_types = "|".join(acled_cfg["event_types"])

    headers = {"Authorization": f"Bearer {token}"}

    params = {
        "country":    acled_cfg["country"],
        "event_type": event_types,
        "year":       f"{start_year}|{end_year}",
        "fields":     "event_date|event_type|sub_event_type|actor1|"
                      "location|latitude|longitude|fatalities|notes",
        "limit":      limit,
    }

    logger.info(f"Fetching ACLED data for Nigeria ({start_year}-{end_year})...")

    response = requests.get(
        "https://acleddata.com/api/acled/read",
        headers=headers,
        params=params,
        timeout=30
    )
    response.raise_for_status()

    records = response.json().get("data", [])

    if not records:
        logger.warning("ACLED returned 0 records. Check filters or credentials.")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["latitude"]   = pd.to_numeric(df["latitude"],   errors="coerce")
    df["longitude"]  = pd.to_numeric(df["longitude"],  errors="coerce")
    df["fatalities"] = pd.to_numeric(df["fatalities"], errors="coerce").fillna(0)
    df = df.dropna(subset=["latitude", "longitude"])

    logger.success(f"Fetched {len(df):,} ACLED incidents")
    return df


# ============================================================
# Filter to AOI
# ============================================================

def filter_to_aoi(
    df:     pd.DataFrame,
    config: dict,
    zone:   str = "full"
) -> gpd.GeoDataFrame:
    """Filter ACLED records to the project AOI."""
    if zone == "full":
        lon_min, lat_min, lon_max, lat_max = config["aoi"]["bbox"]
    else:
        lon_min, lat_min, lon_max, lat_max = config["aoi"]["zones"][zone]["bbox"]

    mask = (
        (df["longitude"] >= lon_min) & (df["longitude"] <= lon_max) &
        (df["latitude"]  >= lat_min) & (df["latitude"]  <= lat_max)
    )
    filtered = df[mask].copy()

    gdf = gpd.GeoDataFrame(
        filtered,
        geometry = gpd.points_from_xy(filtered["longitude"], filtered["latitude"]),
        crs      = config["grid"]["crs"]
    )

    logger.success(
        f"Filtered to AOI: {len(gdf):,} incidents "
        f"(from {len(df):,} total Nigeria records)"
    )
    return gdf


# ============================================================
# Tag incidents onto grid cells
# ============================================================

def tag_incidents_to_grid(
    incidents: gpd.GeoDataFrame,
    grid:      gpd.GeoDataFrame,
    config:    dict
) -> gpd.GeoDataFrame:
    """Spatial join incidents to grid cells + compute per-cell scores."""
    joined = gpd.sjoin(
        incidents,
        grid[["cell_id", "lat", "lon", "zone", "geometry"]],
        how       = "left",
        predicate = "within"
    )

    cell_stats = (
        joined.groupby("cell_id")
        .agg(
            incident_count   = ("event_date", "count"),
            last_incident    = ("event_date", "max"),
            total_fatalities = ("fatalities", "sum"),
        )
        .reset_index()
    )

    # Recency bonus: incidents in last 12 months weighted 2x
    cutoff = incidents["event_date"].max() - pd.DateOffset(months=12)
    recent = (
        joined[joined["event_date"] >= cutoff]
        .groupby("cell_id")
        .size()
        .reset_index(name="recent_incidents")
    )
    cell_stats = cell_stats.merge(recent, on="cell_id", how="left")
    cell_stats["recent_incidents"] = cell_stats["recent_incidents"].fillna(0)

    max_count = cell_stats["incident_count"].max()
    if max_count > 0:
        cell_stats["acled_score"] = (
            (cell_stats["incident_count"] + cell_stats["recent_incidents"] * 2)
            / (max_count * 3)
        ).clip(0, 1)
    else:
        cell_stats["acled_score"] = 0.0

    grid_tagged = grid.merge(cell_stats, on="cell_id", how="left")
    grid_tagged["incident_count"]   = grid_tagged["incident_count"].fillna(0).astype(int)
    grid_tagged["recent_incidents"] = grid_tagged["recent_incidents"].fillna(0).astype(int)
    grid_tagged["total_fatalities"] = grid_tagged["total_fatalities"].fillna(0).astype(int)
    grid_tagged["acled_score"]      = grid_tagged["acled_score"].fillna(0.0)

    hot_cells = (grid_tagged["incident_count"] > 0).sum()
    logger.success(
        f"Grid tagged: {hot_cells} cells have historical incidents | "
        f"max in one cell: {cell_stats['incident_count'].max()}"
    )
    return grid_tagged


# ============================================================
# Proximity score
# ============================================================

def compute_proximity_scores(
    grid:      gpd.GeoDataFrame,
    incidents: gpd.GeoDataFrame,
    config:    dict
) -> gpd.GeoDataFrame:
    """Score cells by proximity to nearest historical incident."""
    from scipy.spatial import cKDTree
    import numpy as np

    radius_km  = config["acled"]["proximity_radius_km"]
    radius_deg = radius_km / 111.0

    incident_coords = incidents[["latitude", "longitude"]].to_numpy()
    grid_coords     = grid[["lat", "lon"]].to_numpy()

    tree     = cKDTree(incident_coords)
    dists, _ = tree.query(grid_coords, k=1)
    proximity   = np.clip(1 - (dists / radius_deg), 0, 1)
    no_incident = grid["incident_count"] == 0

    grid = grid.copy()
    grid.loc[no_incident, "acled_score"] = (
        grid.loc[no_incident, "acled_score"]
        .fillna(0)
        .add(proximity[no_incident] * 0.3)
        .clip(0, 1)
    )
    logger.info(f"Proximity scores applied to {no_incident.sum()} cells")
    return grid


# ============================================================
# Save / load
# ============================================================

def save_acled(df: pd.DataFrame, config: dict) -> Path:
    out_path = Path(config["paths"]["acled"]) / "acled_nigeria.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    logger.success(f"ACLED data saved -> {out_path}")
    return out_path


def load_acled(config: dict) -> pd.DataFrame:
    path = Path(config["paths"]["acled"]) / "acled_nigeria.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"ACLED data not found at {path}. Run fetch_acled() first."
        )
    df = pd.read_parquet(path)
    logger.info(f"ACLED loaded: {len(df):,} records from {path}")
    return df


# ============================================================
# Summary
# ============================================================

def incident_summary(incidents: gpd.GeoDataFrame) -> None:
    print("\n" + "=" * 50)
    print("ACLED INCIDENT SUMMARY - Nigeria Forest Corridor")
    print("=" * 50)
    print(f"Total incidents : {len(incidents):,}")
    print(f"Date range      : {incidents['event_date'].min().date()} -> "
          f"{incidents['event_date'].max().date()}")
    print(f"Total fatalities: {int(incidents['fatalities'].sum()):,}")
    print("\nBy event type:")
    print(incidents["event_type"].value_counts().to_string())
    print("\nTop 10 locations:")
    print(incidents["location"].value_counts().head(10).to_string())
    print("=" * 50)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[2]))

    config    = load_config()
    df        = fetch_acled(config)
    save_acled(df, config)
    incidents = filter_to_aoi(df, config)
    incident_summary(incidents)