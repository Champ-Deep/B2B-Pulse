from pydantic import BaseModel


class UserProfileUpdate(BaseModel):
    markdown_text: str | None = None
    tone_settings: dict | None = None


class UserProfileResponse(BaseModel):
    markdown_text: str | None
    tone_settings: dict | None

    model_config = {"from_attributes": True}
