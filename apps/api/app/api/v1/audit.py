from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ...db import tenant_connection
from ...schemas.audit import (
    AuditActorData,
    AuditEventData,
    AuditEventListData,
    AuditEventListMeta,
    AuditEventListResponse,
)
from ...security.audit import record_audit
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

router = APIRouter(tags=["administration"])

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "credential",
    "authorization",
    "cookie",
    "geometry",
    "coordinates",
)


def _require_audit_access(principal: Principal) -> None:
    if not is_allowed(principal.role, Action.MANAGE_ORGANISATION):
        raise ApiError(
            403,
            "permission_denied",
            "Permission denied",
            "Your role cannot access the organisation audit log.",
        )


def _normalise_time(value: datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ApiError(
            422,
            "invalid_time_range",
            "Invalid time range",
            f"{field} must include a timezone offset.",
        )
    return value.astimezone(UTC)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(fragment in str(key).lower() for fragment in _SENSITIVE_KEY_FRAGMENTS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _event_data(row: dict[str, Any]) -> AuditEventData:
    actor = None
    if row["actor_id"] is not None:
        actor = AuditActorData(id=row["actor_id"], display_name=row["actor_display_name"])
    return AuditEventData(
        id=row["id"],
        organisation_id=row["organisation_id"],
        actor=actor,
        action=row["action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        before_summary=_redact(row["before_summary"]),
        after_summary=_redact(row["after_summary"]),
        reason=row["reason"],
        correlation_id=row["correlation_id"],
        ip_address=row["ip_address"],
        created_at=row["created_at"],
    )


@router.get("/admin/audit-events", response_model=AuditEventListResponse)
async def list_audit_events(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    actor_id: Annotated[UUID | None, Query()] = None,
    action_prefix: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    target_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    target_id: Annotated[UUID | None, Query()] = None,
    correlation_id: Annotated[UUID | None, Query()] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> AuditEventListResponse:
    _require_audit_access(principal)
    created_after = _normalise_time(created_after, field="created_after")
    created_before = _normalise_time(created_before, field="created_before")
    if created_after and created_before and created_after >= created_before:
        raise ApiError(
            422,
            "invalid_time_range",
            "Invalid time range",
            "created_after must be earlier than created_before.",
        )
    normalised_action = action_prefix.strip() if action_prefix else None
    normalised_target_type = target_type.strip() if target_type else None
    scope_values = {
        "organisation_id": str(principal.organisation_id),
        "actor_id": str(actor_id) if actor_id else None,
        "action_prefix": normalised_action,
        "target_type": normalised_target_type,
        "target_id": str(target_id) if target_id else None,
        "correlation_id": str(correlation_id) if correlation_id else None,
        "created_after": created_after.isoformat() if created_after else None,
        "created_before": created_before.isoformat() if created_before else None,
    }
    scope = cursor_scope(scope_values)
    position = None
    if cursor:
        try:
            position = decode_cursor(cursor, scope=scope)
        except CursorError as error:
            raise ApiError(
                400,
                "invalid_cursor",
                "Invalid pagination cursor",
                "The cursor is invalid or does not belong to this query.",
            ) from error
    escaped_action = None
    if normalised_action:
        escaped_action = (
            normalised_action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
    cursor_time = position.created_at if position else None
    cursor_id = position.resource_id if position else None
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (
            await connection.execute(
                """
                SELECT e.id,e.organisation_id,e.actor_id,u.display_name actor_display_name,
                  e.action,e.target_type,e.target_id,e.before_summary,e.after_summary,
                  e.reason,e.correlation_id,e.ip_address::text ip_address,e.created_at
                FROM audit_events e
                LEFT JOIN user_profiles u ON u.id=e.actor_id
                WHERE (%s::uuid IS NULL OR e.actor_id=%s)
                  AND (%s::text IS NULL OR e.action LIKE %s ESCAPE '\\')
                  AND (%s::text IS NULL OR e.target_type=%s)
                  AND (%s::uuid IS NULL OR e.target_id=%s)
                  AND (%s::uuid IS NULL OR e.correlation_id=%s)
                  AND (%s::timestamptz IS NULL OR e.created_at>=%s)
                  AND (%s::timestamptz IS NULL OR e.created_at<%s)
                  AND (
                    %s::timestamptz IS NULL
                    OR (e.created_at,e.id)<(%s,%s::uuid)
                  )
                ORDER BY e.created_at DESC,e.id DESC
                LIMIT %s
                """,
                (
                    actor_id,
                    actor_id,
                    escaped_action,
                    escaped_action,
                    normalised_target_type,
                    normalised_target_type,
                    target_id,
                    target_id,
                    correlation_id,
                    correlation_id,
                    created_after,
                    created_after,
                    created_before,
                    created_before,
                    cursor_time,
                    cursor_time,
                    cursor_id,
                    limit + 1,
                ),
            )
        ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor(
                CursorPosition(created_at=last["created_at"], resource_id=last["id"]),
                scope=scope,
            )
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="administration.audit_events_viewed",
            target_type="audit_event",
            after={
                "filters": {key: value for key, value in scope_values.items() if value is not None},
                "result_count": len(page),
            },
        )
    return AuditEventListResponse(
        data=AuditEventListData(items=[_event_data(row) for row in page]),
        meta=AuditEventListMeta(
            request_id=UUID(request.state.request_id),
            next_cursor=next_cursor,
            has_more=has_more,
        ),
    )
