import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.engagement import AIAvoidPhrase
from app.models.user import User, UserProfile
from app.schemas.engagement import CommentGenerateRequest, CommentGenerateResponse
from app.services.comment_generator import generate_and_review_comment

router = APIRouter(prefix="/automation", tags=["automation"])

AUTOMATION_DEFAULTS = {
    "risk_profile": "safe",
    "quiet_hours_enabled": True,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
    "polling_interval": 300,
}


class AutomationSettingsUpdate(BaseModel):
    risk_profile: Literal["safe", "aggro"] = "safe"
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = Field("22:00", pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str = Field("07:00", pattern=r"^\d{2}:\d{2}$")
    polling_interval: int = Field(300, ge=60, le=3600)


@router.get("/settings", summary="Get Automation Settings")
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


@router.put("/settings", summary="Update Automation Settings")
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


@router.post("/generate-comment", response_model=CommentGenerateResponse, summary="Generate Comment Preview")
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


# --- Avoid Phrases CRUD ---


class AvoidPhraseCreate(BaseModel):
    phrase: str = Field(..., min_length=1, max_length=500)


class AvoidPhraseResponse(BaseModel):
    id: uuid.UUID
    phrase: str
    active: bool

    model_config = {"from_attributes": True}


@router.get("/avoid-phrases", response_model=list[AvoidPhraseResponse], summary="List Avoid Phrases")
async def list_avoid_phrases(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List custom avoid phrases for the current user's org."""
    result = await db.execute(
        select(AIAvoidPhrase)
        .where(AIAvoidPhrase.org_id == current_user.org_id, AIAvoidPhrase.active.is_(True))
        .order_by(AIAvoidPhrase.created_at.desc())
    )
    return result.scalars().all()


@router.post("/avoid-phrases", response_model=AvoidPhraseResponse, status_code=201, summary="Create Avoid Phrase")
async def create_avoid_phrase(
    body: AvoidPhraseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a custom avoid phrase for the org (admin/owner only)."""
    if current_user.role.value not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only admins can manage writing rules")

    # Check for duplicate
    existing = await db.execute(
        select(AIAvoidPhrase).where(
            AIAvoidPhrase.org_id == current_user.org_id,
            AIAvoidPhrase.phrase == body.phrase.strip(),
            AIAvoidPhrase.active.is_(True),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This phrase already exists")

    phrase = AIAvoidPhrase(org_id=current_user.org_id, phrase=body.phrase.strip())
    db.add(phrase)
    await db.commit()
    return phrase


@router.delete("/avoid-phrases/{phrase_id}", status_code=204, summary="Delete Avoid Phrase")
async def delete_avoid_phrase(
    phrase_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a custom avoid phrase (admin/owner only)."""
    if current_user.role.value not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only admins can manage writing rules")

    result = await db.execute(
        select(AIAvoidPhrase).where(
            AIAvoidPhrase.id == phrase_id, AIAvoidPhrase.org_id == current_user.org_id
        )
    )
    phrase = result.scalar_one_or_none()
    if not phrase:
        raise HTTPException(status_code=404, detail="Phrase not found")

    phrase.active = False
    await db.commit()
