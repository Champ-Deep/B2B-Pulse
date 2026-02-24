from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.invite import InviteStatus, OrgInvite
from app.models.org import Org
from app.models.tracked_page import PollingMode, TrackedPage, TrackedPageSubscription
from app.models.user import User, UserProfile, UserRole
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(request: SignupRequest, db: AsyncSession = Depends(get_db)):
    # Check if email already exists
    result = await db.execute(select(User).where(User.email == request.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    org = None
    role = UserRole.OWNER
    invite = None

    if request.invite_code:
        # --- Join existing org via invite ---
        inv_result = await db.execute(
            select(OrgInvite).where(OrgInvite.invite_code == request.invite_code)
        )
        invite = inv_result.scalar_one_or_none()
        if not invite or invite.status != InviteStatus.PENDING:
            raise HTTPException(status_code=400, detail="Invalid or expired invite")
        if invite.expires_at < datetime.now(UTC):
            invite.status = InviteStatus.EXPIRED
            raise HTTPException(status_code=400, detail="Invite has expired")
        if invite.email and invite.email.lower() != request.email.lower():
            raise HTTPException(
                status_code=400,
                detail="This invite was sent to a different email address",
            )

        org_result = await db.execute(select(Org).where(Org.id == invite.org_id))
        org = org_result.scalar_one()
        role = UserRole.MEMBER

        invite.status = InviteStatus.ACCEPTED
        invite.accepted_at = datetime.now(UTC)
    else:
        # --- Create new org ---
        if not request.org_name:
            raise HTTPException(
                status_code=400,
                detail="Organization name is required for new signups",
            )
        org = Org(name=request.org_name)
        db.add(org)
        await db.flush()

    # Create user
    user = User(
        org_id=org.id,
        email=request.email,
        hashed_password=hash_password(request.password),
        full_name=request.full_name,
        role=role,
    )
    db.add(user)
    await db.flush()

    if invite:
        invite.accepted_by = user.id

    # Create empty user profile
    profile = UserProfile(user_id=user.id)
    db.add(profile)

    # Auto-subscribe invited members to all active tracked pages
    if request.invite_code:
        pages_result = await db.execute(
            select(TrackedPage).where(
                TrackedPage.org_id == org.id,
                TrackedPage.active.is_(True),
            )
        )
        for page in pages_result.scalars().all():
            db.add(
                TrackedPageSubscription(
                    tracked_page_id=page.id,
                    user_id=user.id,
                    auto_like=True,
                    auto_comment=False,
                    polling_mode=PollingMode.NORMAL,
                )
            )

    # Generate tokens
    token_data = {"sub": str(user.id), "org_id": str(org.id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    token_data = {"sub": str(user.id), "org_id": str(user.org_id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(request.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    token_data = {"sub": str(user.id), "org_id": str(user.org_id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
