from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.user import User, UserProfile
from app.schemas.user import UserProfileResponse, UserProfileUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        return UserProfileResponse(markdown_text=None, tone_settings=None)
    return profile


@router.put("/profile", response_model=UserProfileResponse)
async def update_profile(
    request: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)

    if request.markdown_text is not None:
        profile.markdown_text = request.markdown_text
    if request.tone_settings is not None:
        profile.tone_settings = request.tone_settings

    await db.commit()
    return profile
