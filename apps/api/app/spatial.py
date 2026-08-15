import hashlib
import json
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg import Error as PsycopgError


class AoiValidationError(ValueError):
    """A safe AOI validation error suitable for an API problem response."""


@dataclass(frozen=True)
class ValidatedAoi:
    geometry: dict[str, Any]
    area_sq_km: float
    vertex_count: int
    bounds: dict[str, float]
    checksum: str
    validation_result: dict[str, Any]


async def validate_aoi(
    connection: AsyncConnection,
    *,
    geometry: dict[str, Any],
    source_crs: str,
    provenance: dict[str, Any],
    max_area_sq_km: float,
    max_vertices: int,
) -> ValidatedAoi:
    try:
        source_srid = int(source_crs.split(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise AoiValidationError("source_crs must use the EPSG:<code> format") from error
    encoded_geometry = json.dumps(geometry, separators=(",", ":"))
    try:
        async with connection.transaction():
            raw = await (
                await connection.execute(
                    """
                    WITH source AS (
                      SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),%s) geometry
                    ), cleaned AS (
                      SELECT geometry,ST_RemoveRepeatedPoints(geometry) cleaned FROM source
                    )
                    SELECT GeometryType(geometry) geometry_type,
                      ST_IsEmpty(geometry) is_empty,
                      ST_IsValid(cleaned) is_valid,
                      ST_IsValidReason(cleaned) validity_reason,
                      ST_NPoints(geometry) input_vertices,
                      ST_NPoints(cleaned) cleaned_vertices,
                      ST_XMin(Box2D(geometry)) min_x,ST_XMax(Box2D(geometry)) max_x,
                      ST_YMin(Box2D(geometry)) min_y,ST_YMax(Box2D(geometry)) max_y
                    FROM cleaned
                    """,
                    (encoded_geometry, source_srid),
                )
            ).fetchone()
    except PsycopgError as error:
        raise AoiValidationError("geometry is not valid GeoJSON for the declared CRS") from error
    if not raw or raw["geometry_type"] not in {"POLYGON", "MULTIPOLYGON"}:
        raise AoiValidationError("geometry must be a Polygon or MultiPolygon")
    if raw["is_empty"]:
        raise AoiValidationError("geometry must not be empty")
    if not raw["is_valid"]:
        raise AoiValidationError(f"geometry topology is invalid: {raw['validity_reason']}")
    if source_srid == 4326 and (
        raw["min_x"] < -180 or raw["max_x"] > 180 or raw["min_y"] < -90 or raw["max_y"] > 90
    ):
        raise AoiValidationError("longitude or latitude coordinates are out of bounds")
    try:
        async with connection.transaction():
            normalised = await (
                await connection.execute(
                    """
                    WITH source AS (
                      SELECT ST_RemoveRepeatedPoints(
                        ST_SetSRID(ST_GeomFromGeoJSON(%s),%s)
                      ) geometry
                    ), projected AS (
                      SELECT ST_Transform(geometry,4326) geometry FROM source
                    ), normalised AS (
                      SELECT ST_Multi(ST_ForcePolygonCCW(geometry)) geometry FROM projected
                    )
                    SELECT ST_AsGeoJSON(geometry,9,0)::jsonb canonical_geometry,
                      ST_IsValid(geometry) is_valid,ST_IsEmpty(geometry) is_empty,
                      ST_NPoints(geometry) vertex_count,
                      ST_Area(geometry::geography)/1000000.0 area_sq_km,
                      ST_XMin(Box2D(geometry)) west,ST_YMin(Box2D(geometry)) south,
                      ST_XMax(Box2D(geometry)) east,ST_YMax(Box2D(geometry)) north
                    FROM normalised
                    """,
                    (encoded_geometry, source_srid),
                )
            ).fetchone()
    except PsycopgError as error:
        raise AoiValidationError("geometry could not be transformed to EPSG:4326") from error
    if not normalised or normalised["is_empty"] or not normalised["is_valid"]:
        raise AoiValidationError("transformed geometry is invalid or empty")
    if (
        normalised["west"] < -180
        or normalised["east"] > 180
        or normalised["south"] < -90
        or normalised["north"] > 90
    ):
        raise AoiValidationError("transformed longitude or latitude coordinates are out of bounds")
    vertex_count = int(normalised["vertex_count"])
    area_sq_km = float(normalised["area_sq_km"])
    if vertex_count > max_vertices:
        raise AoiValidationError(f"geometry exceeds the {max_vertices} vertex limit")
    if area_sq_km <= 0 or area_sq_km > max_area_sq_km:
        raise AoiValidationError(f"geometry exceeds the {max_area_sq_km:g} km² area limit")
    canonical = normalised["canonical_geometry"]
    checksum_document = {
        "geometry": canonical,
        "provenance": provenance,
        "source_crs": source_crs,
    }
    checksum = hashlib.sha256(
        json.dumps(checksum_document, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    bounds = {
        "west": float(normalised["west"]),
        "south": float(normalised["south"]),
        "east": float(normalised["east"]),
        "north": float(normalised["north"]),
    }
    validation_result = {
        "valid": True,
        "normalised_to": "EPSG:4326",
        "input_geometry_type": raw["geometry_type"],
        "input_vertex_count": int(raw["input_vertices"]),
        "vertex_count": vertex_count,
        "consecutive_duplicates_removed": int(raw["input_vertices"]) - int(raw["cleaned_vertices"]),
        "area_sq_km": area_sq_km,
        "bounds": bounds,
    }
    return ValidatedAoi(
        geometry=canonical,
        area_sq_km=area_sq_km,
        vertex_count=vertex_count,
        bounds=bounds,
        checksum=checksum,
        validation_result=validation_result,
    )
