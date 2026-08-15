from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from ...db import tenant_connection
from ...schemas.sites import AssetData, AssetDownloadData, AssetDownloadResponse, AssetListResponse
from ...security.audit import record_audit
from ...security.permissions import Action, is_allowed
from ...settings import get_settings
from ..dependencies import Principal, current_principal
from ..errors import ApiError
from .sites import _visibility_sql

router = APIRouter(tags=["assets"])

_ASSET_COLUMNS = """
  a.id,a.observation_id,a.processing_run_id,a.asset_type,a.cog_valid,a.bands,
  a.resolution_metres,a.checksum,a.size_bytes,a.processing_version,a.lineage,a.created_at
"""


def _meta(request: Request) -> dict[str, UUID]:
    return {"request_id": UUID(request.state.request_id)}


def _local_asset_path(object_key: str) -> Path:
    root = Path(get_settings().asset_root).resolve()
    candidate = (root / object_key).resolve()
    if root not in candidate.parents:
        raise ApiError(404, "asset_not_found", "Asset not found", "The asset does not exist or is unavailable.")
    return candidate


@router.get("/observations/{observation_id}/assets", response_model=AssetListResponse)
async def list_observation_assets(observation_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> AssetListResponse:
    if not is_allowed(principal.role, Action.VIEW_SITE):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot view observation assets.")
    visibility, params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(
            f"""SELECT {_ASSET_COLUMNS} FROM raster_assets a JOIN observations o ON o.id=a.observation_id
            JOIN sites s ON s.id=o.site_id WHERE a.observation_id=%s AND ({visibility}) ORDER BY a.created_at""",
            (observation_id, *params),
        )).fetchall()
    return AssetListResponse(data=[AssetData.model_validate(row) for row in rows], meta=_meta(request))


@router.post("/assets/{asset_id}/download", response_model=AssetDownloadResponse)
async def create_download_reference(asset_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> AssetDownloadResponse:
    if not is_allowed(principal.role, Action.VIEW_SITE):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot download observation assets.")
    visibility, params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        asset = await (await connection.execute(
            f"""SELECT a.id,a.object_key FROM raster_assets a JOIN observations o ON o.id=a.observation_id
            JOIN sites s ON s.id=o.site_id WHERE a.id=%s AND ({visibility})""",
            (asset_id, *params),
        )).fetchone()
        if not asset or not asset["object_key"]:
            raise ApiError(404, "asset_not_found", "Asset not found", "The asset does not exist or is unavailable.")
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        path = _local_asset_path(asset["object_key"])
        if not path.is_file():
            raise ApiError(404, "asset_not_found", "Asset not found", "The asset file is unavailable.")
        reference = f"/api/v1/assets/{asset['id']}/content?expires_at={expires_at.isoformat()}"
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id,
            action="raster_asset.download_reference_created", target_type="raster_asset", target_id=asset_id,
            after={"expires_at": expires_at.isoformat()})
    return AssetDownloadResponse(data=AssetDownloadData(asset_id=asset_id, reference=reference, expires_at=expires_at), meta=_meta(request))


@router.get("/assets/{asset_id}/content", response_class=FileResponse)
async def asset_content(asset_id: UUID, principal: Annotated[Principal, Depends(current_principal)]):
    if not is_allowed(principal.role, Action.VIEW_SITE):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot download observation assets.")
    visibility, params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        asset = await (await connection.execute(
            f"""SELECT a.id,a.object_key,a.asset_type FROM raster_assets a JOIN observations o ON o.id=a.observation_id
            JOIN sites s ON s.id=o.site_id WHERE a.id=%s AND ({visibility})""",
            (asset_id, *params),
        )).fetchone()
    if not asset or not asset["object_key"]:
        raise ApiError(404, "asset_not_found", "Asset not found", "The asset does not exist or is unavailable.")
    path = _local_asset_path(asset["object_key"])
    if not path.is_file():
        raise ApiError(404, "asset_not_found", "Asset not found", "The asset file is unavailable.")
    media_type = "image/tiff" if path.suffix.lower() in {".tif", ".tiff"} else "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)
