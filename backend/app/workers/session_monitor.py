"""Session monitoring task — verify LinkedIn session cookies are still valid.

Architecture notes:
- Runs on a schedule (via Celery beat) to check all LinkedIn integrations
- Uses the existing check_session_valid function from linkedin_actions
- Updates is_active flag if session is invalid
- Updates last_session_check timestamp after each check
- Logs warnings for invalid sessions (could be extended to send email alerts)
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.automation.linkedin_actions import check_session_valid
from app.database import get_task_session
from app.models.integration import IntegrationAccount, Platform
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Minimum time between session checks (1 hour)
MIN_CHECK_INTERVAL = timedelta(hours=1)


@celery_app.task(
    name="app.workers.session_monitor.check_linkedin_sessions",
    soft_time_limit=300,
    time_limit=360,
)
def check_linkedin_sessions():
    """Beat task: check all LinkedIn session cookies are still valid."""
    asyncio.run(_check_sessions())


async def _check_sessions():
    async with get_task_session() as db:
        # Fetch all LinkedIn integrations with session cookies
        result = await db.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.platform == Platform.LINKEDIN,
                IntegrationAccount.session_cookies.isnot(None),
            )
        )
        integrations = result.scalars().all()

        logger.info(f"Checking {len(integrations)} LinkedIn sessions")

        invalid_count = 0
        valid_count = 0
        skipped_count = 0
        now = datetime.now(UTC)

        for integration in integrations:
            user_id = str(integration.user_id)

            # Skip if checked recently (within MIN_CHECK_INTERVAL)
            if integration.last_session_check:
                time_since_last_check = now - integration.last_session_check
                if time_since_last_check < MIN_CHECK_INTERVAL:
                    skipped_count += 1
                    logger.debug(f"Skipping {user_id} - checked recently")
                    continue

            try:
                is_valid = await check_session_valid(user_id)

                # Update last_session_check timestamp
                integration.last_session_check = now

                if is_valid:
                    valid_count += 1
                    logger.debug(f"Session valid for user {user_id}")
                    # Clear any prior needs_reconnect flag set by a previous run.
                    current_settings = integration.settings or {}
                    if current_settings.get("needs_reconnect"):
                        integration.settings = {**current_settings, "needs_reconnect": False}
                else:
                    invalid_count += 1
                    # Mark integration as inactive and flag for user-visible reconnect.
                    integration.is_active = False
                    current_settings = integration.settings or {}
                    integration.settings = {
                        **current_settings,
                        "needs_reconnect": True,
                        "reconnect_reason": "session_invalid",
                        "reconnect_flagged_at": now.isoformat(),
                    }
                    logger.warning(f"Session expired for user {user_id}, needs_reconnect=true")

            except Exception as e:
                # Still update last_session_check even on error
                integration.last_session_check = now
                logger.error(f"Error checking session for user {user_id}: {e}")

        await db.commit()
        logger.info(
            f"Session check complete: {valid_count} valid, {invalid_count} invalid, {skipped_count} skipped"
        )
