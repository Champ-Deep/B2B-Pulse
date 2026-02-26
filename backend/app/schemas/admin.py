import uuid
from datetime import datetime

from pydantic import BaseModel


class OrgSummary(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    team_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgDetail(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    team_count: int
    active_integrations: int
    tracked_pages_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PlatformStats(BaseModel):
    total_orgs: int
    total_users: int
    active_users: int
    total_engagements: int
    active_integrations: int
