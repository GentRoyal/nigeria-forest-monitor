from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import ResponseMeta


class MemberTeamData(BaseModel):
    id: UUID
    name: str


class MemberData(BaseModel):
    id: UUID
    organisation_id: UUID
    department_id: UUID
    department_name: str
    email: str
    display_name: str
    role: str
    status: str
    timezone: str
    teams: list[MemberTeamData]
    version: int
    invited_at: datetime | None
    activated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemberResponse(BaseModel):
    data: MemberData
    meta: ResponseMeta


class MemberListData(BaseModel):
    items: list[MemberData]
    total: int
    limit: int
    offset: int


class MemberListResponse(BaseModel):
    data: MemberListData
    meta: ResponseMeta


class MemberUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = Field(
        default=None,
        pattern="^(owner|administrator|analyst|verification_officer|viewer)$",
    )
    status: str | None = Field(default=None, pattern="^(active|suspended|disabled)$")
    department_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> "MemberUpdateRequest":
        if self.role is None and self.status is None and self.department_id is None:
            raise ValueError("at least one member field is required")
        self.reason = " ".join(self.reason.split())
        if len(self.reason) < 3:
            raise ValueError("reason must contain at least three visible characters")
        return self


class TeamMembershipData(BaseModel):
    team_id: UUID
    user_id: UUID
    status: str
    created_at: datetime


class TeamMembershipResponse(BaseModel):
    data: TeamMembershipData
    meta: ResponseMeta
