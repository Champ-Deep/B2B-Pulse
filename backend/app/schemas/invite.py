import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class InviteCreateRequest(BaseModel):
    email: EmailStr | None = None
    team_id: uuid.UUID | None = None


class InviteResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str | None
    invite_code: str
    status: str
    expires_at: datetime
    created_at: datetime
    invite_url: str
    team_id: uuid.UUID | None = None
    team_name: str | None = None

    model_config = {"from_attributes": True}


class InviteValidateResponse(BaseModel):
    valid: bool
    org_name: str | None = None
    email: str | None = None
    team_name: str | None = None


class OrgMemberResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    integrations: list[str]
    team_id: uuid.UUID | None = None
    team_name: str | None = None

    model_config = {"from_attributes": True}
