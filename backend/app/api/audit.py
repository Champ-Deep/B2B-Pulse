import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.engagement import AuditLog, EngagementAction
from app.models.post import Post
from app.models.tracked_page import TrackedPage
from app.models.user import User
from app.schemas.engagement import ActivityFeedItem, AuditLogResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogResponse], summary="List Audit Logs")
async def list_audit_logs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    action: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """List audit logs for the current user's organization."""
    query = (
        select(AuditLog)
        .where(AuditLog.org_id == current_user.org_id)
        .order_by(AuditLog.created_at.desc())
    )

    if action:
        query = query.where(AuditLog.action == action)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/export", summary="Export Audit Logs as CSV")
async def export_audit_logs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
):
    """Export audit logs as a CSV file download."""
    query = (
        select(AuditLog)
        .where(AuditLog.org_id == current_user.org_id)
        .order_by(AuditLog.created_at.desc())
    )
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)

    result = await db.execute(query)
    logs = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "user_id", "action", "target_type", "target_id", "created_at"])
    for log in logs:
        writer.writerow(
            [
                str(log.id),
                str(log.user_id),
                log.action,
                log.target_type,
                log.target_id,
                log.created_at.isoformat(),
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


@router.get("/analytics/summary", summary="Get Analytics Summary")
async def get_analytics_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get engagement action counts grouped by type and status."""
    # Count engagement actions by type and status for the user's org
    result = await db.execute(
        select(
            EngagementAction.action_type,
            EngagementAction.status,
            func.count(EngagementAction.id),
        )
        .join(User, EngagementAction.user_id == User.id)
        .where(User.org_id == current_user.org_id)
        .group_by(EngagementAction.action_type, EngagementAction.status)
    )
    rows = result.all()

    summary = {"likes": {}, "comments": {}}
    for action_type, action_status, count in rows:
        key = "likes" if action_type.value == "like" else "comments"
        summary[key][action_status.value] = count

    return summary


@router.get("/recent-activity", response_model=list[ActivityFeedItem], summary="Get Recent Activity Feed")
async def get_recent_activity(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, le=50),
):
    """Get recent activity feed for the dashboard."""
    # Recent engagement actions with post and user info
    actions_result = await db.execute(
        select(EngagementAction, User.full_name, Post.url, TrackedPage.name)
        .join(User, EngagementAction.user_id == User.id)
        .join(Post, EngagementAction.post_id == Post.id)
        .join(TrackedPage, Post.tracked_page_id == TrackedPage.id)
        .where(User.org_id == current_user.org_id)
        .order_by(EngagementAction.created_at.desc())
        .limit(limit)
    )

    items = []
    for action, user_name, post_url, page_name in actions_result.all():
        items.append(ActivityFeedItem(
            type=f"{action.action_type.value}_{action.status.value}",
            user_name=user_name,
            post_url=post_url,
            page_name=page_name,
            timestamp=action.completed_at or action.created_at,
            comment_text=action.comment_text if action.action_type.value == "comment" else None,
            error=action.error_message if action.status.value == "failed" else None,
        ))

    return items
