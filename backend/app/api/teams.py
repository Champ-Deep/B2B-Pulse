import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INVITE_EXPIRY_DAYS, settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.invite import InviteStatus, OrgInvite
from app.models.team import Team
from app.models.user import User, UserRole
from app.schemas.invite import InviteCreateRequest, InviteResponse
from app.schemas.team import TeamAssignRequest, TeamCreateRequest, TeamResponse, TeamUpdateRequest

router = APIRouter(prefix="/org/teams", tags=["teams"])

ADMIN_ROLES = (UserRole.OWNER, UserRole.ADMIN)


def _require_admin_or_team_leader(user: User, team_id: uuid.UUID) -> None:
    """Allow OWNER/ADMIN always, or TEAM_LEADER only for their own team."""
    if user.role in ADMIN_ROLES:
        return
    if user.role == UserRole.TEAM_LEADER and user.team_id == team_id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions for this team",
    )


def _frontend_url() -> str:
    return settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"


# ---------------------------------------------------------------------------
# Team CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=TeamResponse, status_code=201, summary="Create team")
async def create_team(
    request: TeamCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new team within the current user's organization."""
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only admins can create teams")

    team = Team(org_id=current_user.org_id, name=request.name)
    db.add(team)
    await db.flush()

    return TeamResponse(
        id=team.id,
        org_id=team.org_id,
        name=team.name,
        member_count=0,
        created_at=team.created_at,
    )


@router.get("", response_model=list[TeamResponse], summary="List teams")
async def list_teams(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all teams in the current user's organization with member counts."""
    teams_result = await db.execute(
        select(Team).where(Team.org_id == current_user.org_id).order_by(Team.name)
    )
    teams = teams_result.scalars().all()

    response = []
    for team in teams:
        count_result = await db.execute(
            select(func.count()).select_from(User).where(
                User.team_id == team.id,
                User.is_active.is_(True),
            )
        )
        member_count = count_result.scalar() or 0

        response.append(
            TeamResponse(
                id=team.id,
                org_id=team.org_id,
                name=team.name,
                member_count=member_count,
                created_at=team.created_at,
            )
        )

    return response


@router.put("/{team_id}", response_model=TeamResponse, summary="Update team")
async def update_team(
    team_id: uuid.UUID,
    request: TeamUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a team. Accessible to admins and the team's leader."""
    _require_admin_or_team_leader(current_user, team_id)

    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.org_id == current_user.org_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    team.name = request.name

    count_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.team_id == team.id,
            User.is_active.is_(True),
        )
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=team.id,
        org_id=team.org_id,
        name=team.name,
        member_count=member_count,
        created_at=team.created_at,
    )


@router.delete("/{team_id}", status_code=204, summary="Delete team")
async def delete_team(
    team_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a team. Members are unassigned (team_id set to NULL)."""
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only admins can delete teams")

    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.org_id == current_user.org_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Unassign all members
    members_result = await db.execute(select(User).where(User.team_id == team_id))
    for member in members_result.scalars().all():
        member.team_id = None

    await db.delete(team)


# ---------------------------------------------------------------------------
# Team-specific invites
# ---------------------------------------------------------------------------


@router.post("/{team_id}/invite", response_model=InviteResponse, status_code=201, summary="Create team invite")
async def create_team_invite(
    team_id: uuid.UUID,
    request: InviteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an invite link tied to a specific team."""
    _require_admin_or_team_leader(current_user, team_id)

    # Verify team exists in this org
    team_result = await db.execute(
        select(Team).where(Team.id == team_id, Team.org_id == current_user.org_id)
    )
    if not team_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Team not found")

    invite = OrgInvite(
        org_id=current_user.org_id,
        invited_by=current_user.id,
        email=str(request.email) if request.email else None,
        invite_code=secrets.token_hex(16),
        status=InviteStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_EXPIRY_DAYS),
        team_id=team_id,
    )
    db.add(invite)
    await db.flush()

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
    )


# ---------------------------------------------------------------------------
# Team member assignment
# ---------------------------------------------------------------------------


@router.put("/members/{user_id}/team", status_code=200, summary="Assign member to team")
async def assign_member_to_team(
    user_id: uuid.UUID,
    request: TeamAssignRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign or move a member to a team (or unassign with team_id=null)."""
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only admins can assign members to teams")

    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == current_user.org_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if request.team_id:
        team_result = await db.execute(
            select(Team).where(Team.id == request.team_id, Team.org_id == current_user.org_id)
        )
        if not team_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Team not found")

    member.team_id = request.team_id
    return {"status": "ok", "user_id": str(user_id), "team_id": str(request.team_id) if request.team_id else None}
