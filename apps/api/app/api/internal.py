import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from psycopg.types.json import Jsonb

from ..db import tenant_connection
from ..schemas.workers import (
    WorkerAssetCreateRequest,
    WorkerAssetData,
    WorkerAssetResponse,
    WorkerCatalogueData,
    WorkerCatalogueResponse,
    WorkerCatalogueUpsertRequest,
    WorkerChangeEventCreateRequest,
    WorkerChangeEventData,
    WorkerChangeEventResponse,
    WorkerClaimData,
    WorkerClaimRequest,
    WorkerClaimResponse,
    WorkerCompleteData,
    WorkerCompleteRequest,
    WorkerCompleteResponse,
    WorkerDiscoveryCursorRequest,
    WorkerFailureRequest,
    WorkerFailureResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerJobData,
    WorkerObservationData,
    WorkerObservationResponse,
    WorkerObservationUpsertRequest,
    WorkerStageData,
    WorkerStageRequest,
    WorkerStageResponse,
)
from ..security.audit import record_audit
from ..security.notifications import notify_event_subscribers
from ..settings import get_settings
from .errors import ApiError

router = APIRouter(prefix="/internal/v1", tags=["internal-worker"])

_JOB_COLUMNS = """
  id,site_id,observation_id,grid_version_id,job_type,trigger_type,priority,status,
  progress,attempt_count,current_stage,lease_expires_at
"""


@dataclass(frozen=True)
class WorkerPrincipal:
    organisation_id: UUID


async def current_worker(
    x_worker_token: Annotated[str | None, Header(alias="X-Worker-Token")],
    x_organisation_id: Annotated[UUID, Header(alias="X-Organisation-ID")],
) -> WorkerPrincipal:
    expected = get_settings().worker_service_token
    if not x_worker_token or not hmac.compare_digest(x_worker_token, expected):
        raise ApiError(401, "worker_authentication_required", "Worker authentication required", "A valid worker service credential is required.")
    return WorkerPrincipal(organisation_id=x_organisation_id)


async def current_scheduler(
    x_scheduler_token: Annotated[str | None, Header(alias="X-Scheduler-Token")],
    x_organisation_id: Annotated[UUID, Header(alias="X-Organisation-ID")],
) -> WorkerPrincipal:
    if not x_scheduler_token or not hmac.compare_digest(x_scheduler_token, get_settings().scheduler_service_token):
        raise ApiError(401, "scheduler_authentication_required", "Scheduler authentication required", "A valid scheduler service credential is required.")
    return WorkerPrincipal(organisation_id=x_organisation_id)


@router.post("/schedules/run-due")
async def create_due_discovery_jobs(principal: Annotated[WorkerPrincipal, Depends(current_scheduler)]) -> dict[str, int]:
    created = 0
    async with tenant_connection(principal.organisation_id) as connection:
        schedules = await (await connection.execute("SELECT id,site_id,scheduling_version,changed_by FROM monitoring_schedules WHERE status='active' AND next_due_at<=now() FOR UPDATE SKIP LOCKED")).fetchall()
        for schedule in schedules:
            key = hashlib.sha256(f"schedule:{schedule['id']}:{schedule['scheduling_version']}".encode()).hexdigest()
            job = await (await connection.execute("""INSERT INTO processing_jobs(organisation_id,site_id,grid_version_id,job_type,trigger_type,priority,idempotency_key,requested_configuration,requested_by)
            SELECT %s,s.id,s.current_grid_version_id,'discovery','scheduled',5,%s,jsonb_build_object('schedule_id',%s::text),%s FROM sites s WHERE s.id=%s
            ON CONFLICT (organisation_id,idempotency_key) DO NOTHING RETURNING id""", (principal.organisation_id,key,schedule['id'],schedule['changed_by'],schedule['site_id']))).fetchone()
            if job:
                created += 1
            await connection.execute("""UPDATE monitoring_schedules SET next_due_at=CASE cadence WHEN 'weekly' THEN next_due_at+interval '7 days' WHEN 'fortnightly' THEN next_due_at+interval '14 days' ELSE next_due_at+interval '1 month' END,updated_at=now() WHERE id=%s""", (schedule['id'],))
    return {"created": created}


def _lease_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _lease_seconds(value: int | None) -> int:
    return value if value is not None else get_settings().worker_lease_seconds


async def _lease_job(connection, principal: WorkerPrincipal, job_id: UUID, lease_token: str):
    job = await (await connection.execute(
        f"SELECT {_JOB_COLUMNS},worker_identity,lease_token_hash FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,)
    )).fetchone()
    if not job:
        raise ApiError(404, "job_not_found", "Job not found", "The processing job does not exist or is unavailable.")
    if not job["lease_token_hash"] or not hmac.compare_digest(job["lease_token_hash"], _lease_hash(lease_token)):
        raise ApiError(409, "invalid_job_lease", "Invalid job lease", "The worker does not hold the current job lease.")
    if job["lease_expires_at"] is None:
        raise ApiError(409, "invalid_job_lease", "Invalid job lease", "The job has no active lease.")
    return job


@router.post("/jobs/{job_id}/claim", response_model=WorkerClaimResponse)
async def claim_job(
    job_id: UUID,
    payload: WorkerClaimRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
) -> WorkerClaimResponse:
    lease_token = secrets.token_urlsafe(32)
    seconds = _lease_seconds(payload.lease_seconds)
    async with tenant_connection(principal.organisation_id) as connection:
        current = await (await connection.execute(
            "SELECT status,lease_expires_at FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,)
        )).fetchone()
        if not current:
            raise ApiError(404, "job_not_found", "Job not found", "The processing job does not exist or is unavailable.")
        if current["status"] not in {"queued", "retry_wait"}:
            raise ApiError(409, "job_not_claimable", "Job cannot be claimed", "Only queued or retry-wait jobs can be claimed.")
        job = await (await connection.execute(
            f"""UPDATE processing_jobs SET status='running',worker_identity=%s,
            lease_token_hash=%s,lease_expires_at=now() + (%s * interval '1 second'),
            heartbeat_at=now(),attempt_count=attempt_count+1,updated_at=now()
            WHERE id=%s RETURNING {_JOB_COLUMNS}""",
            (payload.worker_identity, _lease_hash(lease_token), seconds, job_id),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None,
            action="processing_job.claimed", target_type="processing_job", target_id=job_id,
            after={"worker_identity": payload.worker_identity, "attempt_count": job["attempt_count"]})
    return WorkerClaimResponse(data=WorkerClaimData(job=WorkerJobData.model_validate(job), lease_token=lease_token))


@router.post("/jobs/{job_id}/heartbeat", response_model=WorkerHeartbeatResponse)
async def heartbeat_job(
    job_id: UUID,
    payload: WorkerHeartbeatRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerHeartbeatResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    seconds = _lease_seconds(payload.lease_seconds)
    async with tenant_connection(principal.organisation_id) as connection:
        current = await _lease_job(connection, principal, job_id, x_job_lease_token)
        if current["status"] not in {"running", "publishing"}:
            raise ApiError(409, "job_not_active", "Job is not active", "Only active jobs can send a heartbeat.")
        if current["lease_expires_at"] <= await _database_now(connection):
            raise ApiError(409, "job_lease_expired", "Job lease expired", "The current job lease has expired.")
        job = await (await connection.execute(
            f"""UPDATE processing_jobs SET progress=GREATEST(progress,%s),current_stage=COALESCE(%s,current_stage),heartbeat_at=now(),
            lease_expires_at=now() + (%s * interval '1 second'),updated_at=now()
            WHERE id=%s RETURNING {_JOB_COLUMNS}""",
            (payload.progress, payload.stage, seconds, job_id),
        )).fetchone()
    return WorkerHeartbeatResponse(data=WorkerJobData.model_validate(job))


async def _database_now(connection):
    row = await (await connection.execute("SELECT now() AS current_time")).fetchone()
    return row["current_time"]


async def _active_lease_job(connection, principal: WorkerPrincipal, job_id: UUID, lease_token: str):
    current = await _lease_job(connection, principal, job_id, lease_token)
    if current["status"] not in {"running", "publishing"}:
        raise ApiError(409, "job_not_active", "Job is not active", "Only active jobs can send worker callbacks.")
    if current["lease_expires_at"] <= await _database_now(connection):
        raise ApiError(409, "job_lease_expired", "Job lease expired", "The current job lease has expired.")
    return current


@router.get("/jobs/{job_id}/input")
async def get_job_input(
    job_id: UUID,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> dict[str, object]:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        job = await _active_lease_job(connection, principal, job_id, x_job_lease_token)
        site = await (await connection.execute(
            """SELECT s.id,s.name,s.sensitivity,s.current_grid_version_id,
            ST_AsGeoJSON(b.geometry,9,0)::jsonb boundary,
            ms.id schedule_id,ms.sensor_settings,ms.quality_settings,ms.last_discovery_cursor
            FROM sites s JOIN site_boundary_versions b ON b.id=s.current_boundary_version_id
            LEFT JOIN monitoring_schedules ms ON ms.site_id=s.id AND ms.status='active'
            WHERE s.id=%s""", (job["site_id"],)
        )).fetchone()
        if not site:
            raise ApiError(422, "job_site_unavailable", "Job site unavailable", "The claimed job no longer has an active boundary.")
        config = await (await connection.execute("SELECT requested_configuration FROM processing_jobs WHERE id=%s", (job_id,))).fetchone()
    return {
        "job": {key: (str(value) if key.endswith("_id") and value is not None else value) for key, value in job.items()},
        "site": site,
        "requested_configuration": config["requested_configuration"],
    }


@router.post("/schedules/{schedule_id}/discovery-cursor")
async def update_discovery_cursor(
    schedule_id: UUID,
    payload: WorkerDiscoveryCursorRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> dict[str, str]:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        job = await _active_lease_job(connection, principal, payload.job_id, x_job_lease_token)
        schedule = await (await connection.execute("SELECT id FROM monitoring_schedules WHERE id=%s AND site_id=%s AND status='active'", (schedule_id, job["site_id"]))).fetchone()
        if not schedule:
            raise ApiError(422, "invalid_schedule", "Invalid schedule", "The schedule does not belong to the claimed job site.")
        await connection.execute("UPDATE monitoring_schedules SET last_discovery_cursor=%s,updated_at=now() WHERE id=%s", (payload.cursor, schedule_id))
    return {"cursor": payload.cursor}


@router.post("/jobs/{job_id}/stages", response_model=WorkerStageResponse, status_code=201)
async def record_job_stage(
    job_id: UUID,
    payload: WorkerStageRequest,
    response: Response,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerStageResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    callback_key = _lease_hash(idempotency_key)
    async with tenant_connection(principal.organisation_id) as connection:
        await _active_lease_job(connection, principal, job_id, x_job_lease_token)
        existing = await (await connection.execute(
            """SELECT id,processing_job_id,stage,details,created_at FROM processing_job_stage_callbacks
            WHERE processing_job_id=%s AND idempotency_key_hash=%s""", (job_id, callback_key)
        )).fetchone()
        if existing:
            response.status_code = 200
            return WorkerStageResponse(data=WorkerStageData.model_validate(existing))
        callback = await (await connection.execute(
            """INSERT INTO processing_job_stage_callbacks(organisation_id,processing_job_id,stage,idempotency_key_hash,details)
            VALUES (%s,%s,%s,%s,%s)
            RETURNING id,processing_job_id,stage,details,created_at""",
            (principal.organisation_id, job_id, payload.stage, callback_key, Jsonb(payload.details)),
        )).fetchone()
        await connection.execute(
            "UPDATE processing_jobs SET current_stage=%s,status=CASE WHEN %s='publishing' THEN 'publishing' ELSE status END,updated_at=now() WHERE id=%s",
            (payload.stage, payload.stage, job_id),
        )
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None,
            action="processing_job.stage_recorded", target_type="processing_job", target_id=job_id,
            after={"stage": payload.stage, "callback_id": str(callback["id"])})
    return WorkerStageResponse(data=WorkerStageData.model_validate(callback))


@router.post("/jobs/{job_id}/fail", response_model=WorkerFailureResponse)
async def fail_job(
    job_id: UUID,
    payload: WorkerFailureRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerFailureResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        current = await _active_lease_job(connection, principal, job_id, x_job_lease_token)
        job = await (await connection.execute(
            f"""UPDATE processing_jobs SET status=%s,failure_summary=%s,worker_identity=NULL,
            lease_token_hash=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=now()
            WHERE id=%s RETURNING {_JOB_COLUMNS}""",
            ("retry_wait" if payload.retryable else "failed", Jsonb({"category": payload.category, "message": payload.message, "retryable": payload.retryable}), job_id),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None,
            action="processing_job.failed", target_type="processing_job", target_id=job_id,
            before={"status": current["status"]},
            after={"status": job["status"], "category": payload.category, "retryable": payload.retryable})
    return WorkerFailureResponse(data=WorkerJobData.model_validate(job))


@router.post("/catalogue-items/upsert", response_model=WorkerCatalogueResponse)
async def upsert_catalogue_item(
    payload: WorkerCatalogueUpsertRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerCatalogueResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        await _active_lease_job(connection, principal, payload.job_id, x_job_lease_token)
        item = await (await connection.execute(
            """INSERT INTO catalogue_items(organisation_id,provider,collection,source_identifier,acquired_at,footprint,assets,licence,attribution,source_metadata)
            VALUES (%s,%s,%s,%s,%s,ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)),%s,%s,%s,%s)
            ON CONFLICT (organisation_id,provider,collection,source_identifier) DO UPDATE
            SET acquired_at=EXCLUDED.acquired_at,footprint=EXCLUDED.footprint,assets=EXCLUDED.assets,licence=EXCLUDED.licence,attribution=EXCLUDED.attribution,source_metadata=EXCLUDED.source_metadata
            RETURNING id,provider,collection,source_identifier,acquired_at,assets,licence,attribution,source_metadata,created_at""",
            (principal.organisation_id, payload.provider, payload.collection, payload.source_identifier, payload.acquired_at, payload.footprint.model_dump_json(), Jsonb(payload.assets), payload.licence, payload.attribution, Jsonb(payload.source_metadata)),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None, action="catalogue_item.upserted", target_type="catalogue_item", target_id=item["id"], after={"job_id": str(payload.job_id), "provider": item["provider"]})
    return WorkerCatalogueResponse(data=WorkerCatalogueData.model_validate(item))


@router.post("/observations/upsert", response_model=WorkerObservationResponse)
async def upsert_observation(
    payload: WorkerObservationUpsertRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerObservationResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        job = await _active_lease_job(connection, principal, payload.job_id, x_job_lease_token)
        if job["grid_version_id"] and job["grid_version_id"] != payload.grid_version_id:
            raise ApiError(422, "invalid_grid_version", "Invalid grid version", "The observation grid version must match the claimed job.")
        catalogue = await (await connection.execute("SELECT id FROM catalogue_items WHERE id=%s", (payload.catalogue_item_id,))).fetchone()
        grid = await (await connection.execute("SELECT id FROM grid_versions WHERE id=%s AND site_id=%s", (payload.grid_version_id, job["site_id"]))).fetchone()
        if not catalogue or not grid:
            raise ApiError(422, "invalid_observation_lineage", "Invalid observation lineage", "The catalogue item and grid version must belong to the claimed job context.")
        observation = await (await connection.execute(
            """INSERT INTO observations(organisation_id,site_id,catalogue_item_id,grid_version_id,baseline_observation_id,coverage_ratio,quality_assessment,eligibility,eligibility_reason,discovery_method,status,observed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (organisation_id,site_id,catalogue_item_id,grid_version_id) DO UPDATE
            SET baseline_observation_id=EXCLUDED.baseline_observation_id,coverage_ratio=EXCLUDED.coverage_ratio,quality_assessment=EXCLUDED.quality_assessment,eligibility=EXCLUDED.eligibility,eligibility_reason=EXCLUDED.eligibility_reason,status=EXCLUDED.status,observed_at=EXCLUDED.observed_at
            RETURNING id,site_id,catalogue_item_id,grid_version_id,baseline_observation_id,coverage_ratio,quality_assessment,eligibility,eligibility_reason,discovery_method,status,observed_at,created_at""",
            (principal.organisation_id, job["site_id"], payload.catalogue_item_id, payload.grid_version_id, payload.baseline_observation_id, payload.coverage_ratio, Jsonb(payload.quality_assessment), payload.eligibility, payload.eligibility_reason, payload.discovery_method, payload.status, payload.observed_at),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None, action="observation.upserted", target_type="observation", target_id=observation["id"], after={"job_id": str(payload.job_id), "status": payload.status})
    return WorkerObservationResponse(data=WorkerObservationData.model_validate(observation))


@router.post("/jobs/{job_id}/complete", response_model=WorkerCompleteResponse)
async def complete_job(
    job_id: UUID,
    payload: WorkerCompleteRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerCompleteResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        job = await _active_lease_job(connection, principal, job_id, x_job_lease_token)
        boundary = await (await connection.execute("SELECT id FROM site_boundary_versions WHERE id=%s AND site_id=%s", (payload.boundary_version_id, job["site_id"]))).fetchone()
        grid = await (await connection.execute("SELECT id FROM grid_versions WHERE id=%s AND site_id=%s", (payload.grid_version_id, job["site_id"]))).fetchone()
        observation = None
        if payload.observation_id:
            observation = await (await connection.execute("SELECT id FROM observations WHERE id=%s AND site_id=%s", (payload.observation_id, job["site_id"]))).fetchone()
        if not boundary or not grid or (payload.observation_id and not observation):
            raise ApiError(422, "invalid_completion_lineage", "Invalid completion lineage", "Completion inputs must belong to the claimed job site.")
        orchestration = await (await connection.execute(
            """INSERT INTO orchestration_runs(organisation_id,processing_job_id,orchestrator_run_identifier,dag_id,dag_version,current_stage,last_callback_at,output_summary,terminal_result)
            VALUES (%s,%s,%s,%s,%s,'completed',now(),%s,%s)
            ON CONFLICT (organisation_id,orchestrator_run_identifier) DO UPDATE SET last_callback_at=now()
            RETURNING id""",
            (principal.organisation_id, job_id, payload.orchestrator_run_identifier, payload.dag_id, payload.dag_version, Jsonb({"output_asset_count": len(payload.output_assets)}), Jsonb({"status": "completed"})),
        )).fetchone()
        processing_run = await (await connection.execute(
            """INSERT INTO processing_runs(organisation_id,orchestration_run_id,observation_id,boundary_version_id,grid_version_id,input_assets,parameters,code_version,model_version,environment,started_at,completed_at,output_assets,metrics,warnings,checksum)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)
            ON CONFLICT (organisation_id,orchestration_run_id) DO UPDATE SET completed_at=processing_runs.completed_at
            RETURNING id""",
            (principal.organisation_id, orchestration["id"], payload.observation_id, payload.boundary_version_id, payload.grid_version_id, Jsonb(payload.input_assets), Jsonb(payload.parameters), payload.code_version, payload.model_version, Jsonb(payload.environment), payload.started_at, Jsonb(payload.output_assets), Jsonb(payload.metrics), Jsonb(payload.warnings), payload.checksum),
        )).fetchone()
        completed = await (await connection.execute(
            f"""UPDATE processing_jobs SET status='completed',progress=100,current_stage='completed',worker_identity=NULL,lease_token_hash=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=now()
            WHERE id=%s RETURNING {_JOB_COLUMNS}""", (job_id,)
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None, action="processing_job.completed", target_type="processing_job", target_id=job_id, after={"orchestrator_run_identifier": payload.orchestrator_run_identifier})
    return WorkerCompleteResponse(data=WorkerCompleteData(job=WorkerJobData.model_validate(completed), processing_run_id=processing_run["id"]))


@router.post("/assets", response_model=WorkerAssetResponse, status_code=201)
async def register_asset(
    payload: WorkerAssetCreateRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerAssetResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        job = await _active_lease_job(connection, principal, payload.job_id, x_job_lease_token)
        run = await (await connection.execute(
            """SELECT pr.id FROM processing_runs pr JOIN orchestration_runs r ON r.id=pr.orchestration_run_id
            WHERE pr.id=%s AND r.processing_job_id=%s""", (payload.processing_run_id, payload.job_id)
        )).fetchone()
        if not run:
            raise ApiError(422, "invalid_processing_run", "Invalid processing run", "The processing run does not belong to the claimed job.")
        if payload.observation_id:
            observation = await (await connection.execute("SELECT id FROM observations WHERE id=%s AND site_id=%s", (payload.observation_id, job["site_id"]))).fetchone()
            if not observation:
                raise ApiError(422, "invalid_observation", "Invalid observation", "The observation does not belong to the claimed job site.")
        asset = await (await connection.execute(
            """INSERT INTO raster_assets(organisation_id,observation_id,processing_run_id,asset_type,object_key,source_href,cog_valid,bands,resolution_metres,checksum,size_bytes,processing_version,lineage)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id,observation_id,processing_run_id,asset_type,object_key,source_href,cog_valid,bands,resolution_metres,checksum,size_bytes,processing_version,lineage,created_at""",
            (principal.organisation_id, payload.observation_id, payload.processing_run_id, payload.asset_type, payload.object_key, payload.source_href, payload.cog_valid, Jsonb(payload.bands), payload.resolution_metres, payload.checksum, payload.size_bytes, payload.processing_version, Jsonb(payload.lineage)),
        )).fetchone()
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None, action="raster_asset.registered", target_type="raster_asset", target_id=asset["id"], after={"job_id": str(payload.job_id), "asset_type": payload.asset_type})
    return WorkerAssetResponse(data=WorkerAssetData.model_validate(asset))


@router.post("/events", response_model=WorkerChangeEventResponse, status_code=201)
async def publish_change_event(
    payload: WorkerChangeEventCreateRequest,
    principal: Annotated[WorkerPrincipal, Depends(current_worker)],
    x_job_lease_token: Annotated[str | None, Header(alias="X-Job-Lease-Token")],
) -> WorkerChangeEventResponse:
    if not x_job_lease_token:
        raise ApiError(401, "job_lease_required", "Job lease required", "A current job lease token is required.")
    async with tenant_connection(principal.organisation_id) as connection:
        job = await _active_lease_job(connection, principal, payload.job_id, x_job_lease_token)
        run = await (await connection.execute(
            """SELECT pr.id FROM processing_runs pr JOIN orchestration_runs r ON r.id=pr.orchestration_run_id
            WHERE pr.id=%s AND r.processing_job_id=%s""", (payload.processing_run_id, payload.job_id)
        )).fetchone()
        observation = await (await connection.execute(
            "SELECT id FROM observations WHERE id=%s AND site_id=%s", (payload.observation_id, job["site_id"])
        )).fetchone()
        if not run or not observation:
            raise ApiError(422, "invalid_event_lineage", "Invalid event lineage", "The observation and processing run must belong to the claimed job.")
        event = await (await connection.execute(
            """INSERT INTO change_events(organisation_id,site_id,observation_id,processing_run_id,category,geometry,affected_area_sq_m,signal_strength,sensitivity,resolution)
            VALUES (%s,%s,%s,%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s,%s,%s,%s)
            RETURNING id,site_id,observation_id,processing_run_id,category,review_status,sensitivity,created_at""",
            (principal.organisation_id, job["site_id"], payload.observation_id, payload.processing_run_id, payload.category, payload.geometry.model_dump_json(), payload.affected_area_sq_m, payload.signal_strength, payload.sensitivity, payload.resolution),
        )).fetchone()
        for cell in payload.grid_cells:
            valid = await (await connection.execute("SELECT id FROM grid_cells WHERE id=%s", (cell.grid_cell_id,))).fetchone()
            if not valid:
                raise ApiError(422, "invalid_grid_cell", "Invalid grid cell", "Every event grid cell must belong to this organisation.")
            await connection.execute(
                "INSERT INTO event_grid_cells(organisation_id,event_id,grid_cell_id,measurements) VALUES (%s,%s,%s,%s)",
                (principal.organisation_id, event["id"], cell.grid_cell_id, Jsonb(cell.measurements)),
            )
        await notify_event_subscribers(
            connection, organisation_id=principal.organisation_id, site_id=job["site_id"], event_id=event["id"],
            notification_type="change_event_detected", safe_summary="A new possible forest change requires review.",
            sensitivity=payload.sensitivity, protected_path=f"/events/{event['id']}",
        )
        await record_audit(connection, organisation_id=principal.organisation_id, actor_id=None, action="change_event.detected", target_type="change_event", target_id=event["id"], after={"job_id": str(payload.job_id), "category": payload.category, "grid_cell_count": len(payload.grid_cells)})
    return WorkerChangeEventResponse(data=WorkerChangeEventData.model_validate(event))
