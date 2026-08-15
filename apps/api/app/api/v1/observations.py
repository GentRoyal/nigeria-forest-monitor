from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ...db import tenant_connection
from ...schemas.sites import (
    ObservationData,
    ObservationListMeta,
    ObservationListResponse,
    ObservationResponse,
)
from ...security.cursors import (
    CursorError,
    CursorPosition,
    cursor_scope,
    decode_cursor,
    encode_cursor,
)
from ...security.permissions import Action, is_allowed
from ..dependencies import Principal, current_principal
from ..errors import ApiError
from .sites import _visibility_sql

router = APIRouter(tags=["observations"])

_OBSERVATION_COLUMNS = """
  o.id,o.site_id,o.catalogue_item_id,o.grid_version_id,o.baseline_observation_id,
  o.coverage_ratio,o.quality_assessment,o.eligibility,o.eligibility_reason,
  o.discovery_method,o.status,o.observed_at,o.created_at
"""


def _meta(request: Request, *, next_cursor: str | None = None) -> ObservationListMeta:
    return ObservationListMeta(request_id=UUID(request.state.request_id), next_cursor=next_cursor)


def _require_site_access(principal: Principal) -> None:
    if not is_allowed(principal.role, Action.VIEW_SITE):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot view observations.")


@router.get("/sites/{site_id}/observations", response_model=ObservationListResponse)
async def list_site_observations(
    site_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    status: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> ObservationListResponse:
    _require_site_access(principal)
    scope = cursor_scope({"organisation_id": str(principal.organisation_id), "site_id": str(site_id), "status": status})
    try:
        position = decode_cursor(cursor, scope=scope) if cursor else None
    except CursorError as error:
        raise ApiError(400, "invalid_cursor", "Invalid pagination cursor", "The cursor is invalid or does not belong to this observation query.") from error
    visibility, visibility_params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(
            f"""SELECT {_OBSERVATION_COLUMNS}
            FROM observations o JOIN sites s ON s.id=o.site_id
            WHERE o.site_id=%s AND s.status='active' AND ({visibility})
              AND (%s::text IS NULL OR o.status=%s)
              AND (%s::timestamptz IS NULL OR (o.observed_at,o.id)<(%s,%s::uuid))
            ORDER BY o.observed_at DESC,o.id DESC LIMIT %s""",
            (site_id, *visibility_params, status, status,
             position.created_at if position else None, position.created_at if position else None,
             position.resource_id if position else None, limit + 1),
        )).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = encode_cursor(CursorPosition(page[-1]["observed_at"], page[-1]["id"]), scope=scope) if has_more else None
    return ObservationListResponse(data=[ObservationData.model_validate(row) for row in page], meta=_meta(request, next_cursor=next_cursor))


@router.get("/observations/{observation_id}", response_model=ObservationResponse)
async def get_observation(
    observation_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> ObservationResponse:
    _require_site_access(principal)
    visibility, visibility_params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        row = await (await connection.execute(
            f"""SELECT {_OBSERVATION_COLUMNS} FROM observations o JOIN sites s ON s.id=o.site_id
            WHERE o.id=%s AND s.status='active' AND ({visibility})""",
            (observation_id, *visibility_params),
        )).fetchone()
    if not row:
        raise ApiError(404, "observation_not_found", "Observation not found", "The observation does not exist or is unavailable.")
    return ObservationResponse(data=ObservationData.model_validate(row), meta=_meta(request))
