from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from .auth import ResponseMeta


class AuditActorData(BaseModel):
    id: UUID
    display_name: str


class AuditEventData(BaseModel):
    id: UUID
    organisation_id: UUID
    actor: AuditActorData | None
    action: str
    target_type: str
    target_id: UUID | None
    before_summary: dict[str, Any] | None
    after_summary: dict[str, Any] | None
    reason: str | None
    correlation_id: UUID
    ip_address: str | None
    created_at: datetime


class AuditEventListData(BaseModel):
    items: list[AuditEventData]


class AuditEventListMeta(ResponseMeta):
    next_cursor: str | None
    has_more: bool


class AuditEventListResponse(BaseModel):
    data: AuditEventListData
    meta: AuditEventListMeta
