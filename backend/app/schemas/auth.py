import uuid

from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    org_id: uuid.UUID
    is_active: bool
    team_id: uuid.UUID | None = None
    is_platform_admin: bool = False
    linkedin_id: str | None = None

    model_config = {"from_attributes": True}
