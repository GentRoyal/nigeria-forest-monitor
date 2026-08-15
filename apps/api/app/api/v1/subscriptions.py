from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.sites import (
    SubscriptionCreateRequest,
    SubscriptionData,
    SubscriptionListResponse,
    SubscriptionResponse,
)
from ...security.permissions import Action, is_allowed
from ..dependencies import Principal, current_principal
from ..errors import ApiError
from .sites import _visibility_sql

router = APIRouter(tags=["subscriptions"])


def _meta(request: Request) -> dict[str, UUID]:
    return {"request_id": UUID(request.state.request_id)}


@router.get("/subscriptions", response_model=SubscriptionListResponse)
async def list_subscriptions(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> SubscriptionListResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(
            "SELECT id,site_id,event_id,channels,digest_enabled,created_at FROM subscriptions WHERE user_id=%s ORDER BY created_at DESC", (principal.user_id,)
        )).fetchall()
    return SubscriptionListResponse(data=[SubscriptionData.model_validate(row) for row in rows], meta=_meta(request))


@router.post("/subscriptions", response_model=SubscriptionResponse, status_code=201)
async def create_subscription(payload: SubscriptionCreateRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> SubscriptionResponse:
    if not is_allowed(principal.role, Action.VIEW_SITE):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot create subscriptions.")
    visibility, params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        if payload.site_id:
            allowed = await (await connection.execute(f"SELECT s.id FROM sites s WHERE s.id=%s AND ({visibility})", (payload.site_id, *params))).fetchone()
        else:
            allowed = await (await connection.execute(f"SELECT e.id FROM change_events e JOIN sites s ON s.id=e.site_id WHERE e.id=%s AND ({visibility})", (payload.event_id, *params))).fetchone()
        if not allowed:
            raise ApiError(404, "subscription_target_not_found", "Subscription target not found", "The target does not exist or is unavailable.")
        try:
            subscription = await (await connection.execute(
                """INSERT INTO subscriptions(organisation_id,user_id,site_id,event_id,channels,digest_enabled)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,site_id,event_id,channels,digest_enabled,created_at""",
                (principal.organisation_id, principal.user_id, payload.site_id, payload.event_id, Jsonb(payload.channels), payload.digest_enabled),
            )).fetchone()
        except UniqueViolation as error:
            raise ApiError(409, "subscription_already_exists", "Subscription already exists", "You already subscribe to this target.") from error
    return SubscriptionResponse(data=SubscriptionData.model_validate(subscription), meta=_meta(request))


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def delete_subscription(subscription_id: UUID, principal: Annotated[Principal, Depends(current_principal)]) -> None:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        deleted = await (await connection.execute("DELETE FROM subscriptions WHERE id=%s AND user_id=%s RETURNING id", (subscription_id, principal.user_id))).fetchone()
    if not deleted:
        raise ApiError(404, "subscription_not_found", "Subscription not found", "The subscription does not exist or is unavailable.")
