from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .sites import GeoJsonArea


class WorkerClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_identity: str = Field(min_length=3, max_length=200)
    lease_seconds: int | None = Field(default=None, ge=60, le=1800)

    @field_validator("worker_identity")
    @classmethod
    def strip_worker_identity(cls, value: str) -> str:
        return value.strip()


class WorkerJobData(BaseModel):
    id: UUID
    site_id: UUID
    observation_id: UUID | None
    grid_version_id: UUID | None
    job_type: str
    trigger_type: str
    priority: int
    status: str
    progress: int
    attempt_count: int
    current_stage: str | None
    lease_expires_at: datetime | None


class WorkerClaimData(BaseModel):
    job: WorkerJobData
    lease_token: str


class WorkerClaimResponse(BaseModel):
    data: WorkerClaimData


class WorkerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    progress: int = Field(ge=0, le=99)
    stage: Literal["discovery", "quality_check", "processing", "publishing"] | None = None
    lease_seconds: int | None = Field(default=None, ge=60, le=1800)


class WorkerHeartbeatResponse(BaseModel):
    data: WorkerJobData


class WorkerStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["discovery", "quality_check", "processing", "publishing"]
    details: dict[str, object] = Field(default_factory=dict)


class WorkerStageData(BaseModel):
    id: UUID
    processing_job_id: UUID
    stage: str
    details: dict[str, object]
    created_at: datetime


class WorkerStageResponse(BaseModel):
    data: WorkerStageData


class WorkerFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=3, max_length=2_000)
    retryable: bool = False

    @field_validator("category", "message")
    @classmethod
    def strip_failure_text(cls, value: str) -> str:
        return value.strip()


class WorkerFailureResponse(BaseModel):
    data: WorkerJobData


class WorkerCatalogueUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    provider: str = Field(min_length=2, max_length=120)
    collection: str = Field(min_length=2, max_length=160)
    source_identifier: str = Field(min_length=1, max_length=500)
    acquired_at: datetime
    footprint: GeoJsonArea
    assets: dict[str, Any] = Field(default_factory=dict)
    licence: str = Field(min_length=2, max_length=240)
    attribution: str = Field(min_length=2, max_length=500)
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "collection", "source_identifier", "licence", "attribution")
    @classmethod
    def strip_catalogue_text(cls, value: str) -> str:
        return value.strip()


class WorkerCatalogueData(BaseModel):
    id: UUID
    provider: str
    collection: str
    source_identifier: str
    acquired_at: datetime
    assets: dict[str, Any]
    licence: str
    attribution: str
    source_metadata: dict[str, Any]
    created_at: datetime


class WorkerCatalogueResponse(BaseModel):
    data: WorkerCatalogueData


class WorkerObservationUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    catalogue_item_id: UUID
    grid_version_id: UUID
    baseline_observation_id: UUID | None = None
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    quality_assessment: dict[str, Any] = Field(default_factory=dict)
    eligibility: Literal["pending", "eligible", "ineligible"]
    eligibility_reason: str | None = Field(default=None, max_length=2_000)
    discovery_method: Literal["scheduled", "manual", "backfill"]
    status: Literal["discovered", "evaluating", "eligible", "ineligible", "queued", "processing", "ready", "failed", "superseded"]
    observed_at: datetime

    @field_validator("eligibility_reason")
    @classmethod
    def strip_eligibility_reason(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class WorkerObservationData(BaseModel):
    id: UUID
    site_id: UUID
    catalogue_item_id: UUID
    grid_version_id: UUID
    baseline_observation_id: UUID | None
    coverage_ratio: float | None
    quality_assessment: dict[str, Any]
    eligibility: str
    eligibility_reason: str | None
    discovery_method: str
    status: str
    observed_at: datetime
    created_at: datetime


class WorkerObservationResponse(BaseModel):
    data: WorkerObservationData


class WorkerResultAssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_type: Literal["source_reference", "derived_cog", "thumbnail"]
    object_key: str | None = Field(default=None, max_length=1024)
    source_href: str | None = Field(default=None, max_length=2048)
    cog_valid: bool | None = None
    bands: list[dict[str, Any]] = Field(default_factory=list)
    resolution_metres: float | None = Field(default=None, gt=0)
    checksum: str | None = Field(default=None, max_length=256)
    size_bytes: int | None = Field(default=None, ge=0)
    processing_version: str | None = Field(default=None, max_length=160)
    lineage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_correct_location(self) -> "WorkerResultAssetInput":
        if self.asset_type == "source_reference" and not self.source_href:
            raise ValueError("source_reference assets require source_href")
        if self.asset_type != "source_reference" and not self.object_key:
            raise ValueError("derived assets require object_key")
        return self


class WorkerGridObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grid_cell_id: UUID
    quality: dict[str, Any] = Field(default_factory=dict)
    measurements: dict[str, Any] = Field(default_factory=dict)
    change_features: dict[str, Any] = Field(default_factory=dict)


class WorkerResultEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "possible_vegetation_loss",
        "possible_linear_clearing",
        "possible_burn_signal",
        "possible_water_change",
        "unknown_disturbance",
    ]
    geometry: GeoJsonArea
    affected_area_sq_m: float | None = Field(default=None, ge=0)
    signal_strength: float | None = Field(default=None, ge=0, le=1)
    sensitivity: Literal["normal", "sensitive"] = "normal"
    resolution: str | None = Field(default=None, max_length=500)
    grid_cells: list["WorkerEventCellInput"] = Field(default_factory=list, max_length=10_000)


class WorkerCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orchestrator_run_identifier: str = Field(min_length=3, max_length=300)
    dag_id: str = Field(min_length=2, max_length=200)
    dag_version: str = Field(min_length=1, max_length=120)
    observation_id: UUID | None = None
    boundary_version_id: UUID
    grid_version_id: UUID
    input_assets: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    code_version: str = Field(min_length=1, max_length=160)
    model_version: str | None = Field(default=None, max_length=160)
    environment: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    output_assets: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    checksum: str | None = Field(default=None, max_length=256)
    assets: list[WorkerResultAssetInput] = Field(default_factory=list, max_length=10_000)
    grid_observations: list[WorkerGridObservationInput] = Field(default_factory=list, max_length=100_000)
    events: list[WorkerResultEventInput] = Field(default_factory=list, max_length=10_000)


class WorkerCompleteResponse(BaseModel):
    data: "WorkerCompleteData"


class WorkerCompleteData(BaseModel):
    job: WorkerJobData
    processing_run_id: UUID


class WorkerAssetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    processing_run_id: UUID
    observation_id: UUID | None = None
    asset_type: Literal["source_reference", "derived_cog", "thumbnail"]
    object_key: str | None = Field(default=None, max_length=1024)
    source_href: str | None = Field(default=None, max_length=2048)
    cog_valid: bool | None = None
    bands: list[dict[str, Any]] = Field(default_factory=list)
    resolution_metres: float | None = Field(default=None, gt=0)
    checksum: str | None = Field(default=None, max_length=256)
    size_bytes: int | None = Field(default=None, ge=0)
    processing_version: str | None = Field(default=None, max_length=160)
    lineage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("object_key", "source_href")
    @classmethod
    def strip_asset_locations(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def require_correct_location(self) -> "WorkerAssetCreateRequest":
        if self.asset_type == "source_reference" and not self.source_href:
            raise ValueError("source_reference assets require source_href")
        if self.asset_type != "source_reference" and not self.object_key:
            raise ValueError("derived assets require object_key")
        return self


class WorkerAssetData(BaseModel):
    id: UUID
    observation_id: UUID | None
    processing_run_id: UUID | None
    asset_type: str
    object_key: str | None
    source_href: str | None
    cog_valid: bool | None
    bands: list[dict[str, Any]]
    resolution_metres: float | None
    checksum: str | None
    size_bytes: int | None
    processing_version: str | None
    lineage: dict[str, Any]
    created_at: datetime


class WorkerAssetResponse(BaseModel):
    data: WorkerAssetData


class WorkerEventCellInput(BaseModel):
    grid_cell_id: UUID
    measurements: dict[str, Any] = Field(default_factory=dict)


class WorkerChangeEventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    observation_id: UUID
    processing_run_id: UUID
    category: Literal[
        "possible_vegetation_loss",
        "possible_linear_clearing",
        "possible_burn_signal",
        "possible_water_change",
        "unknown_disturbance",
    ]
    geometry: GeoJsonArea
    affected_area_sq_m: float | None = Field(default=None, ge=0)
    signal_strength: float | None = Field(default=None, ge=0, le=1)
    sensitivity: Literal["normal", "sensitive"] = "normal"
    resolution: str | None = Field(default=None, max_length=500)
    grid_cells: list[WorkerEventCellInput] = Field(default_factory=list, max_length=10_000)


class WorkerChangeEventData(BaseModel):
    id: UUID
    site_id: UUID
    observation_id: UUID
    processing_run_id: UUID
    category: str
    review_status: str
    sensitivity: str
    created_at: datetime


class WorkerChangeEventResponse(BaseModel):
    data: WorkerChangeEventData


class WorkerExportCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    result_object_key: str = Field(min_length=1, max_length=1024)
    checksum: str = Field(min_length=16, max_length=256)
    expires_at: datetime

    @field_validator("result_object_key")
    @classmethod
    def relative_export_key(cls, value: str) -> str:
        key = value.strip().replace("\\", "/")
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError("result_object_key must be a relative export path")
        return key


class WorkerDiscoveryCursorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    cursor: str = Field(min_length=1, max_length=500)
