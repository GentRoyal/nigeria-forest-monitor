from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.sites import (
    NotificationData,
    NotificationListResponse,
    NotificationPreferencesData,
    NotificationPreferencesRequest,
    NotificationPreferencesResponse,
    NotificationResponse,
)
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["notifications"])


def _meta(request: Request) -> dict[str, UUID]:
    return {"request_id": UUID(request.state.request_id)}


_COLUMNS = "id,event_id,notification_type,safe_summary,sensitivity,protected_path,read_at,created_at"


@router.get("/me/notification-preferences", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> NotificationPreferencesResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        row = await (await connection.execute("SELECT notification_preferences FROM user_profiles WHERE id=%s", (principal.user_id,))).fetchone()
    return NotificationPreferencesResponse(data=NotificationPreferencesData.model_validate(row["notification_preferences"]), meta=_meta(request))


@router.put("/me/notification-preferences", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(payload: NotificationPreferencesRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> NotificationPreferencesResponse:
    preferences = {"channels": payload.channels, "digest_enabled": payload.digest_enabled}
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        await connection.execute("UPDATE user_profiles SET notification_preferences=%s WHERE id=%s", (Jsonb(preferences), principal.user_id))
    return NotificationPreferencesResponse(data=NotificationPreferencesData.model_validate(preferences), meta=_meta(request))


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NotificationListResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(
            f"SELECT {_COLUMNS} FROM notifications WHERE recipient_id=%s AND (%s::boolean=false OR read_at IS NULL) ORDER BY created_at DESC,id DESC LIMIT %s",
            (principal.user_id, unread_only, limit),
        )).fetchall()
    return NotificationListResponse(data=[NotificationData.model_validate(row) for row in rows], meta=_meta(request))


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(notification_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> NotificationResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        notification = await (await connection.execute(
            f"UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE id=%s AND recipient_id=%s RETURNING {_COLUMNS}",
            (notification_id, principal.user_id),
        )).fetchone()
    if not notification:
        raise ApiError(404, "notification_not_found", "Notification not found", "The notification does not exist or is unavailable.")
    return NotificationResponse(data=NotificationData.model_validate(notification), meta=_meta(request))
