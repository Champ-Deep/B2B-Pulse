"""Engagement task workers — like and comment on social-media posts.

Architecture notes:
- Each Celery task uses asyncio.run() which creates a *new* event loop, so the
  web server's SQLAlchemy session factory cannot be reused.  All DB access
  inside async helpers must go through get_task_session() which builds a fresh
  engine per invocation.
- Imports of app models / services are deferred inside the async helpers to
  avoid circular imports and to keep Celery's module-loading lightweight.
- Retry strategy: network-level errors (timeout, connect) and Celery
  SoftTimeLimitExceeded are treated as retriable.  All other exceptions are
  considered fatal and logged without retry.
- Stagger algorithm: likes are scheduled with small random delays
  (LIKE_STAGGER_MIN..MAX); comments get longer delays
  (COMMENT_STAGGER_MIN..MAX) plus an additional per-user offset
  (COMMENT_INTER_USER_DELAY * user_index) to avoid a burst of AI comments.
"""

import asyncio
import logging
import random
from datetime import UTC, datetime

import httpx
from celery.exceptions import SoftTimeLimitExceeded

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.engagement_tasks.schedule_staggered_engagements",
    soft_time_limit=120,
    time_limit=180,
)
def schedule_staggered_engagements(post_id: str, tracked_page_id: str):
    """Create engagement actions for all subscribed users and stagger their execution."""
    asyncio.run(_schedule_engagements(post_id, tracked_page_id))


async def _schedule_engagements(post_id: str, tracked_page_id: str):
    import uuid

    from sqlalchemy import func, select

    from app.database import get_task_session
    from app.models.engagement import ActionStatus, ActionType, EngagementAction
    from app.models.tracked_page import TrackedPageSubscription
    from app.models.user import UserProfile

    async with get_task_session() as db:
        # Get all subscriptions for this page
        result = await db.execute(
            select(TrackedPageSubscription).where(
                TrackedPageSubscription.tracked_page_id == uuid.UUID(tracked_page_id)
            )
        )
        subscriptions = result.scalars().all()

        now = datetime.now(UTC)
        is_weekend = now.weekday() >= 5  # Saturday=5, Sunday=6

        for i, sub in enumerate(subscriptions):
            # Load user's automation settings for risk profile and quiet hours
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.user_id == sub.user_id)
            )
            profile = profile_result.scalar_one_or_none()
            auto_settings = (profile.automation_settings if profile else None) or {}
            risk = auto_settings.get("risk_profile", "safe")

            # --- Quiet hours offset ---
            quiet_offset = 0
            if auto_settings.get("quiet_hours_enabled", True):
                quiet_offset = _quiet_hours_offset(
                    now,
                    auto_settings.get("quiet_hours_start", "22:00"),
                    auto_settings.get("quiet_hours_end", "07:00"),
                )

            # --- Daily cap check ---
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_likes = (
                await db.execute(
                    select(func.count(EngagementAction.id)).where(
                        EngagementAction.user_id == sub.user_id,
                        EngagementAction.action_type == ActionType.LIKE,
                        EngagementAction.created_at >= today_start,
                    )
                )
            ).scalar() or 0
            today_comments = (
                await db.execute(
                    select(func.count(EngagementAction.id)).where(
                        EngagementAction.user_id == sub.user_id,
                        EngagementAction.action_type == ActionType.COMMENT,
                        EngagementAction.created_at >= today_start,
                    )
                )
            ).scalar() or 0

            like_cap = 150 if risk == "aggro" else 50
            comment_cap = 60 if risk == "aggro" else 20

            # --- Create like action ---
            if sub.auto_like and today_likes < like_cap:
                like_action = EngagementAction(
                    post_id=uuid.UUID(post_id),
                    user_id=sub.user_id,
                    action_type=ActionType.LIKE,
                    status=ActionStatus.PENDING,
                )
                db.add(like_action)
                await db.flush()

                if risk == "aggro":
                    delay = random.randint(1, 2) * (i + 1)
                else:
                    from app.config import LIKE_STAGGER_MAX, LIKE_STAGGER_MIN

                    delay = random.randint(LIKE_STAGGER_MIN, LIKE_STAGGER_MAX) * (i + 1)
                    if is_weekend:
                        delay *= 2  # Weekend dampening

                delay += quiet_offset
                execute_engagement.apply_async(
                    args=[str(like_action.id)],
                    countdown=delay,
                )

            # --- Create comment action ---
            if sub.auto_comment and today_comments < comment_cap:
                comment_action = EngagementAction(
                    post_id=uuid.UUID(post_id),
                    user_id=sub.user_id,
                    action_type=ActionType.COMMENT,
                    status=ActionStatus.PENDING,
                )
                db.add(comment_action)
                await db.flush()

                if risk == "aggro":
                    delay = random.randint(15, 60) + (i * 15)
                else:
                    from app.config import (
                        COMMENT_INTER_USER_DELAY,
                        COMMENT_STAGGER_MAX,
                        COMMENT_STAGGER_MIN,
                    )

                    delay = random.randint(COMMENT_STAGGER_MIN, COMMENT_STAGGER_MAX) + (
                        i * COMMENT_INTER_USER_DELAY
                    )
                    if is_weekend:
                        delay *= 2  # Weekend dampening

                delay += quiet_offset
                execute_engagement.apply_async(
                    args=[str(comment_action.id)],
                    countdown=delay,
                )

        await db.commit()
        logger.info(f"Scheduled {len(subscriptions)} engagement sets for post {post_id}")


def _quiet_hours_offset(now: datetime, start_str: str, end_str: str) -> int:
    """Calculate seconds until quiet hours end, or 0 if not in quiet hours."""
    try:
        sh, sm = int(start_str[:2]), int(start_str[3:5])
        eh, em = int(end_str[:2]), int(end_str[3:5])
    except (ValueError, IndexError):
        return 0

    current_minutes = now.hour * 60 + now.minute
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em

    in_quiet = False
    if start_minutes > end_minutes:
        # Wraps midnight (e.g. 22:00 - 07:00)
        in_quiet = current_minutes >= start_minutes or current_minutes < end_minutes
    else:
        # Same day (e.g. 01:00 - 06:00)
        in_quiet = start_minutes <= current_minutes < end_minutes

    if not in_quiet:
        return 0

    # Calculate seconds until end of quiet hours
    if current_minutes < end_minutes:
        return (end_minutes - current_minutes) * 60
    else:
        # Past midnight wrap: minutes until midnight + end_minutes
        return ((24 * 60 - current_minutes) + end_minutes) * 60


@celery_app.task(
    name="app.workers.engagement_tasks.execute_engagement",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=360,
)
def execute_engagement(self, engagement_action_id: str):
    """Execute a single engagement action (like or comment)."""
    from app.core.locks import acquire_user_lock

    # Pre-lock lookup: get user_id and platform so we can acquire a per-user lock.
    # This must use asyncio.run() because get_task_session() is async.
    lookup = asyncio.run(_lookup_engagement_meta(engagement_action_id))
    if lookup is None:
        logger.error(f"Engagement action {engagement_action_id} not found or missing post")
        return

    user_id, platform_value = lookup

    # Determine lock action based on platform
    lock_action = f"engagement_{platform_value}"

    # Try to acquire lock (non-blocking)
    lock = acquire_user_lock(str(user_id), lock_action)
    if not lock:
        logger.info(f"User {user_id} is busy with {lock_action}, will retry")
        raise self.retry(countdown=30)  # Retry after 30 seconds

    try:
        asyncio.run(_execute_engagement(engagement_action_id))
    except (httpx.TimeoutException, httpx.ConnectError, SoftTimeLimitExceeded) as e:
        logger.warning(f"Retriable error for {engagement_action_id}: {e}")
        raise self.retry(exc=e) from e
    except Exception as e:
        logger.error(f"Engagement execution failed for {engagement_action_id}: {e}")
        raise
    finally:
        lock.release()


async def _lookup_engagement_meta(engagement_action_id: str):
    """Async helper to fetch user_id and platform for an engagement action.

    Returns (user_id, platform_value) or None if not found.
    """
    import uuid

    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.engagement import EngagementAction
    from app.models.post import Post

    async with get_task_session() as db:
        result = await db.execute(
            select(EngagementAction.user_id, EngagementAction.post_id).where(
                EngagementAction.id == uuid.UUID(engagement_action_id)
            )
        )
        action_data = result.one_or_none()
        if not action_data:
            return None
        user_id, post_id = action_data

        post_result = await db.execute(select(Post.platform).where(Post.id == post_id))
        platform = post_result.scalar_one_or_none()
        if not platform:
            return None

        return (user_id, platform.value)


async def _execute_engagement(engagement_action_id: str):
    import uuid

    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.engagement import ActionStatus, ActionType, AuditLog, EngagementAction
    from app.models.post import Post
    from app.models.user import User, UserProfile

    async with get_task_session() as db:
        # Load the engagement action with related data
        result = await db.execute(
            select(EngagementAction).where(EngagementAction.id == uuid.UUID(engagement_action_id))
        )
        action = result.scalar_one_or_none()
        if not action:
            logger.error(f"Engagement action {engagement_action_id} not found")
            return

        if action.status != ActionStatus.PENDING:
            logger.info(
                f"Action {engagement_action_id} already processed (status: {action.status})"
            )
            return

        # Update status to in_progress
        action.status = ActionStatus.IN_PROGRESS
        action.attempted_at = datetime.now(UTC)
        await db.commit()

        # Load post
        post_result = await db.execute(select(Post).where(Post.id == action.post_id))
        post = post_result.scalar_one()

        # Load user profile
        user_result = await db.execute(select(User).where(User.id == action.user_id))
        user = user_result.scalar_one()
        profile_result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
        profile = profile_result.scalar_one_or_none()

        # Load integration account and refresh tokens
        from app.core.security import decrypt_value
        from app.models.integration import IntegrationAccount
        from app.models.integration import Platform as IntPlatform

        platform_value = post.platform.value

        integration = None
        access_token = None

        if platform_value == "linkedin":
            int_result = await db.execute(
                select(IntegrationAccount).where(
                    IntegrationAccount.user_id == action.user_id,
                    IntegrationAccount.platform == IntPlatform.LINKEDIN,
                )
            )
            integration = int_result.scalar_one_or_none()
            if integration:
                from app.services.token_service import refresh_linkedin_token

                try:
                    access_token = await refresh_linkedin_token(integration, db)
                except Exception as refresh_err:
                    logger.warning(f"LinkedIn token refresh failed (continuing): {refresh_err}")
                    if integration.access_token:
                        access_token = decrypt_value(integration.access_token)

        elif platform_value == "meta":
            int_result = await db.execute(
                select(IntegrationAccount).where(
                    IntegrationAccount.user_id == action.user_id,
                    IntegrationAccount.platform == IntPlatform.META,
                )
            )
            integration = int_result.scalar_one_or_none()
            if integration:
                from app.services.token_service import refresh_meta_token

                try:
                    access_token = await refresh_meta_token(integration, db)
                except Exception as refresh_err:
                    logger.warning(f"Meta token refresh failed (continuing): {refresh_err}")
                    if integration.access_token:
                        access_token = decrypt_value(integration.access_token)

        # Execute the action
        try:
            if action.action_type == ActionType.LIKE:
                await _execute_like(
                    platform_value,
                    str(action.user_id),
                    post,
                    integration=integration,
                    access_token=access_token,
                )
                action.status = ActionStatus.COMPLETED

            elif action.action_type == ActionType.COMMENT:
                comment_platform = _get_comment_platform(platform_value, post.url)

                # Load org-level custom avoid phrases
                from app.models.engagement import AIAvoidPhrase
                from app.services.comment_generator import (
                    DEFAULT_AVOID_PHRASES,
                    generate_and_review_comment,
                )

                org_phrases_result = await db.execute(
                    select(AIAvoidPhrase).where(
                        AIAvoidPhrase.org_id == user.org_id,
                        AIAvoidPhrase.active.is_(True),
                    )
                )
                custom_phrases = [p.phrase for p in org_phrases_result.scalars().all()]
                all_avoid = list(DEFAULT_AVOID_PHRASES) + custom_phrases if custom_phrases else None

                comment_result = await generate_and_review_comment(
                    post_content=post.content_text or "",
                    user_profile=profile.markdown_text if profile else "",
                    tone_settings=profile.tone_settings if profile else None,
                    avoid_phrases=all_avoid,
                    platform=comment_platform,
                )
                action.comment_text = comment_result["comment"]
                action.llm_response = comment_result["llm_data"]

                await _execute_comment(
                    platform_value,
                    str(action.user_id),
                    post,
                    comment_result["comment"],
                    integration=integration,
                    access_token=access_token,
                )
                action.status = ActionStatus.COMPLETED

            action.completed_at = datetime.now(UTC)

        except Exception as e:
            action.status = ActionStatus.FAILED
            action.error_message = str(e)
            logger.error(f"Action {engagement_action_id} failed: {e}")

        # Write audit log
        audit = AuditLog(
            org_id=user.org_id,
            user_id=user.id,
            action=f"{action.action_type.value}_{action.status.value}",
            target_type="post",
            target_id=str(post.id),
            metadata_={
                "post_url": post.url,
                "action_type": action.action_type.value,
                "comment_text": action.comment_text,
            },
        )
        db.add(audit)
        await db.commit()


def _get_comment_platform(platform_value: str, post_url: str) -> str:
    """Determine the specific platform for comment tone generation."""
    if platform_value == "linkedin":
        return "linkedin"
    elif platform_value == "meta":
        from app.services.url_utils import is_instagram_url

        if is_instagram_url(post_url):
            return "instagram"
        return "facebook"
    return platform_value


async def _execute_like(
    platform_value: str, user_id: str, post, integration=None, access_token=None
) -> None:
    """Execute a like action on the appropriate platform.

    For LinkedIn: uses REST API if OAuth token + person URN are available, falls back to Playwright.
    For Meta: uses Playwright automation.
    """
    if platform_value == "linkedin":
        # Try REST API first (preferred — uses OAuth token)
        if integration and access_token:
            person_urn = (integration.settings or {}).get("person_urn")
            if person_urn:
                from app.services.linkedin_api import extract_activity_urn_from_url, react_to_post

                activity_urn = extract_activity_urn_from_url(post.url)
                if activity_urn:
                    success = await react_to_post(access_token, person_urn, activity_urn)
                    if success:
                        return
                    raise ValueError(f"LinkedIn API reaction failed for {post.url}")
                else:
                    logger.warning(
                        f"Could not extract activity URN from {post.url} — trying Playwright"
                    )
            else:
                logger.warning(
                    "No person_urn in integration settings — re-connect LinkedIn in Settings"
                )

        # Fallback: Playwright (requires browser session cookies)
        from app.automation.linkedin_actions import like_post

        await like_post(user_id, post.url)

    elif platform_value == "meta":
        from app.services.url_utils import is_instagram_url

        if is_instagram_url(post.url):
            from app.automation.instagram_actions import like_post as ig_like

            await ig_like(user_id, post.url)
        else:
            from app.automation.facebook_actions import like_post as fb_like

            await fb_like(user_id, post.url)
    else:
        raise ValueError(f"Unsupported platform for like: {platform_value}")


async def _execute_comment(
    platform_value: str, user_id: str, post, comment_text: str, integration=None, access_token=None
) -> None:
    """Execute a comment action on the appropriate platform.

    For LinkedIn: uses REST API if OAuth token + person URN are available, falls back to Playwright.
    For Meta: uses Playwright automation.
    """
    if platform_value == "linkedin":
        # Try REST API first
        if integration and access_token:
            person_urn = (integration.settings or {}).get("person_urn")
            if person_urn:
                from app.services.linkedin_api import comment_on_post as li_api_comment
                from app.services.linkedin_api import extract_activity_urn_from_url

                activity_urn = extract_activity_urn_from_url(post.url)
                if activity_urn:
                    success = await li_api_comment(
                        access_token, person_urn, activity_urn, comment_text
                    )
                    if success:
                        return
                    raise ValueError(f"LinkedIn API comment failed for {post.url}")
                else:
                    logger.warning(
                        f"Could not extract activity URN from {post.url} — trying Playwright"
                    )

        # Fallback: Playwright
        from app.automation.linkedin_actions import comment_on_post

        await comment_on_post(user_id, post.url, comment_text)

    elif platform_value == "meta":
        from app.services.url_utils import is_instagram_url

        if is_instagram_url(post.url):
            from app.automation.instagram_actions import comment_on_post as ig_comment

            await ig_comment(user_id, post.url, comment_text)
        else:
            from app.automation.facebook_actions import comment_on_post as fb_comment

            await fb_comment(user_id, post.url, comment_text)
    else:
        raise ValueError(f"Unsupported platform for comment: {platform_value}")
