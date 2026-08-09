from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import ResponseMeta


class OrganisationData(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    workspace_template_version: int
    default_timezone: str
    version: int
    created_at: datetime
    updated_at: datetime


class OrganisationResponse(BaseModel):
    data: OrganisationData
    meta: ResponseMeta


class OrganisationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    default_timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = " ".join(value.split())
        if len(normalised) < 2:
            raise ValueError("name must contain at least two visible characters")
        return normalised

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        try:
            ZoneInfo(normalised)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("default_timezone must be a valid IANA timezone") from error
        return normalised

    @model_validator(mode="after")
    def require_change(self) -> "OrganisationUpdateRequest":
        if self.name is None and self.default_timezone is None:
            raise ValueError("at least one organisation field is required")
        return self


class DepartmentData(BaseModel):
    id: UUID
    organisation_id: UUID
    name: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class DepartmentResponse(BaseModel):
    data: DepartmentData
    meta: ResponseMeta


class DepartmentListData(BaseModel):
    items: list[DepartmentData]


class DepartmentListResponse(BaseModel):
    data: DepartmentListData
    meta: ResponseMeta


class DepartmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str) -> str:
        normalised = " ".join(value.split())
        if len(normalised) < 2:
            raise ValueError("name must contain at least two visible characters")
        return normalised


class DepartmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    status: str | None = Field(default=None, pattern="^(active|archived)$")

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = " ".join(value.split())
        if len(normalised) < 2:
            raise ValueError("name must contain at least two visible characters")
        return normalised

    @model_validator(mode="after")
    def require_change(self) -> "DepartmentUpdateRequest":
        if self.name is None and self.status is None:
            raise ValueError("at least one department field is required")
        return self


class TeamData(BaseModel):
    id: UUID
    organisation_id: UUID
    department_id: UUID
    department_name: str
    name: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class TeamResponse(BaseModel):
    data: TeamData
    meta: ResponseMeta


class TeamListData(BaseModel):
    items: list[TeamData]


class TeamListResponse(BaseModel):
    data: TeamListData
    meta: ResponseMeta


class TeamCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    name: str = Field(min_length=2, max_length=160)

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str) -> str:
        normalised = " ".join(value.split())
        if len(normalised) < 2:
            raise ValueError("name must contain at least two visible characters")
        return normalised


class TeamUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    status: str | None = Field(default=None, pattern="^(active|archived)$")

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = " ".join(value.split())
        if len(normalised) < 2:
            raise ValueError("name must contain at least two visible characters")
        return normalised

    @model_validator(mode="after")
    def require_change(self) -> "TeamUpdateRequest":
        if self.name is None and self.status is None:
            raise ValueError("at least one team field is required")
        return self
