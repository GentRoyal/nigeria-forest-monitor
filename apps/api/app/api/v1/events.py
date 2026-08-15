from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.sites import (
    ChangeEventData,
    ChangeEventListMeta,
    ChangeEventListResponse,
    ChangeEventResponse,
    EventTransitionRequest,
    ReviewCreateRequest,
    ReviewData,
    ReviewListResponse,
    ReviewResponse,
)
from ...security.audit import record_audit
from ...security.cursors import (
    CursorError,
    CursorPosition,
    cursor_scope,
    decode_cursor,
    encode_cursor,
)
from ...security.permissions import Action, Role, is_allowed
from ..dependencies import Principal, current_principal
from ..errors import ApiError
from .sites import _visibility_sql

router = APIRouter(tags=["events"])

_EVENT_COLUMNS = """
  e.id,e.site_id,e.observation_id,e.processing_run_id,e.category,
  ST_AsGeoJSON(e.geometry,9,0)::jsonb geometry,e.affected_area_sq_m,e.signal_strength,
  e.review_status,e.sensitivity,e.resolution,e.resolved_at,e.created_at,e.updated_at
"""
_REVIEW_COLUMNS = """
  id,event_id,review_type,decision,rationale,confidence_statement,actor_id,
  supporting_evidence supporting_evidence_ids,supersedes_review_id,submitted_at
"""


def _meta(request: Request, *, next_cursor: str | None = None) -> ChangeEventListMeta:
    return ChangeEventListMeta(request_id=UUID(request.state.request_id), next_cursor=next_cursor)


def _event_visibility(principal: Principal) -> tuple[str, list[object]]:
    if principal.role == Role.VERIFICATION_OFFICER:
        return (
            """EXISTS (
              SELECT 1 FROM event_assignments ea
              WHERE ea.event_id=e.id AND ea.assignee_id=%s
                AND ea.assignment_type='institutional_verification'
                AND ea.status IN ('pending','accepted')
            )""",
            [principal.user_id],
        )
    return _visibility_sql(principal, "s")


async def _find_event(connection, principal: Principal, event_id: UUID, *, lock: bool = False):
    visibility, params = _event_visibility(principal)
    return await (await connection.execute(
        f"""SELECT {_EVENT_COLUMNS} FROM change_events e JOIN sites s ON s.id=e.site_id
        WHERE e.id=%s AND s.status='active' AND ({visibility}) {'FOR UPDATE' if lock else ''}""",
        (event_id, *params),
    )).fetchone()


@router.get("/events", response_model=ChangeEventListResponse)
async def list_events(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    site_id: Annotated[UUID | None, Query()] = None,
    review_status: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> ChangeEventListResponse:
    visibility, visibility_params = _event_visibility(principal)
    scope = cursor_scope({"organisation_id": str(principal.organisation_id), "site_id": str(site_id) if site_id else None, "review_status": review_status})
    try:
        position = decode_cursor(cursor, scope=scope) if cursor else None
    except CursorError as error:
        raise ApiError(400, "invalid_cursor", "Invalid pagination cursor", "The cursor is invalid or does not belong to this event query.") from error
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(
            f"""SELECT {_EVENT_COLUMNS} FROM change_events e JOIN sites s ON s.id=e.site_id
            WHERE s.status='active' AND ({visibility})
              AND (%s::uuid IS NULL OR e.site_id=%s)
              AND (%s::text IS NULL OR e.review_status=%s)
              AND (%s::timestamptz IS NULL OR (e.created_at,e.id)<(%s,%s::uuid))
            ORDER BY e.created_at DESC,e.id DESC LIMIT %s""",
            (*visibility_params, site_id, site_id, review_status, review_status,
             position.created_at if position else None, position.created_at if position else None,
             position.resource_id if position else None, limit + 1),
        )).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = encode_cursor(CursorPosition(page[-1]["created_at"], page[-1]["id"]), scope=scope) if has_more else None
    return ChangeEventListResponse(data=[ChangeEventData.model_validate(row) for row in page], meta=_meta(request, next_cursor=next_cursor))


@router.get("/events/{event_id}", response_model=ChangeEventResponse)
async def get_event(event_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ChangeEventResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        event = await _find_event(connection, principal, event_id)
    if not event:
        raise ApiError(404, "event_not_found", "Event not found", "The event does not exist or is unavailable.")
    return ChangeEventResponse(data=ChangeEventData.model_validate(event), meta=_meta(request))


@router.get("/events/{event_id}/reviews", response_model=ReviewListResponse)
async def list_reviews(event_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> ReviewListResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        if not await _find_event(connection, principal, event_id):
            raise ApiError(404, "event_not_found", "Event not found", "The event does not exist or is unavailable.")
        rows = await (await connection.execute(
            f"SELECT {_REVIEW_COLUMNS} FROM reviews WHERE event_id=%s ORDER BY submitted_at,id", (event_id,)
        )).fetchall()
    return ReviewListResponse(data=[ReviewData.model_validate(row) for row in rows], meta=_meta(request))


@router.post("/events/{event_id}/reviews", response_model=ReviewResponse, status_code=201)
async def create_review(
    event_id: UUID, payload: ReviewCreateRequest, request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ReviewResponse:
    if payload.review_type == "remote_analysis" and not is_allowed(principal.role, Action.REMOTE_REVIEW):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot submit a remote review.")
    if payload.review_type == "institutional_verification" and not is_allowed(principal.role, Action.INSTITUTIONAL_VERIFY):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot submit institutional verification.")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        event = await _find_event(connection, principal, event_id, lock=True)
        if not event:
            raise ApiError(404, "event_not_found", "Event not found", "The event does not exist or is unavailable.")
        if payload.review_type == "institutional_verification":
            assignment = await (await connection.execute(
                """SELECT id FROM event_assignments WHERE event_id=%s AND assignee_id=%s
                   AND assignment_type='institutional_verification' AND status='accepted'""",
                (event_id, principal.user_id),
            )).fetchone()
            if not assignment:
                raise ApiError(403, "assignment_required", "Accepted assignment required", "Institutional verification requires an accepted assignment.")
        evidence_ids = payload.supporting_evidence_ids
        if evidence_ids:
            count = await (await connection.execute(
                "SELECT count(*) AS total FROM event_evidence WHERE event_id=%s AND id = ANY(%s::uuid[])",
                (event_id, evidence_ids),
            )).fetchone()
            if count["total"] != len(set(evidence_ids)):
                raise ApiError(422, "invalid_evidence", "Invalid supporting evidence", "Every supporting evidence item must belong to this event.")
        review = await (await connection.execute(
            f"""INSERT INTO reviews(organisation_id,event_id,review_type,decision,rationale,confidence_statement,actor_id,supporting_evidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_REVIEW_COLUMNS}""",
            (principal.organisation_id, event_id, payload.review_type, payload.decision, payload.rationale,
             payload.confidence_statement, principal.user_id, Jsonb([str(value) for value in evidence_ids])),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id,
            action="change_event.review_submitted", target_type="change_event", target_id=event_id,
            after={"review_id": str(review["id"]), "review_type": payload.review_type, "decision": payload.decision})
    return ReviewResponse(data=ReviewData.model_validate(review), meta=_meta(request))


@router.post("/events/{event_id}/transitions", response_model=ChangeEventResponse)
async def transition_event(
    event_id: UUID, payload: EventTransitionRequest, request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ChangeEventResponse:
    remote_statuses = {"under_remote_review", "awaiting_more_observations", "remotely_corroborated", "inconclusive", "dismissed"}
    if payload.to_status in remote_statuses and not is_allowed(principal.role, Action.REMOTE_REVIEW):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot make a remote review decision.")
    if payload.to_status == "referred_to_authority" and principal.role not in {Role.OWNER, Role.ADMINISTRATOR, Role.ANALYST}:
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot refer an event.")
    if payload.to_status in {"institutionally_verified", "resolved"} and not is_allowed(principal.role, Action.INSTITUTIONAL_VERIFY):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot make this verification decision.")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        event = await _find_event(connection, principal, event_id, lock=True)
        if not event:
            raise ApiError(404, "event_not_found", "Event not found", "The event does not exist or is unavailable.")
        if event["review_status"] == "resolved":
            raise ApiError(409, "event_already_resolved", "Event already resolved", "Resolved events cannot be transitioned.")
        if payload.to_status != "under_remote_review" and payload.review_id is None:
            raise ApiError(422, "review_required", "Review required", "This transition must reference an immutable submitted review.")
        if payload.review_id:
            review = await (await connection.execute(
                "SELECT review_type,actor_id FROM reviews WHERE id=%s AND event_id=%s", (payload.review_id, event_id)
            )).fetchone()
            if not review or review["actor_id"] != principal.user_id:
                raise ApiError(422, "invalid_review", "Invalid review", "The referenced review must belong to this event and actor.")
            required_type = "institutional_verification" if payload.to_status in {"institutionally_verified", "resolved"} else "remote_analysis"
            if review["review_type"] != required_type:
                raise ApiError(422, "invalid_review", "Invalid review", "The review type does not support this transition.")
        updated = await (await connection.execute(
            f"""UPDATE change_events SET review_status=%s,resolution=%s,
            resolved_at=CASE WHEN %s='resolved' THEN now() ELSE resolved_at END,updated_at=now()
            WHERE id=%s RETURNING {_EVENT_COLUMNS}""",
            (payload.to_status, payload.reason, payload.to_status, event_id),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id,
            action="change_event.transitioned", target_type="change_event", target_id=event_id,
            before={"review_status": event["review_status"]},
            after={"review_status": payload.to_status, "review_id": str(payload.review_id) if payload.review_id else None},
            reason=payload.reason)
    return ChangeEventResponse(data=ChangeEventData.model_validate(updated), meta=_meta(request))
