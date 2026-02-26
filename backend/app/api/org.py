import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INVITE_EXPIRY_DAYS, settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.integration import IntegrationAccount
from app.models.invite import InviteStatus, OrgInvite
from app.models.org import Org
from app.models.team import Team
from app.models.user import User, UserRole
from app.schemas.invite import (
    InviteCreateRequest,
    InviteResponse,
    InviteValidateResponse,
    OrgMemberResponse,
)

router = APIRouter(prefix="/org", tags=["org"])


def _require_admin(user: User) -> None:
    if user.role not in (UserRole.OWNER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can perform this action",
        )


def _frontend_url() -> str:
    return settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"


def _build_invite_response(invite: OrgInvite, team_name: str | None = None) -> InviteResponse:
    return InviteResponse(
        id=invite.id,
        org_id=invite.org_id,
        email=invite.email,
        invite_code=invite.invite_code,
        status=invite.status.value,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        invite_url=f"{_frontend_url()}/signup?invite={invite.invite_code}",
        team_id=invite.team_id,
        team_name=team_name,
    )


@router.post("/invites", response_model=InviteResponse, status_code=201, summary="Create Invite")
async def create_invite(
    request: InviteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an org invite link, optionally targeting a specific team."""
    _require_admin(current_user)

    team_name = None
    if request.team_id:
        team_result = await db.execute(
            select(Team).where(Team.id == request.team_id, Team.org_id == current_user.org_id)
        )
        team = team_result.scalar_one_or_none()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        team_name = team.name

    invite = OrgInvite(
        org_id=current_user.org_id,
        invited_by=current_user.id,
        email=str(request.email) if request.email else None,
        invite_code=secrets.token_hex(16),
        status=InviteStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_EXPIRY_DAYS),
        team_id=request.team_id,
    )
    db.add(invite)
    await db.flush()

    return _build_invite_response(invite, team_name=team_name)


@router.get("/invites", response_model=list[InviteResponse], summary="List Invites")
async def list_invites(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all invites for the current user's organization."""
    _require_admin(current_user)
    result = await db.execute(
        select(OrgInvite)
        .where(OrgInvite.org_id == current_user.org_id)
        .order_by(OrgInvite.created_at.desc())
    )
    invites = result.scalars().all()

    # Resolve team names
    team_ids = {inv.team_id for inv in invites if inv.team_id}
    team_names: dict = {}
    if team_ids:
        teams_result = await db.execute(select(Team).where(Team.id.in_(team_ids)))
        team_names = {t.id: t.name for t in teams_result.scalars().all()}

    return [_build_invite_response(inv, team_name=team_names.get(inv.team_id)) for inv in invites]


@router.delete("/invites/{invite_id}", status_code=204, summary="Revoke Invite")
async def revoke_invite(
    invite_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a pending org invite."""
    _require_admin(current_user)
    result = await db.execute(
        select(OrgInvite).where(
            OrgInvite.id == invite_id,
            OrgInvite.org_id == current_user.org_id,
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != InviteStatus.PENDING:
        raise HTTPException(status_code=400, detail="Can only revoke pending invites")

    invite.status = InviteStatus.REVOKED


@router.get("/invites/validate/{invite_code}", response_model=InviteValidateResponse, summary="Validate Invite Code")
async def validate_invite(
    invite_code: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint (no auth) — validates invite code for the signup page."""
    result = await db.execute(select(OrgInvite).where(OrgInvite.invite_code == invite_code))
    invite = result.scalar_one_or_none()

    if not invite or invite.status != InviteStatus.PENDING:
        return InviteValidateResponse(valid=False)
    if invite.expires_at < datetime.now(UTC):
        return InviteValidateResponse(valid=False)

    org_result = await db.execute(select(Org).where(Org.id == invite.org_id))
    org = org_result.scalar_one()

    team_name = None
    if invite.team_id:
        team_result = await db.execute(select(Team).where(Team.id == invite.team_id))
        team = team_result.scalar_one_or_none()
        if team:
            team_name = team.name

    return InviteValidateResponse(
        valid=True,
        org_name=org.name,
        email=invite.email,
        team_name=team_name,
    )


@router.get("/members", response_model=list[OrgMemberResponse], summary="List Org Members")
async def list_members(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all org members with their integration status."""
    result = await db.execute(
        select(User).where(User.org_id == current_user.org_id).order_by(User.created_at)
    )
    members = result.scalars().all()

    # Resolve team names for all members
    team_ids = {m.team_id for m in members if m.team_id}
    team_names: dict = {}
    if team_ids:
        teams_result = await db.execute(select(Team).where(Team.id.in_(team_ids)))
        team_names = {t.id: t.name for t in teams_result.scalars().all()}

    response = []
    for member in members:
        int_result = await db.execute(
            select(IntegrationAccount.platform).where(
                IntegrationAccount.user_id == member.id,
                IntegrationAccount.is_active.is_(True),
            )
        )
        platforms = [row[0].value for row in int_result.all()]

        response.append(
            OrgMemberResponse(
                id=member.id,
                email=member.email,
                full_name=member.full_name,
                role=member.role.value,
                is_active=member.is_active,
                created_at=member.created_at,
                integrations=platforms,
                team_id=member.team_id,
                team_name=team_names.get(member.team_id),
            )
        )

    return response


@router.delete("/members/{user_id}", status_code=204, summary="Remove Org Member")
async def remove_member(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an org member (admin/owner only)."""
    _require_admin(current_user)

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.org_id == current_user.org_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == UserRole.OWNER:
        raise HTTPException(status_code=400, detail="Cannot remove the org owner")

    member.is_active = False
