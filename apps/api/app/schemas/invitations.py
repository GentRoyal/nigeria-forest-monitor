from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import ResponseMeta


class InvitationData(BaseModel):
    id: UUID
    organisation_id: UUID
    department_id: UUID
    department_name: str
    email: str
    role: str
    status: str
    invited_by: UUID
    invited_by_name: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationListData(BaseModel):
    items: list[InvitationData]
    total: int
    limit: int
    offset: int


class InvitationListResponse(BaseModel):
    data: InvitationListData
    meta: ResponseMeta


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(
        pattern="^(administrator|analyst|verification_officer|viewer)$",
    )

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        normalised = value.strip().lower()
        local, separator, domain = normalised.partition("@")
        if not separator or not local or not domain or "." not in domain:
            raise ValueError("email must be a valid address")
        return normalised


class InvitationCreatedData(InvitationData):
    development_token: str | None = None


class InvitationCreatedResponse(BaseModel):
    data: InvitationCreatedData
    meta: ResponseMeta
