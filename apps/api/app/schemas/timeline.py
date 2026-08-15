from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from .auth import ResponseMeta


class TimelineEntryData(BaseModel):
    id: UUID
    entry_type: str
    occurred_at: datetime
    summary: str
    payload: dict[str, Any]


class TimelineResponse(BaseModel):
    data: list[TimelineEntryData]
    meta: ResponseMeta
