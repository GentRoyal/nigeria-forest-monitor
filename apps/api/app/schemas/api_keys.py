from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import ResponseMeta


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    scopes: set[Literal["read", "write", "export"]] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str) -> str:
        return " ".join(value.split())


class ApiKeyData(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreatedData(ApiKeyData):
    secret: str


class ApiKeyResponse(BaseModel):
    data: ApiKeyData
    meta: ResponseMeta


class ApiKeyCreatedResponse(BaseModel):
    data: ApiKeyCreatedData
    meta: ResponseMeta


class ApiKeyListResponse(BaseModel):
    data: list[ApiKeyData]
    meta: ResponseMeta


class UsageData(BaseModel):
    active_members: int
    active_sites: int
    queued_or_running_jobs: int
    stored_assets: int
    asset_bytes: int
    active_api_keys: int
    completed_exports: int


class UsageResponse(BaseModel):
    data: UsageData
    meta: ResponseMeta
