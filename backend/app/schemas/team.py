import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class TeamUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class TeamResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    member_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamAssignRequest(BaseModel):
    team_id: uuid.UUID | None = None
