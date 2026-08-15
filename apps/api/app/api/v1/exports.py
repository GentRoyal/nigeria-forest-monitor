import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.sites import (
    ExportCreateRequest,
    ExportData,
    ExportDownloadData,
    ExportDownloadResponse,
    ExportListResponse,
    ExportResponse,
)
from ...security.audit import record_audit
from ...security.permissions import Action, is_allowed
from ...settings import get_settings
from ..dependencies import Principal, current_principal
from ..errors import ApiError
from .sites import _visibility_sql

router = APIRouter(tags=["exports"])
_COLUMNS = "id,export_type,scope,filters,status,expires_at,checksum,sensitivity,download_count,created_at"


def _meta(request: Request) -> dict[str, UUID]:
    return {"request_id": UUID(request.state.request_id)}


def _local_export_path(object_key: str):
    root = Path(get_settings().export_root).resolve()
    candidate = (root / object_key).resolve()
    if root not in candidate.parents:
        raise ApiError(404, "export_not_ready", "Export not ready", "The export is not available for download.")
    return candidate


@router.post("/exports", response_model=ExportResponse, status_code=201)
async def create_export(payload: ExportCreateRequest, request: Request, response: Response, principal: Annotated[Principal, Depends(current_principal)], idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]) -> ExportResponse:
    if not is_allowed(principal.role, Action.EXPORT):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot create exports.")
    visibility, params = _visibility_sql(principal, "s")
    scope = {"resource": payload.resource, "site_id": str(payload.site_id)}
    key = hashlib.sha256(json.dumps({"scope": scope, "type": payload.export_type, "filters": payload.filters, "key": idempotency_key}, sort_keys=True).encode()).hexdigest()
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await (await connection.execute(f"SELECT id,sensitivity,current_grid_version_id FROM sites s WHERE id=%s AND ({visibility})", (payload.site_id, *params))).fetchone()
        if not site:
            raise ApiError(404, "site_not_found", "Site not found", "The export site does not exist or is unavailable.")
        existing = await (await connection.execute(f"SELECT {_COLUMNS} FROM exports WHERE filters->>'idempotency_key'=%s", (key,))).fetchone()
        if existing:
            response.status_code = 200
            return ExportResponse(data=ExportData.model_validate(existing), meta=_meta(request))
        export = await (await connection.execute(f"""INSERT INTO exports(organisation_id,requested_by,export_type,scope,filters,sensitivity)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING {_COLUMNS}""", (principal.organisation_id, principal.user_id, payload.export_type, Jsonb(scope), Jsonb({**payload.filters, "idempotency_key": key}), site["sensitivity"]))).fetchone()
        await connection.execute("""INSERT INTO processing_jobs(organisation_id,site_id,grid_version_id,job_type,trigger_type,priority,idempotency_key,requested_configuration,requested_by)
            VALUES (%s,%s,%s,'export','manual',5,%s,%s,%s)""", (principal.organisation_id, payload.site_id, site["current_grid_version_id"], key, Jsonb({"export_id": str(export["id"])}), principal.user_id))
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id, action="export.requested", target_type="export", target_id=export["id"], after=scope)
    return ExportResponse(data=ExportData.model_validate(export), meta=_meta(request))


@router.get("/exports", response_model=ExportListResponse)
async def list_exports(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ExportListResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(f"SELECT {_COLUMNS} FROM exports WHERE requested_by=%s ORDER BY created_at DESC", (principal.user_id,))).fetchall()
    return ExportListResponse(data=[ExportData.model_validate(row) for row in rows], meta=_meta(request))


@router.get("/exports/{export_id}", response_model=ExportResponse)
async def get_export(export_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ExportResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        export = await (await connection.execute(f"SELECT {_COLUMNS} FROM exports WHERE id=%s AND requested_by=%s", (export_id, principal.user_id))).fetchone()
    if not export:
        raise ApiError(404, "export_not_found", "Export not found", "The export does not exist or is unavailable.")
    return ExportResponse(data=ExportData.model_validate(export), meta=_meta(request))


@router.post("/exports/{export_id}/download", response_model=ExportDownloadResponse)
async def download_export(export_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ExportDownloadResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        export = await (await connection.execute("SELECT id,status,expires_at,result_object_key FROM exports WHERE id=%s AND requested_by=%s FOR UPDATE", (export_id, principal.user_id))).fetchone()
        if not export or export["status"] != "completed" or not export["result_object_key"]:
            raise ApiError(404, "export_not_ready", "Export not ready", "The export is not available for download.")
        if export["expires_at"] and export["expires_at"] <= datetime.now(UTC):
            raise ApiError(410, "export_expired", "Export expired", "Request a new export.")
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await connection.execute("UPDATE exports SET download_count=download_count+1 WHERE id=%s", (export_id,))
    return ExportDownloadResponse(data=ExportDownloadData(export_id=export_id, reference=f"/api/v1/exports/{export_id}/content?expires_at={expires_at.isoformat()}", expires_at=expires_at), meta=_meta(request))


@router.get("/exports/{export_id}/content", response_class=FileResponse)
async def export_content(export_id: UUID, principal: Annotated[Principal, Depends(current_principal)]):
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        export = await (await connection.execute("SELECT export_type,status,expires_at,result_object_key FROM exports WHERE id=%s AND requested_by=%s", (export_id, principal.user_id))).fetchone()
    if not export or export["status"] != "completed" or not export["result_object_key"] or (export["expires_at"] and export["expires_at"] <= datetime.now(UTC)):
        raise ApiError(404, "export_not_ready", "Export not ready", "The export is not available for download.")
    path = _local_export_path(export["result_object_key"])
    if not path.is_file():
        raise ApiError(404, "export_not_ready", "Export not ready", "The export file is unavailable.")
    media_type = "application/geo+json" if export["export_type"] == "geojson" else "text/csv"
    return FileResponse(path, media_type=media_type, filename=path.name)
