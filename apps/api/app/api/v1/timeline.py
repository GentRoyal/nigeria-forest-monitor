from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ...db import tenant_connection
from ...schemas.timeline import TimelineEntryData, TimelineResponse
from ...security.permissions import Action, is_allowed
from ..dependencies import Principal, current_principal
from ..errors import ApiError
from .sites import _visibility_sql

router = APIRouter(tags=["sites", "timeline"])


@router.get("/sites/{site_id}/timeline", response_model=TimelineResponse)
async def site_timeline(
    site_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> TimelineResponse:
    if not is_allowed(principal.role, Action.VIEW_SITE):
        raise ApiError(403, "permission_denied", "Permission denied", "Your role cannot view this site timeline.")
    visibility, params = _visibility_sql(principal, "s")
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await (await connection.execute(f"SELECT s.id FROM sites s WHERE s.id=%s AND s.status<>'deleted' AND ({visibility})", (site_id, *params))).fetchone()
        if not site:
            raise ApiError(404, "site_not_found", "Site not found", "The site does not exist or is unavailable.")
        rows = await (await connection.execute(
            """SELECT * FROM (
              SELECT o.id,'observation' entry_type,o.observed_at occurred_at,
                'Observation ' || o.status summary,
                jsonb_build_object('status',o.status,'eligibility',o.eligibility,'coverage_ratio',o.coverage_ratio,'catalogue_item_id',o.catalogue_item_id::text) payload
              FROM observations o WHERE o.site_id=%s
              UNION ALL
              SELECT j.id,'job',j.updated_at,'Job ' || j.status,
                jsonb_build_object('job_type',j.job_type,'trigger_type',j.trigger_type,'status',j.status,'progress',j.progress,'observation_id',j.observation_id::text)
              FROM processing_jobs j WHERE j.site_id=%s
              UNION ALL
              SELECT e.id,'event',e.updated_at,'Possible change: ' || e.category,
                jsonb_build_object('category',e.category,'review_status',e.review_status,'signal_strength',e.signal_strength,'affected_area_sq_m',e.affected_area_sq_m,'sensitivity',e.sensitivity)
              FROM change_events e WHERE e.site_id=%s
              UNION ALL
              SELECT r.id,'review',r.submitted_at,'Review: ' || r.decision,
                jsonb_build_object('event_id',r.event_id::text,'review_type',r.review_type,'decision',r.decision,'actor_id',r.actor_id::text)
              FROM reviews r JOIN change_events e ON e.id=r.event_id WHERE e.site_id=%s
              UNION ALL
              SELECT a.id,'assignment',COALESCE(a.completed_at,a.accepted_at,a.created_at),'Assignment ' || a.status,
                jsonb_build_object('event_id',a.event_id::text,'assignment_type',a.assignment_type,'status',a.status,'assignee_id',a.assignee_id::text,'due_at',a.due_at)
              FROM event_assignments a JOIN change_events e ON e.id=a.event_id WHERE e.site_id=%s
              UNION ALL
              SELECT ae.id,'audit',ae.created_at,ae.action,
                jsonb_build_object('target_type',ae.target_type,'target_id',ae.target_id::text,'actor_id',ae.actor_id::text,'after_summary',ae.after_summary)
              FROM audit_events ae
              LEFT JOIN processing_jobs j ON ae.target_type='processing_job' AND ae.target_id=j.id
              LEFT JOIN change_events e ON ae.target_type='change_event' AND ae.target_id=e.id
              WHERE (ae.target_type='site' AND ae.target_id=%s) OR j.site_id=%s OR e.site_id=%s
            ) timeline ORDER BY occurred_at DESC,id DESC LIMIT %s""",
            (site_id, site_id, site_id, site_id, site_id, site_id, site_id, site_id, limit),
        )).fetchall()
    return TimelineResponse(data=[TimelineEntryData.model_validate(row) for row in rows], meta={"request_id": UUID(request.state.request_id)})
