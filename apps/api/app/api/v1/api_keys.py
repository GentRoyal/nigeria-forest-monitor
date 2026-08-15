import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.api_keys import (
    ApiKeyCreatedData,
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyData,
    ApiKeyListResponse,
    ApiKeyResponse,
    UsageData,
    UsageResponse,
)
from ...security.audit import record_audit
from ...security.permissions import Action, is_allowed
from ...security.tokens import hash_opaque_token
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["api-keys", "administration"])
_COLUMNS = "id,name,key_prefix,scopes,expires_at,revoked_at,last_used_at,created_at"


def _meta(request: Request) -> dict[str, UUID]:
    return {"request_id": UUID(request.state.request_id)}



def _require_key_management(principal: Principal) -> None:
    if principal.api_key_id or not is_allowed(principal.role, Action.MANAGE_ORGANISATION):
        raise ApiError(403, "permission_denied", "Permission denied", "Only organisation owners and administrators using an interactive session can manage API keys.")


@router.get("/api-keys", response_model=ApiKeyListResponse)
async def list_api_keys(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ApiKeyListResponse:
    _require_key_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(f"SELECT {_COLUMNS} FROM api_keys ORDER BY created_at DESC")).fetchall()
    return ApiKeyListResponse(data=[ApiKeyData.model_validate(row) for row in rows], meta=_meta(request))


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(payload: ApiKeyCreateRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ApiKeyCreatedResponse:
    _require_key_management(principal)
    if payload.expires_at and payload.expires_at <= datetime.now(UTC):
        raise ApiError(422, "invalid_expiry", "Invalid expiry", "API key expiry must be in the future.")
    prefix = f"nfm_{secrets.token_hex(4)}"
    secret = f"{prefix}_{secrets.token_urlsafe(32)}"
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        try:
            key = await (await connection.execute(
                f"""INSERT INTO api_keys(organisation_id,accountable_user_id,name,key_prefix,secret_hash,scopes,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {_COLUMNS}""",
                (principal.organisation_id, principal.user_id, payload.name, prefix, hash_opaque_token(secret), Jsonb(sorted(payload.scopes)), payload.expires_at),
            )).fetchone()
        except UniqueViolation as error:
            raise ApiError(409, "api_key_name_conflict", "API key already exists", "Choose a different API key name.") from error
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id, action="api_key.created", target_type="api_key", target_id=key["id"], after={"name": key["name"], "prefix": prefix, "scopes": key["scopes"], "expires_at": key["expires_at"].isoformat() if key["expires_at"] else None})
    return ApiKeyCreatedResponse(data=ApiKeyCreatedData.model_validate({**key, "secret": secret}), meta=_meta(request))


@router.delete("/api-keys/{key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(key_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ApiKeyResponse:
    _require_key_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        key = await (await connection.execute(f"UPDATE api_keys SET revoked_at=COALESCE(revoked_at,now()) WHERE id=%s RETURNING {_COLUMNS}", (key_id,))).fetchone()
        if not key:
            raise ApiError(404, "api_key_not_found", "API key not found", "The API key does not exist.")
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id, action="api_key.revoked", target_type="api_key", target_id=key_id, after={"name": key["name"]})
    return ApiKeyResponse(data=ApiKeyData.model_validate(key), meta=_meta(request))


@router.get("/admin/usage", response_model=UsageResponse)
async def get_usage(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> UsageResponse:
    _require_key_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        row = await (await connection.execute("""SELECT
          (SELECT count(*) FROM user_profiles WHERE status='"'"'active'"'"') active_members,
          (SELECT count(*) FROM sites WHERE status<>'"'"'deleted'"'"') active_sites,
          (SELECT count(*) FROM processing_jobs WHERE status IN ('"'"'queued'"'"','"'"'running'"'"')) queued_or_running_jobs,
          (SELECT count(*) FROM raster_assets) stored_assets,
          (SELECT COALESCE(sum(size_bytes),0) FROM raster_assets) asset_bytes,
          (SELECT count(*) FROM api_keys WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())) active_api_keys,
          (SELECT count(*) FROM exports WHERE status='"'"'completed'"'"') completed_exports""")).fetchone()
    return UsageResponse(data=UsageData.model_validate(row), meta=_meta(request))
