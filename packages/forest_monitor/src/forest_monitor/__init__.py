"""Reusable geospatial analysis package for Nigeria Forest Monitor."""

from .runtime import configure_geospatial_runtime

configure_geospatial_runtime()

__all__ = ["configure_geospatial_runtime"]
