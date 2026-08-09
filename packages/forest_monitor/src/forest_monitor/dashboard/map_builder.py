"""Build and save interactive risk maps."""

from __future__ import annotations

from pathlib import Path

import folium
import geopandas as gpd
from folium.features import GeoJsonTooltip
from loguru import logger

from forest_monitor.config import load_config, resolve_path

RISK_COLORS = {
    "low": "#2ca25f",
    "moderate": "#fec44f",
    "high": "#fe9929",
    "critical": "#de2d26",
}


def build_risk_map(
    scored_grid: gpd.GeoDataFrame,
    config: dict,
    incidents: gpd.GeoDataFrame | None = None,
) -> folium.Map:
    """Create a Folium map from a risk-scored grid."""
    required = {"cell_id", "risk_score", "risk_level", "geometry"}
    missing = sorted(required.difference(scored_grid.columns))
    if missing:
        raise ValueError(f"scored_grid is missing: {', '.join(missing)}")

    center = config["dashboard"]["map_center"]
    result = folium.Map(location=center, zoom_start=int(config["dashboard"]["map_zoom"]), tiles="CartoDB positron")
    display = scored_grid.to_crs("EPSG:4326").copy()
    display["risk_level"] = display["risk_level"].astype(str)
    display["risk_score"] = display["risk_score"].round(3)

    folium.GeoJson(
        display.to_json(),
        name="Risk cells",
        style_function=lambda feature: {
            "color": RISK_COLORS.get(feature["properties"].get("risk_level"), "#777777"),
            "fillColor": RISK_COLORS.get(feature["properties"].get("risk_level"), "#777777"),
            "weight": 1,
            "fillOpacity": 0.55,
        },
        tooltip=GeoJsonTooltip(
            fields=[field for field in ("cell_id", "zone", "risk_score", "risk_level", "change_score", "classifier_score", "acled_score") if field in display.columns],
            aliases=[field.replace("_", " ").title() for field in ("cell_id", "zone", "risk_score", "risk_level", "change_score", "classifier_score", "acled_score") if field in display.columns],
            localize=True,
        ),
    ).add_to(result)

    if incidents is not None and not incidents.empty:
        incident_layer = folium.FeatureGroup(name="Historical incidents", show=False)
        for row in incidents.to_crs("EPSG:4326").itertuples():
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                continue
            folium.CircleMarker(
                [geometry.y, geometry.x],
                radius=3,
                color="#54278f",
                fill=True,
                fill_opacity=0.7,
                tooltip=str(getattr(row, "location", "Incident")),
            ).add_to(incident_layer)
        incident_layer.add_to(result)

    folium.LayerControl(collapsed=False).add_to(result)
    return result


def save_risk_map(risk_map: folium.Map, config: dict, name: str = "risk_map.html") -> Path:
    output = resolve_path(config, "reports", create=True) / name
    risk_map.save(str(output))
    logger.success(f"Risk map saved -> {output}")
    return output


__all__ = ["build_risk_map", "save_risk_map", "RISK_COLORS", "load_config"]