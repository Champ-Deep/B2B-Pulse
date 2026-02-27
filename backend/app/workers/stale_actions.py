"""Cleanup task — detect and retry/fail stale engagement actions.

Actions stuck in 'pending' for > 30 minutes or 'in_progress' for > 10 minutes
are re-queued or marked as failed.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

PENDING_STALE_MINUTES = 30
IN_PROGRESS_STALE_MINUTES = 10


@celery_app.task(
    name="app.workers.stale_actions.cleanup_stale_actions",
    soft_time_limit=60,
    time_limit=90,
)
def cleanup_stale_actions():
    """Beat task: find and recover stale engagement actions."""
    asyncio.run(_cleanup())


async def _cleanup():
    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.engagement import ActionStatus, EngagementAction

    now = datetime.now(UTC)
    pending_cutoff = now - timedelta(minutes=PENDING_STALE_MINUTES)
    in_progress_cutoff = now - timedelta(minutes=IN_PROGRESS_STALE_MINUTES)

    async with get_task_session() as db:
        # 1. Re-queue stale PENDING actions (never attempted)
        result = await db.execute(
            select(EngagementAction).where(
                EngagementAction.status == ActionStatus.PENDING,
                EngagementAction.attempted_at.is_(None),
                EngagementAction.created_at < pending_cutoff,
            )
        )
        stale_pending = result.scalars().all()

        requeued = 0
        for action in stale_pending:
            from app.workers.engagement_tasks import execute_engagement

            execute_engagement.delay(str(action.id))
            requeued += 1

        # 2. Fail stale IN_PROGRESS actions (attempted but never completed)
        result = await db.execute(
            select(EngagementAction).where(
                EngagementAction.status == ActionStatus.IN_PROGRESS,
                EngagementAction.attempted_at < in_progress_cutoff,
            )
        )
        stale_in_progress = result.scalars().all()

        failed = 0
        for action in stale_in_progress:
            action.status = ActionStatus.FAILED
            action.error_message = "Timed out — worker may have crashed during execution"
            failed += 1

        await db.commit()

        if requeued or failed:
            logger.info(
                f"Stale action cleanup: re-queued {requeued} pending, failed {failed} in-progress"
            )
