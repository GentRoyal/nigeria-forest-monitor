import importlib.util
import os
from pathlib import Path
from typing import Annotated


def _isolate_native_data() -> None:
    rasterio_spec = importlib.util.find_spec("rasterio")
    if rasterio_spec is not None and rasterio_spec.origin is not None:
        rasterio_directory = Path(rasterio_spec.origin).resolve().parent
        for variable, directory in (
            ("PROJ_DATA", rasterio_directory / "proj_data"),
            ("GDAL_DATA", rasterio_directory / "gdal_data"),
        ):
            if directory.is_dir():
                os.environ[variable] = str(directory)
        return

    pyproj_spec = importlib.util.find_spec("pyproj")
    if pyproj_spec is not None and pyproj_spec.origin is not None:
        proj_data = Path(pyproj_spec.origin).resolve().parent / "proj_dir" / "share" / "proj"
        if proj_data.is_dir():
            os.environ["PROJ_DATA"] = str(proj_data)


_isolate_native_data()

from fastapi import FastAPI, HTTPException, Query, status  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from titiler.core.factory import TilerFactory  # noqa: E402

RASTER_ROOT = Path(os.getenv("NFM_RASTER_ROOT", "data/rasters")).resolve()


def local_raster_path(
    url: Annotated[str, Query(description="Absolute or local raster path")],
) -> str:
    """Restrict TiTiler to files beneath the configured local raster root."""
    candidate = Path(url)
    if not candidate.is_absolute():
        candidate = RASTER_ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(RASTER_ROOT):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="raster path is outside the configured storage root",
        )
    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="raster not found")
    return str(candidate)


app = FastAPI(
    title="Nigeria Forest Monitor Tiles",
    version="0.1.0",
    description="Restricted local COG tile service.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

cog = TilerFactory(router_prefix="/cog", path_dependency=local_raster_path)
app.include_router(cog.router, prefix="/cog", tags=["Cloud Optimized GeoTIFF"])


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok", "raster_root": str(RASTER_ROOT)}
