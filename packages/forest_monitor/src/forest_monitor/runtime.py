"""Runtime isolation for geospatial native-data directories."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _package_directory(package: str) -> Path | None:
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve().parent


def configure_geospatial_runtime() -> None:
    """Prefer PROJ/GDAL data shipped in the active Python environment.

    Windows machine-level variables can point a virtual environment at a global
    Python or PostgreSQL installation with incompatible native data files.
    """
    rasterio_directory = _package_directory("rasterio")
    if rasterio_directory is not None:
        proj_data = rasterio_directory / "proj_data"
        if proj_data.is_dir():
            os.environ["PROJ_DATA"] = str(proj_data)

        gdal_data = rasterio_directory / "gdal_data"
        if gdal_data.is_dir():
            os.environ["GDAL_DATA"] = str(gdal_data)
        return

    pyproj_directory = _package_directory("pyproj")
    if pyproj_directory is not None:
        proj_data = pyproj_directory / "proj_dir" / "share" / "proj"
        if proj_data.is_dir():
            os.environ["PROJ_DATA"] = str(proj_data)
