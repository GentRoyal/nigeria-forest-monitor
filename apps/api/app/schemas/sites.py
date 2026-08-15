from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import ResponseMeta


class GeoJsonArea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]


class BoundaryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geometry: GeoJsonArea
    source_authority: str = Field(min_length=1, max_length=240)
    source_identifier: str = Field(min_length=1, max_length=240)
    source_url: str | None = Field(default=None, max_length=2048)
    licence: str = Field(min_length=1, max_length=240)
    attribution: str = Field(min_length=1, max_length=500)
    effective_date: date | None = None
    source_crs: str = Field(default="EPSG:4326", pattern=r"^EPSG:[1-9][0-9]{0,7}$")

    @field_validator(
        "source_authority", "source_identifier", "licence", "attribution", "source_crs"
    )
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_url")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class BoundaryVersionCreateRequest(BoundaryCreateRequest):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class SiteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    origin: Literal["predefined", "custom"]
    sensitivity: Literal["normal", "sensitive"] = "normal"
    managing_department_id: UUID
    tags: list[str] = Field(default_factory=list, max_length=30)
    boundary: BoundaryCreateRequest

    @field_validator("name", "slug")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        return value.strip()

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("tags")
    @classmethod
    def normalise_tags(cls, values: list[str]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip().lower()
            if not value or len(value) > 80:
                raise ValueError("tags must contain 1 to 80 characters")
            if value not in seen:
                tags.append(value)
                seen.add(value)
        return tags


class SiteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    sensitivity: Literal["normal", "sensitive"] | None = None
    managing_department_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> "SiteUpdateRequest":
        mutable = {"name", "slug", "description", "sensitivity", "managing_department_id"}
        if not (self.model_fields_set & mutable):
            raise ValueError("at least one mutable field must be supplied")
        required = mutable - {"description"}
        if any(
            field in self.model_fields_set and getattr(self, field) is None for field in required
        ):
            raise ValueError("site identity, sensitivity, and department cannot be null")
        return self

    @field_validator("name", "slug", "description", "reason")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class TagData(BaseModel):
    id: UUID
    name: str


class BoundaryData(BaseModel):
    id: UUID
    version: int
    geometry: dict[str, Any] | None = None
    source_authority: str
    source_identifier: str
    source_url: str | None
    licence: str
    attribution: str
    effective_date: date | None
    source_crs: str
    checksum: str
    validation_result: dict[str, Any]
    area_sq_km: float
    bounds: dict[str, float]
    created_by: UUID
    change_reason: str
    superseded_at: datetime | None
    is_current: bool
    created_at: datetime


class BoundaryResponse(BaseModel):
    data: BoundaryData
    meta: ResponseMeta


class BoundaryListMeta(ResponseMeta):
    next_cursor: str | None = None


class BoundaryListResponse(BaseModel):
    data: list[BoundaryData]
    meta: BoundaryListMeta


class GridVersionData(BaseModel):
    id: UUID
    version: int
    method: str
    resolution_metres: float
    parameters: dict[str, Any]
    creation_reason: str
    processing_compatibility: str
    cell_count: int
    superseded_at: datetime | None
    is_current: bool
    created_at: datetime


class GridVersionListMeta(ResponseMeta):
    next_cursor: str | None = None


class GridVersionListResponse(BaseModel):
    data: list[GridVersionData]
    meta: GridVersionListMeta


class GridGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["square"] = "square"
    resolution_metres: float = Field(ge=50, le=10_000)
    clip_to_boundary: bool = True
    creation_reason: str = Field(min_length=3, max_length=500)
    processing_compatibility: str = Field(min_length=1, max_length=120)

    @field_validator("creation_reason", "processing_compatibility")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()


class GridVersionResponse(BaseModel):
    data: GridVersionData
    meta: ResponseMeta


class ScheduleUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence: Literal["weekly", "fortnightly", "monthly"]
    sensor_settings: dict[str, Any] = Field(default_factory=dict)
    quality_settings: dict[str, Any] = Field(default_factory=dict)


class ScheduleSuspendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class ScheduleData(BaseModel):
    id: UUID
    site_id: UUID
    cadence: str
    sensor_settings: dict[str, Any]
    quality_settings: dict[str, Any]
    next_due_at: datetime
    status: str
    scheduling_version: int
    changed_by: UUID
    created_at: datetime
    updated_at: datetime


class ScheduleResponse(BaseModel):
    data: ScheduleData
    meta: ResponseMeta


class ManualJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: Literal["discovery", "processing", "reprocessing"]
    observation_id: UUID | None = None
    processing_version: str | None = Field(default=None, max_length=120)
    priority: int = Field(default=5, ge=1, le=9)
    suspended_site_override: bool = False
    override_warning_acknowledged: bool = False

    @model_validator(mode="after")
    def require_observation_for_processing(self) -> "ManualJobRequest":
        if self.job_type in {"processing", "reprocessing"} and self.observation_id is None:
            raise ValueError("processing and reprocessing jobs require observation_id")
        if self.suspended_site_override and not self.override_warning_acknowledged:
            raise ValueError("suspended-site overrides require warning acknowledgement")
        return self


class JobData(BaseModel):
    id: UUID
    site_id: UUID
    observation_id: UUID | None
    grid_version_id: UUID | None
    retry_of_job_id: UUID | None
    job_type: str
    trigger_type: str
    priority: int
    status: str
    progress: int
    created_at: datetime
    updated_at: datetime


class JobResponse(BaseModel):
    data: JobData
    meta: ResponseMeta


class JobCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class JobRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int | None = Field(default=None, ge=1, le=9)


class JobListMeta(ResponseMeta):
    next_cursor: str | None = None


class JobListResponse(BaseModel):
    data: list[JobData]
    meta: JobListMeta


class ObservationData(BaseModel):
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


class ObservationListMeta(ResponseMeta):
    next_cursor: str | None = None


class ObservationListResponse(BaseModel):
    data: list[ObservationData]
    meta: ObservationListMeta


class ObservationResponse(BaseModel):
    data: ObservationData
    meta: ResponseMeta


class AssetData(BaseModel):
    id: UUID
    observation_id: UUID | None
    processing_run_id: UUID | None
    asset_type: str
    cog_valid: bool | None
    bands: list[dict[str, Any]]
    resolution_metres: float | None
    checksum: str | None
    size_bytes: int | None
    processing_version: str | None
    lineage: dict[str, Any]
    created_at: datetime


class AssetListResponse(BaseModel):
    data: list[AssetData]
    meta: ResponseMeta


class AssetDownloadData(BaseModel):
    asset_id: UUID
    reference: str
    expires_at: datetime


class AssetDownloadResponse(BaseModel):
    data: AssetDownloadData
    meta: ResponseMeta


class NotificationData(BaseModel):
    id: UUID
    event_id: UUID | None
    notification_type: str
    safe_summary: str
    sensitivity: str
    protected_path: str
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    data: list[NotificationData]
    meta: ResponseMeta


class NotificationResponse(BaseModel):
    data: NotificationData
    meta: ResponseMeta


class SubscriptionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: UUID | None = None
    event_id: UUID | None = None
    channels: list[Literal["in_app", "email"]] = Field(default_factory=lambda: ["in_app"], min_length=1, max_length=2)
    digest_enabled: bool = True

    @model_validator(mode="after")
    def require_one_target(self) -> "SubscriptionCreateRequest":
        if (self.site_id is None) == (self.event_id is None):
            raise ValueError("supply exactly one of site_id or event_id")
        return self


class SubscriptionData(BaseModel):
    id: UUID
    site_id: UUID | None
    event_id: UUID | None
    channels: list[str]
    digest_enabled: bool
    created_at: datetime


class SubscriptionResponse(BaseModel):
    data: SubscriptionData
    meta: ResponseMeta


class SubscriptionListResponse(BaseModel):
    data: list[SubscriptionData]
    meta: ResponseMeta


class NotificationPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[Literal["in_app", "email"]] = Field(min_length=1, max_length=2)
    digest_enabled: bool = True


class NotificationPreferencesData(BaseModel):
    channels: list[str]
    digest_enabled: bool


class NotificationPreferencesResponse(BaseModel):
    data: NotificationPreferencesData
    meta: ResponseMeta


class ChangeEventData(BaseModel):
    id: UUID
    site_id: UUID
    observation_id: UUID
    processing_run_id: UUID
    category: str
    geometry: dict[str, Any]
    affected_area_sq_m: float | None
    signal_strength: float | None
    review_status: str
    sensitivity: str
    resolution: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChangeEventListMeta(ResponseMeta):
    next_cursor: str | None = None


class ChangeEventListResponse(BaseModel):
    data: list[ChangeEventData]
    meta: ChangeEventListMeta


class ChangeEventResponse(BaseModel):
    data: ChangeEventData
    meta: ResponseMeta


class ReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_type: Literal["remote_analysis", "institutional_verification"]
    decision: str = Field(min_length=2, max_length=120)
    rationale: str = Field(min_length=3, max_length=10_000)
    confidence_statement: str = Field(min_length=3, max_length=2_000)
    supporting_evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("decision", "rationale", "confidence_statement")
    @classmethod
    def strip_review_text(cls, value: str) -> str:
        return value.strip()


class ReviewData(BaseModel):
    id: UUID
    event_id: UUID
    review_type: str
    decision: str
    rationale: str
    confidence_statement: str
    actor_id: UUID
    supporting_evidence_ids: list[UUID]
    supersedes_review_id: UUID | None
    submitted_at: datetime


class ReviewResponse(BaseModel):
    data: ReviewData
    meta: ResponseMeta


class ReviewListResponse(BaseModel):
    data: list[ReviewData]
    meta: ResponseMeta


class EventTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_status: Literal[
        "under_remote_review",
        "awaiting_more_observations",
        "remotely_corroborated",
        "referred_to_authority",
        "institutionally_verified",
        "inconclusive",
        "dismissed",
        "resolved",
    ]
    reason: str = Field(min_length=3, max_length=500)
    review_id: UUID | None = None

    @field_validator("reason")
    @classmethod
    def strip_transition_reason(cls, value: str) -> str:
        return value.strip()


class EventAssignmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: UUID
    assignment_type: Literal["analyst_review", "institutional_verification"]
    due_at: datetime | None = None


class EventAssignmentData(BaseModel):
    id: UUID
    event_id: UUID
    assignee_id: UUID
    assigned_by: UUID
    assignment_type: str
    due_at: datetime | None
    accepted_at: datetime | None
    completed_at: datetime | None
    status: str
    created_at: datetime


class EventAssignmentResponse(BaseModel):
    data: EventAssignmentData
    meta: ResponseMeta


class EventAssignmentListResponse(BaseModel):
    data: list[EventAssignmentData]
    meta: ResponseMeta


class EventEvidenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: Literal[
        "raster_comparison", "analyst_note", "authorised_report", "authorised_media"
    ]
    source: str = Field(min_length=2, max_length=500)
    collected_at: datetime
    access_classification: Literal["normal", "sensitive", "restricted"] = "normal"
    checksum: str | None = Field(default=None, min_length=16, max_length=256)
    object_key: str | None = Field(default=None, min_length=1, max_length=1024)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", "checksum", "object_key")
    @classmethod
    def strip_evidence_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class EventEvidenceData(BaseModel):
    id: UUID
    event_id: UUID
    evidence_type: str
    source: str
    collected_by: UUID
    collected_at: datetime
    access_classification: str
    checksum: str | None
    object_key: str | None
    provenance: dict[str, Any]
    created_at: datetime


class EventEvidenceResponse(BaseModel):
    data: EventEvidenceData
    meta: ResponseMeta


class EventEvidenceListResponse(BaseModel):
    data: list[EventEvidenceData]
    meta: ResponseMeta


class EventCommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("body")
    @classmethod
    def strip_comment(cls, value: str) -> str:
        return value.strip()


class EventCommentData(BaseModel):
    id: UUID
    event_id: UUID
    author_id: UUID
    body: str
    created_at: datetime
    edited_at: datetime | None


class EventCommentResponse(BaseModel):
    data: EventCommentData
    meta: ResponseMeta


class EventCommentListResponse(BaseModel):
    data: list[EventCommentData]
    meta: ResponseMeta


class GridCellData(BaseModel):
    id: UUID
    grid_version_id: UUID
    cell_key: str
    display_label: str | None
    geometry: dict[str, Any]
    area_sq_m: float
    created_at: datetime


class GridCellListMeta(ResponseMeta):
    next_cursor: str | None = None
    grid_version_id: UUID


class GridCellListResponse(BaseModel):
    data: list[GridCellData]
    meta: GridCellListMeta


class SiteData(BaseModel):
    id: UUID
    organisation_id: UUID
    managing_department_id: UUID
    managing_department_name: str
    name: str
    slug: str
    description: str | None
    origin: str
    sensitivity: str
    status: str
    monitoring_health: str
    version: int
    tags: list[TagData]
    current_boundary: BoundaryData | None
    created_at: datetime
    updated_at: datetime


class SiteResponse(BaseModel):
    data: SiteData
    meta: ResponseMeta


class SiteListMeta(ResponseMeta):
    next_cursor: str | None = None


class SiteListResponse(BaseModel):
    data: list[SiteData]
    meta: SiteListMeta
