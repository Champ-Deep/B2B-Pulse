from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.user import User, UserProfile
from app.schemas.engagement import CommentGenerateRequest, CommentGenerateResponse
from app.services.comment_generator import generate_and_review_comment

router = APIRouter(prefix="/automation", tags=["automation"])

AUTOMATION_DEFAULTS = {
    "risk_profile": "safe",
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
    "polling_interval": 300,
}


class AutomationSettingsUpdate(BaseModel):
    risk_profile: Literal["safe", "aggro"] = "safe"
    quiet_hours_start: str = Field("22:00", pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str = Field("07:00", pattern=r"^\d{2}:\d{2}$")
    polling_interval: int = Field(300, ge=60, le=3600)


@router.get("/settings")
async def get_automation_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's automation settings."""
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()

    if not profile or not profile.automation_settings:
        return AUTOMATION_DEFAULTS

    return {**AUTOMATION_DEFAULTS, **profile.automation_settings}


@router.put("/settings")
async def update_automation_settings(
    body: AutomationSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's automation settings."""
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()

    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)

    profile.automation_settings = body.model_dump()

    return profile.automation_settings


@router.post("/generate-comment", response_model=CommentGenerateResponse)
async def generate_comment(
    request: CommentGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a comment for a given post content (for testing/preview)."""
    # Load user profile
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()

    comment_result = await generate_and_review_comment(
        post_content=request.post_content,
        user_profile=profile.markdown_text if profile else "",
        tone_settings=profile.tone_settings if profile else None,
        page_tags=request.page_tags,
    )

    return CommentGenerateResponse(
        comments=comment_result["all_variants"],
        model_used=comment_result["llm_data"]["generation"]["model"],
        review_passed=comment_result["review_passed"],
        review_notes=comment_result.get("review_notes"),
    )
