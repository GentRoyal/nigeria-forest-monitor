import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from ...db import tenant_connection
from ...schemas.sites import (
    JobCancelRequest,
    JobData,
    JobListMeta,
    JobListResponse,
    JobResponse,
    JobRetryRequest,
)
from ...security.audit import record_audit
from ...security.cursors import (
    CursorError,
    CursorPosition,
    cursor_scope,
    decode_cursor,
    encode_cursor,
)
from ...security.permissions import Role
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["jobs"])


def _require_job_observability(principal: Principal) -> None:
    if principal.role not in {Role.OWNER, Role.ADMINISTRATOR}:
        raise ApiError(
            403, "permission_denied", "Permission denied", "Your role cannot view operational jobs."
        )


def _meta(request: Request, *, next_cursor: str | None = None) -> JobListMeta:
    return JobListMeta(request_id=UUID(request.state.request_id), next_cursor=next_cursor)


_JOB_COLUMNS = """
  id,site_id,observation_id,grid_version_id,retry_of_job_id,job_type,trigger_type,priority,
  status,progress,created_at,updated_at
"""


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    site_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
    job_type: Annotated[
        Literal["discovery", "processing", "reprocessing", "export"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> JobListResponse:
    _require_job_observability(principal)
    scope = cursor_scope(
        {
            "organisation_id": str(principal.organisation_id),
            "site_id": str(site_id) if site_id else None,
            "status": status,
            "job_type": job_type,
        }
    )
    position = None
    if cursor:
        try:
            position = decode_cursor(cursor, scope=scope)
        except CursorError as error:
            raise ApiError(
                400,
                "invalid_cursor",
                "Invalid pagination cursor",
                "The cursor is invalid or does not belong to this job query.",
            ) from error
    cursor_time = position.created_at if position else None
    cursor_id = position.resource_id if position else None
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (
            await connection.execute(
                f"""SELECT {_JOB_COLUMNS} FROM processing_jobs
            WHERE (%s::uuid IS NULL OR site_id=%s)
              AND (%s::text IS NULL OR status=%s)
              AND (%s::text IS NULL OR job_type=%s)
              AND (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s::uuid))
            ORDER BY created_at DESC,id DESC LIMIT %s""",
                (
                    site_id,
                    site_id,
                    status,
                    status,
                    job_type,
                    job_type,
                    cursor_time,
                    cursor_time,
                    cursor_id,
                    limit + 1,
                ),
            )
        ).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = (
        encode_cursor(CursorPosition(page[-1]["created_at"], page[-1]["id"]), scope=scope)
        if has_more
        else None
    )
    return JobListResponse(
        data=[JobData.model_validate(row) for row in page],
        meta=_meta(request, next_cursor=next_cursor),
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> JobResponse:
    _require_job_observability(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        job = await (
            await connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE id=%s", (job_id,)
            )
        ).fetchone()
    if not job:
        raise ApiError(
            404, "job_not_found", "Job not found", "The job does not exist or is unavailable."
        )
    return JobResponse(data=JobData.model_validate(job), meta=_meta(request))


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: UUID,
    payload: JobCancelRequest,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> JobResponse:
    _require_job_observability(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        current = await (
            await connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,)
            )
        ).fetchone()
        if not current:
            raise ApiError(
                404, "job_not_found", "Job not found", "The job does not exist or is unavailable."
            )
        if current["status"] not in {"queued", "orchestrating", "retry_wait"}:
            raise ApiError(
                409,
                "job_not_cancellable",
                "Job cannot be cancelled",
                "Only queued, orchestrating, or retry-wait jobs can be cancelled.",
            )
        job = await (
            await connection.execute(
                f"""UPDATE processing_jobs
                SET status='cancelled',cancellation_reason=%s,updated_at=now()
                WHERE id=%s RETURNING {_JOB_COLUMNS}""",
                (payload.reason, job_id),
            )
        ).fetchone()
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="processing_job.cancelled",
            target_type="processing_job",
            target_id=job_id,
            before={"status": current["status"]},
            after={"status": "cancelled"},
            reason=payload.reason,
        )
    return JobResponse(data=JobData.model_validate(job), meta=_meta(request))


@router.post("/jobs/{job_id}/retry", response_model=JobResponse, status_code=201)
async def retry_job(
    job_id: UUID,
    payload: JobRetryRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> JobResponse:
    _require_job_observability(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        original = await (
            await connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,)
            )
        ).fetchone()
        if not original:
            raise ApiError(
                404, "job_not_found", "Job not found", "The job does not exist or is unavailable."
            )
        if original["status"] not in {"failed", "cancelled"}:
            raise ApiError(
                409,
                "job_not_retryable",
                "Job cannot be retried",
                "Only failed or cancelled jobs can be retried.",
            )
        retry_key = hashlib.sha256(
            json.dumps(
                {"retry_of": str(job_id), "idempotency_key": idempotency_key}, sort_keys=True
            ).encode()
        ).hexdigest()
        existing = await (
            await connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE idempotency_key=%s", (retry_key,)
            )
        ).fetchone()
        if existing:
            response.status_code = 200
            return JobResponse(data=JobData.model_validate(existing), meta=_meta(request))
        job = await (
            await connection.execute(
                f"""INSERT INTO processing_jobs(organisation_id,site_id,observation_id,grid_version_id,retry_of_job_id,job_type,trigger_type,priority,idempotency_key,requested_configuration,requested_by) SELECT organisation_id,site_id,observation_id,grid_version_id,id,job_type,'retry',%s,%s,jsonb_set(requested_configuration,'{{retry_of_job_id}}',to_jsonb(id::text),true),%s FROM processing_jobs WHERE id=%s RETURNING {_JOB_COLUMNS}""",
                (payload.priority or original["priority"], retry_key, principal.user_id, job_id),
            )
        ).fetchone()
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="processing_job.retried",
            target_type="processing_job",
            target_id=job["id"],
            after={"retry_of_job_id": str(job_id), "priority": job["priority"]},
        )
    return JobResponse(data=JobData.model_validate(job), meta=_meta(request))


@router.get("/admin/jobs/failed", response_model=JobListResponse)
async def list_failed_jobs(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> JobListResponse:
    _require_job_observability(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (await connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE status IN ('failed','retry_wait') ORDER BY updated_at DESC,id DESC LIMIT %s",
            (limit,),
        )).fetchall()
    return JobListResponse(data=[JobData.model_validate(row) for row in rows], meta=_meta(request))


@router.post("/admin/jobs/{job_id}/reprocess", response_model=JobResponse, status_code=201)
async def reprocess_job(
    job_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> JobResponse:
    _require_job_observability(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        original = await (await connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,)
        )).fetchone()
        if not original:
            raise ApiError(404, "job_not_found", "Job not found", "The job does not exist or is unavailable.")
        key = hashlib.sha256(f"reprocess:{job_id}:{idempotency_key}".encode()).hexdigest()
        existing = await (await connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM processing_jobs WHERE idempotency_key=%s", (key,)
        )).fetchone()
        if existing:
            response.status_code = 200
            return JobResponse(data=JobData.model_validate(existing), meta=_meta(request))
        job = await (await connection.execute(
            f"""INSERT INTO processing_jobs(organisation_id,site_id,observation_id,grid_version_id,retry_of_job_id,job_type,trigger_type,priority,idempotency_key,requested_configuration,requested_by)
            SELECT organisation_id,site_id,observation_id,grid_version_id,id,'reprocessing','manual',priority,%s,
              jsonb_set(requested_configuration,'{{reprocess_of_job_id}}',to_jsonb(id::text),true),%s
            FROM processing_jobs WHERE id=%s RETURNING {_JOB_COLUMNS}""",
            (key, principal.user_id, job_id),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=principal.user_id,
            action="processing_job.reprocessing_requested", target_type="processing_job", target_id=job["id"],
            after={"reprocess_of_job_id": str(job_id)})
    return JobResponse(data=JobData.model_validate(job), meta=_meta(request))
