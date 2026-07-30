"""
Identity routes.

Clerk is the identity provider. It issues and refreshes session tokens on the
client, so there is no login endpoint and no token-refresh endpoint here — the
backend only ever *verifies* a token it is handed.

What used to live here (LinkedIn OAuth login) has moved down a layer: LinkedIn
OAuth is still used, but purely to connect an integration, under
``/api/integrations/linkedin/*``. Splitting the two is the trade we accepted
when choosing Clerk. It costs a second step during onboarding and buys a login
that survives a LinkedIn session expiring, works for admins who never connect
an account, and keeps identity independent of the platform we automate.

Invites changed shape as a result. They used to ride along inside the OAuth
state; now a user signs in with Clerk first and redeems the code explicitly.
That is strictly more forgiving — an invite arriving *after* someone signed up
used to be unusable.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk import RequestContext, get_request_context
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.invite import InviteStatus, OrgInvite
from app.models.org import Org
from app.models.tracked_page import PollingMode, TrackedPage, TrackedPageSubscription
from app.models.user import User, UserProfile
from app.schemas.auth import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _aware(value: datetime) -> datetime:
    """
    Treat a naive timestamp as UTC.

    Postgres hands back timezone-aware values, but SQLite (and any row written
    before the column was tz-aware) does not. Comparing the two raises, so
    every expiry check goes through here rather than trusting the driver.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RedeemInviteRequest(BaseModel):
    invite_code: str = Field(..., min_length=8, max_length=64)


class RedeemInviteResponse(BaseModel):
    org_id: str
    org_name: str
    team_id: str | None = None
    subscribed_pages: int = 0


@router.get("/me", response_model=UserResponse, summary="Get current user")
async def get_me(current_user: User = Depends(get_current_user)):
    """
    The authenticated user.

    Also the provisioning endpoint: the first call for a new Clerk user creates
    the local User (and its Org, or a personal workspace) as a side effect of
    resolving the token, so the client calls this immediately after sign-in.
    """
    return current_user


@router.get("/invite/{invite_code}", summary="Preview an invite before signing in")
async def preview_invite(invite_code: str, db: AsyncSession = Depends(get_db)):
    """
    Show what an invite is for, without requiring authentication.

    Lets the sign-in page say "Join Acme's workspace" rather than asking
    someone to authenticate on faith.
    """
    invite = (
        await db.execute(select(OrgInvite).where(OrgInvite.invite_code == invite_code))
    ).scalar_one_or_none()

    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")

    org = (await db.execute(select(Org).where(Org.id == invite.org_id))).scalar_one()
    expired = _aware(invite.expires_at) < datetime.now(UTC)

    return {
        "org_name": org.name,
        "email": invite.email,
        "valid": invite.status == InviteStatus.PENDING and not expired,
        "status": "expired" if expired else invite.status.value,
    }


@router.post("/redeem-invite", response_model=RedeemInviteResponse)
async def redeem_invite(
    body: RedeemInviteRequest,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
):
    """Join an organization using an invite code, after signing in with Clerk."""
    invite = (
        await db.execute(
            select(OrgInvite).where(OrgInvite.invite_code == body.invite_code)
        )
    ).scalar_one_or_none()

    if invite is None or invite.status != InviteStatus.PENDING:
        raise HTTPException(status_code=404, detail="Invite not found or already used")

    if _aware(invite.expires_at) < datetime.now(UTC):
        invite.status = InviteStatus.EXPIRED
        await db.commit()
        raise HTTPException(status_code=410, detail="This invite has expired")

    user = ctx.user
    if invite.email and user.email and invite.email.lower() != user.email.lower():
        raise HTTPException(
            status_code=403,
            detail=(
                f"This invite was issued to {invite.email}. "
                f"Sign in with that address to accept it."
            ),
        )

    previous_org_id = user.org_id
    user.org_id = invite.org_id
    if invite.team_id:
        user.team_id = invite.team_id

    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = datetime.now(UTC)
    invite.accepted_by = user.id

    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        db.add(UserProfile(user_id=user.id))

    # Auto-subscribe the new member to the org's active tracked pages, matching
    # what the old OAuth invite flow did.
    pages = list(
        (
            await db.execute(
                select(TrackedPage).where(
                    TrackedPage.org_id == invite.org_id,
                    TrackedPage.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    already = set(
        (
            await db.execute(
                select(TrackedPageSubscription.tracked_page_id).where(
                    TrackedPageSubscription.user_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )

    subscribed = 0
    for page in pages:
        if page.id in already:
            continue
        db.add(
            TrackedPageSubscription(
                tracked_page_id=page.id,
                user_id=user.id,
                auto_like=True,
                auto_comment=True,
                polling_mode=PollingMode.NORMAL,
            )
        )
        subscribed += 1

    org = (await db.execute(select(Org).where(Org.id == invite.org_id))).scalar_one()

    await db.commit()
    logger.info(
        "User %s redeemed invite into org %s (previously %s)",
        user.id, invite.org_id, previous_org_id,
    )

    return RedeemInviteResponse(
        org_id=str(org.id),
        org_name=org.name,
        team_id=str(invite.team_id) if invite.team_id else None,
        subscribed_pages=subscribed,
    )
