"""Cleanup task — detect and retry/fail stale engagement actions.

Actions stuck in 'pending' for > 30 minutes or 'in_progress' for > 10 minutes
are re-queued or marked as failed.

Also retries FAILED actions with exponential backoff:
- Retry 1: 5 minutes delay
- Retry 2: 10 minutes delay
- Retry 3: 15 minutes delay

Permanent failures (button not found, already liked) are NOT retried.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

PENDING_STALE_MINUTES = 30
IN_PROGRESS_STALE_MINUTES = 10

# Max retries before giving up
MAX_RETRIES = 3

# Exponential backoff delays in seconds
RETRY_DELAYS = {
    1: 5 * 60,  # 5 minutes
    2: 10 * 60,  # 10 minutes
    3: 15 * 60,  # 15 minutes
}

# Error patterns that indicate permanent failures - do NOT retry
PERMANENT_FAILURE_PATTERNS = [
    "button not found",
    "comment box not found",
    "already liked",
    "not found",
    "could not be completed",
    "Like button not found",
    "Comment box not found",
]


def is_permanent_failure(error_message: str | None) -> bool:
    """Check if error indicates a permanent failure that shouldn't be retried."""
    if not error_message:
        return False
    error_lower = error_message.lower()
    return any(pattern.lower() in error_lower for pattern in PERMANENT_FAILURE_PATTERNS)


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
            action.retry_count += 1
            action.last_retry_at = now
            failed += 1

        # 3. Retry FAILED actions with exponential backoff
        #    - Only retry if retry_count < MAX_RETRIES
        #    - Don't retry permanent failures
        #    - Wait for retry delay to pass since last_retry_at
        result = await db.execute(
            select(EngagementAction).where(
                EngagementAction.status == ActionStatus.FAILED,
                EngagementAction.retry_count < MAX_RETRIES,
            )
        )
        failed_actions = result.scalars().all()

        retried = 0
        for action in failed_actions:
            # Skip permanent failures
            if is_permanent_failure(action.error_message):
                logger.debug(
                    f"Skipping retry for {action.id} - permanent failure: {action.error_message}"
                )
                continue

            # Check if enough time has passed since last retry
            if action.last_retry_at:
                retry_delay = RETRY_DELAYS.get(action.retry_count + 1, RETRY_DELAYS[3])
                next_retry_time = action.last_retry_at + timedelta(seconds=retry_delay)
                if now < next_retry_time:
                    logger.debug(f"Skipping retry for {action.id} - waiting for backoff delay")
                    continue

            # Re-queue for retry
            action.status = ActionStatus.PENDING
            action.attempted_at = None
            action.error_message = None

            from app.workers.engagement_tasks import execute_engagement

            # Apply countdown based on retry count (exponential backoff)
            retry_delay = RETRY_DELAYS.get(action.retry_count + 1, RETRY_DELAYS[3])
            execute_engagement.apply_async(args=[str(action.id)], countdown=retry_delay)
            retried += 1

        await db.commit()

        if requeued or failed or retried:
            logger.info(
                f"Stale action cleanup: re-queued {requeued} pending, "
                f"failed {failed} in-progress, retried {retried} failed"
            )
