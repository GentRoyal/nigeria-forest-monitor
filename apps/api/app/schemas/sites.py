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
