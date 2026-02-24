import uuid
from datetime import datetime

from pydantic import BaseModel


class TrackedPageCreate(BaseModel):
    url: str
    name: str = ""
    page_type: str = "personal"


class TrackedPageUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None


class TrackedPageResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    platform: str
    external_id: str | None
    url: str
    name: str
    page_type: str
    active: bool

    model_config = {"from_attributes": True}


class SubscriptionCreate(BaseModel):
    auto_like: bool = True
    auto_comment: bool = True
    polling_mode: str = "normal"
    tags: list[str] | None = None


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    tracked_page_id: uuid.UUID
    user_id: uuid.UUID
    auto_like: bool
    auto_comment: bool
    polling_mode: str
    tags: list | None

    model_config = {"from_attributes": True}


class PostSubmitRequest(BaseModel):
    url: str


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


class EngagementBrief(BaseModel):
    id: uuid.UUID
    action_type: str
    status: str
    completed_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}


class PostWithEngagements(BaseModel):
    id: uuid.UUID
    url: str
    content_text: str | None = None
    external_post_id: str
    first_seen_at: datetime
    engagements: list[EngagementBrief] = []

    model_config = {"from_attributes": True}
