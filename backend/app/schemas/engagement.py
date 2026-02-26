import uuid
from datetime import datetime

from pydantic import BaseModel


class EngagementActionResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    user_id: uuid.UUID
    action_type: str
    status: str
    comment_text: str | None
    attempted_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata_: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CommentGenerateRequest(BaseModel):
    post_content: str
    page_tags: list[str] | None = None
    page_relationship: str | None = None


class CommentGenerateResponse(BaseModel):
    comments: list[str]
    model_used: str
    review_passed: bool
    review_notes: str | None = None


class ActivityFeedItem(BaseModel):
    type: str  # like_completed, comment_completed, comment_failed, post_discovered, etc.
    user_name: str
    post_url: str | None = None
    page_name: str | None = None
    timestamp: datetime
    comment_text: str | None = None
    error: str | None = None
