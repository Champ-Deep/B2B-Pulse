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

    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.engagement import ActionStatus, ActionType, EngagementAction
    from app.models.tracked_page import TrackedPageSubscription

    async with get_task_session() as db:
        # Get all subscriptions for this page
        result = await db.execute(
            select(TrackedPageSubscription).where(
                TrackedPageSubscription.tracked_page_id == uuid.UUID(tracked_page_id)
            )
        )
        subscriptions = result.scalars().all()

        for i, sub in enumerate(subscriptions):
            # Create like action
            if sub.auto_like:
                like_action = EngagementAction(
                    post_id=uuid.UUID(post_id),
                    user_id=sub.user_id,
                    action_type=ActionType.LIKE,
                    status=ActionStatus.PENDING,
                )
                db.add(like_action)
                await db.flush()

                # Enqueue like with small random delay between users
                from app.config import LIKE_STAGGER_MAX, LIKE_STAGGER_MIN

                delay_seconds = random.randint(LIKE_STAGGER_MIN, LIKE_STAGGER_MAX) * (i + 1)
                execute_engagement.apply_async(
                    args=[str(like_action.id)],
                    countdown=delay_seconds,
                )

            # Create comment action
            if sub.auto_comment:
                comment_action = EngagementAction(
                    post_id=uuid.UUID(post_id),
                    user_id=sub.user_id,
                    action_type=ActionType.COMMENT,
                    status=ActionStatus.PENDING,
                )
                db.add(comment_action)
                await db.flush()

                # Stagger comments with random intervals per user
                from app.config import (
                    COMMENT_INTER_USER_DELAY,
                    COMMENT_STAGGER_MAX,
                    COMMENT_STAGGER_MIN,
                )

                delay_seconds = random.randint(COMMENT_STAGGER_MIN, COMMENT_STAGGER_MAX) + (
                    i * COMMENT_INTER_USER_DELAY
                )
                execute_engagement.apply_async(
                    args=[str(comment_action.id)],
                    countdown=delay_seconds,
                )

        await db.commit()
        logger.info(f"Scheduled {len(subscriptions)} engagement sets for post {post_id}")


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
    try:
        asyncio.run(_execute_engagement(engagement_action_id))
    except (httpx.TimeoutException, httpx.ConnectError, SoftTimeLimitExceeded) as e:
        logger.warning(f"Retriable error for {engagement_action_id}: {e}")
        raise self.retry(exc=e) from e
    except Exception as e:
        logger.error(f"Engagement execution failed for {engagement_action_id}: {e}")
        raise


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

                from app.services.comment_generator import generate_and_review_comment

                comment_result = await generate_and_review_comment(
                    post_content=post.content_text or "",
                    user_profile=profile.markdown_text if profile else "",
                    tone_settings=profile.tone_settings if profile else None,
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
