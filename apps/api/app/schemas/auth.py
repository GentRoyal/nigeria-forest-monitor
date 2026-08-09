from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..security.passwords import validate_password


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organisation_slug: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("organisation_slug", "email")
    @classmethod
    def normalise_identity(cls, value: str) -> str:
        return value.strip().lower()


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organisation_slug: str = Field(min_length=1, max_length=120)
    refresh_token: str | None = Field(default=None, min_length=32, max_length=512)

    @field_validator("organisation_slug")
    @classmethod
    def normalise_slug(cls, value: str) -> str:
        return value.strip().lower()


class AccessTokenData(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    session_id: UUID


class ResponseMeta(BaseModel):
    request_id: UUID


class AccessTokenResponse(BaseModel):
    data: AccessTokenData
    meta: ResponseMeta


class TeamSummary(BaseModel):
    id: UUID
    name: str


class ProfileData(BaseModel):
    id: UUID
    organisation_id: UUID
    email: str
    display_name: str
    role: str
    status: str
    timezone: str
    department_id: UUID
    department_name: str
    teams: list[TeamSummary]


class ProfileResponse(BaseModel):
    data: ProfileData
    meta: ResponseMeta


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=2, max_length=160)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("display_name")
    @classmethod
    def normalise_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = " ".join(value.split())
        if len(normalised) < 2:
            raise ValueError("display_name must contain at least two visible characters")
        return normalised

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        try:
            ZoneInfo(normalised)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return normalised

    @model_validator(mode="after")
    def require_change(self) -> "ProfileUpdateRequest":
        if self.display_name is None and self.timezone is None:
            raise ValueError("at least one profile field is required")
        return self


class SessionData(BaseModel):
    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    current: bool


class SessionListData(BaseModel):
    items: list[SessionData]


class SessionListResponse(BaseModel):
    data: SessionListData
    meta: ResponseMeta


class EmptyData(BaseModel):
    success: bool = True


class EmptyResponse(BaseModel):
    data: EmptyData
    meta: ResponseMeta


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organisation_slug: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)

    @field_validator("organisation_slug", "email")
    @classmethod
    def normalise_identity(cls, value: str) -> str:
        return value.strip().lower()


class PasswordResetRequestData(BaseModel):
    accepted: bool = True
    development_token: str | None = None


class PasswordResetRequestResponse(BaseModel):
    data: PasswordResetRequestData
    meta: ResponseMeta


class PasswordResetCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organisation_slug: str = Field(min_length=1, max_length=120)
    token: str = Field(min_length=32, max_length=512)
    new_password: str = Field(min_length=12, max_length=256)

    @field_validator("organisation_slug")
    @classmethod
    def normalise_slug(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("new_password")
    @classmethod
    def enforce_password_policy(cls, value: str) -> str:
        validate_password(value)
        return value


class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organisation_slug: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("organisation_slug")
    @classmethod
    def normalise_invitation_slug(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def normalise_display_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("password")
    @classmethod
    def enforce_invitation_password_policy(cls, value: str) -> str:
        validate_password(value)
        return value


class InvitationSummaryData(BaseModel):
    masked_email: str
    role: str
    organisation_name: str
    department_name: str
    expires_at: datetime


class InvitationSummaryResponse(BaseModel):
    data: InvitationSummaryData
    meta: ResponseMeta


class InvitationAcceptedData(BaseModel):
    user_id: UUID
    next_action: str = "login"


class InvitationAcceptedResponse(BaseModel):
    data: InvitationAcceptedData
    meta: ResponseMeta
