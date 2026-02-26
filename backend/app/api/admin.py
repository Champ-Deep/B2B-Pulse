import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_platform_admin
from app.database import get_db
from app.models.engagement import EngagementAction
from app.models.integration import IntegrationAccount
from app.models.org import Org
from app.models.team import Team
from app.models.tracked_page import TrackedPage
from app.models.user import User
from app.schemas.admin import OrgDetail, OrgSummary, PlatformStats

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/orgs", response_model=list[OrgSummary], summary="List all organizations")
async def list_orgs(
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all organizations with member and team counts (platform admin only)."""
    orgs_result = await db.execute(select(Org).order_by(Org.created_at))
    orgs = orgs_result.scalars().all()

    response = []
    for org in orgs:
        member_count = (
            await db.execute(
                select(func.count()).select_from(User).where(User.org_id == org.id, User.is_active.is_(True))
            )
        ).scalar() or 0

        team_count = (
            await db.execute(
                select(func.count()).select_from(Team).where(Team.org_id == org.id)
            )
        ).scalar() or 0

        response.append(
            OrgSummary(
                id=org.id,
                name=org.name,
                member_count=member_count,
                team_count=team_count,
                created_at=org.created_at,
            )
        )

    return response


@router.get("/orgs/{org_id}", response_model=OrgDetail, summary="Get organization details")
async def get_org_detail(
    org_id: uuid.UUID,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific organization (platform admin only)."""
    org_result = await db.execute(select(Org).where(Org.id == org_id))
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    member_count = (
        await db.execute(
            select(func.count()).select_from(User).where(User.org_id == org_id, User.is_active.is_(True))
        )
    ).scalar() or 0

    team_count = (
        await db.execute(
            select(func.count()).select_from(Team).where(Team.org_id == org_id)
        )
    ).scalar() or 0

    active_integrations = (
        await db.execute(
            select(func.count()).select_from(IntegrationAccount).join(User).where(
                User.org_id == org_id,
                IntegrationAccount.is_active.is_(True),
            )
        )
    ).scalar() or 0

    tracked_pages_count = (
        await db.execute(
            select(func.count()).select_from(TrackedPage).where(TrackedPage.org_id == org_id)
        )
    ).scalar() or 0

    return OrgDetail(
        id=org.id,
        name=org.name,
        member_count=member_count,
        team_count=team_count,
        active_integrations=active_integrations,
        tracked_pages_count=tracked_pages_count,
        created_at=org.created_at,
    )


@router.get("/stats", response_model=PlatformStats, summary="Platform-wide statistics")
async def get_platform_stats(
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get platform-wide statistics (platform admin only)."""
    total_orgs = (await db.execute(select(func.count()).select_from(Org))).scalar() or 0
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    active_users = (
        await db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True)))
    ).scalar() or 0
    total_engagements = (
        await db.execute(select(func.count()).select_from(EngagementAction))
    ).scalar() or 0
    active_integrations = (
        await db.execute(
            select(func.count()).select_from(IntegrationAccount).where(IntegrationAccount.is_active.is_(True))
        )
    ).scalar() or 0

    return PlatformStats(
        total_orgs=total_orgs,
        total_users=total_users,
        active_users=active_users,
        total_engagements=total_engagements,
        active_integrations=active_integrations,
    )
