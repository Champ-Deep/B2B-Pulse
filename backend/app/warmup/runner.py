"""
The warm-up runner: actually perform the day's activity.

``planner.plan_day`` decides *what and when*. This does it.

Where the activity comes from
-----------------------------
In Social Bot this drew from an ICP's recent posts. B2B Pulse has no ICP yet
(that lands in Phase 5), but it has something better suited to warm-up anyway:
**tracked pages**. Those are the companies the org already cares about, so
engaging with them builds exactly the interest graph the account should have,
and a warming account is doing something useful from day one rather than
liking filler.

When ICP targeting lands it becomes a second source feeding the same runner —
the plan, the pacing and the gating do not change.

What runs automatically, and what doesn't
-----------------------------------------
Split by blast radius, not convenience:

- **Likes run automatically.** Reversible, no text, nobody has ever regretted
  one.
- **Comments are left to the existing engagement pipeline**, which generates
  them through the two-pass writer and (from Phase 3) surfaces them for bulk
  approval. They are published under a real person's name and cannot be taken
  back, so they are not executed straight from here.

This module never bypasses the warm-up gate or the caps: it asks
``warmup_service.can_perform`` before every action and consumes a rate-limiter
slot for each one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.safety import caps as caps_policy
from app.transports.base import TransportChallenge, TransportError
from app.warmup import planner, program
from app.warmup import service as warmup_service
from app.warmup.models import AccountActivity, ActivityStatus

logger = logging.getLogger(__name__)

# How many tracked-page posts to consider per run. Pulling everything on every
# tick is its own detectable pattern.
POST_SAMPLE = 25


async def run_today(
    db,
    account,
    *,
    transport: Any = None,
    rate_limiter: Any = None,
    live: Any = None,
    now: datetime | None = None,
    max_actions: int = 25,
) -> dict:
    """
    Perform whatever this account is due to do right now.

    Safe to call on a short interval: only actions whose planned time has
    passed are performed, and work already done today is subtracted, so calling
    it more often does not make the account act faster.
    """
    now = now or datetime.now(UTC)

    assessment = await warmup_service.today(db, account, now=now)
    if assessment.get("paused"):
        return {
            "performed": [],
            "skipped": {"paused": 1},
            "message": "Warm-up is paused for this account",
        }

    plan_actions = assessment["plan"]["actions"]
    stage_key = assessment["stage"]
    throttle = float(assessment["health"].get("throttle", 1.0))

    # Only what is due; anything scheduled later today stays there.
    due = [a for a in plan_actions if _parse(a["at"]) <= now]
    already = assessment["plan"].get("completed_today") or {}

    outstanding: list[dict] = []
    remaining: dict[str, int] = {}
    for item in due:
        action = item["action"]
        if action not in remaining:
            planned = len([a for a in plan_actions if a["action"] == action])
            remaining[action] = max(0, planned - int(already.get(action, 0)))
        if remaining[action] > 0:
            outstanding.append(item)
            remaining[action] -= 1

    if not outstanding:
        return {
            "performed": [],
            "skipped": {},
            "stage": stage_key,
            "message": "Nothing due right now — the day's activity is paced out",
        }

    live = live or await _live_account(db, account)
    client = transport or _default_transport(live)

    feed = await _tracked_page_feed(db, account)
    performed: list[dict] = []
    skipped: dict[str, int] = {}

    for item in outstanding[:max_actions]:
        action = item["action"]

        allowed, _reason = warmup_service.can_perform(account, action)
        if not allowed:
            _bump(skipped, "not_unlocked")
            continue

        if action == program.LIKE:
            post = _take(feed)
            if post is None:
                _bump(skipped, "no_post_to_engage_with")
                continue
            if not await _consume_slot(account, action, rate_limiter, throttle):
                _bump(skipped, "at_cap")
                continue

            outcome = await _like(db, account, client, live, post)
            if outcome:
                performed.append({"action": action, **outcome})
            else:
                _bump(skipped, "like_failed")

        elif action == program.COMMENT:
            # Comments belong to the engagement pipeline — see the docstring.
            _bump(skipped, "comment_handled_by_engagement_pipeline")

        else:
            # follow and post have no content source in B2B Pulse yet.
            _bump(skipped, f"{action}_not_wired_yet")

    await db.commit()

    counts: dict[str, int] = {}
    for entry in performed:
        counts[entry["action"]] = counts.get(entry["action"], 0) + 1

    return {
        "performed": performed,
        "skipped": skipped,
        "stage": stage_key,
        "message": (
            ", ".join(f"{v} {k}s" for k, v in counts.items())
            if counts
            else "nothing performed"
        ),
    }


# ----------------------------------------------------------------------
# Activity source
# ----------------------------------------------------------------------


async def _tracked_page_feed(db, account) -> list[dict]:
    """
    Recent tracked-page posts this account hasn't touched.

    Excludes anything already engaged with by *either* this runner or the
    normal engagement pipeline — double-liking a post is useless and
    conspicuous in equal measure.
    """
    from app.models.engagement import EngagementAction
    from app.models.post import Post
    from app.models.tracked_page import TrackedPage
    from app.models.user import User

    org_id = (
        await db.execute(select(User.org_id).where(User.id == account.user_id))
    ).scalar_one_or_none()
    if org_id is None:
        return []

    posts = list(
        (
            await db.execute(
                select(Post)
                .join(TrackedPage, TrackedPage.id == Post.tracked_page_id)
                .where(TrackedPage.org_id == org_id)
                .order_by(Post.first_seen_at.desc())
                .limit(POST_SAMPLE)
            )
        )
        .scalars()
        .all()
    )
    if not posts:
        return []

    engaged = set(
        (
            await db.execute(
                select(EngagementAction.post_id).where(
                    EngagementAction.user_id == account.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    warmed = set(
        (
            await db.execute(
                select(AccountActivity.subject_urn).where(
                    AccountActivity.account_id == account.id,
                    AccountActivity.subject_urn.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    return [
        {"id": p.id, "url": p.url, "urn": p.external_post_id, "text": p.content_text}
        for p in posts
        if p.id not in engaged and str(p.external_post_id) not in warmed
    ]


async def _like(db, account, client, live, post: dict) -> dict | None:
    """Like one post, recording the outcome either way."""
    subject = post.get("urn") or post.get("url")

    try:
        result = await client.like(live, subject)
    except TransportChallenge as exc:
        # LinkedIn pushed back. Deactivate rather than retrying into a
        # restriction; the next assessment demotes the account's stage.
        account.is_active = False
        await warmup_service.record(
            db, account, program.LIKE, status=ActivityStatus.BLOCKED,
            subject_urn=subject, error=str(exc), commit=False,
        )
        logger.warning("Account %s challenged during warm-up: %s", account.id, exc)
        return None
    except TransportError as exc:
        await warmup_service.record(
            db, account, program.LIKE, status=ActivityStatus.FAILED,
            subject_urn=subject, error=str(exc), commit=False,
        )
        return None

    await warmup_service.record(
        db,
        account,
        program.LIKE,
        status=ActivityStatus.OK if result.success else ActivityStatus.FAILED,
        subject_urn=subject,
        detail={"via": result.via, "post_url": post.get("url")},
        error=None if result.success else result.error,
        commit=False,
    )
    return {"subject": subject, "url": post.get("url")} if result.success else None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _take(feed: list[dict]) -> dict | None:
    return feed.pop(0) if feed else None


async def _consume_slot(account, action: str, rate_limiter, throttle: float) -> bool:
    """Check the global cap. No limiter means we can't prove safety, so refuse."""
    if rate_limiter is None:
        import os

        return os.getenv("ALLOW_UNCAPPED_SENDING", "").lower() == "true"

    caps = caps_policy.caps_for(account, action, throttle=throttle)
    decision = await rate_limiter.check_and_consume(
        str(account.id),
        action,
        per_hour=caps.per_hour,
        per_day=caps.per_day,
        per_week=caps.per_week,
        cooldown_seconds=caps.cooldown_seconds,
    )
    return bool(decision.allowed)


async def _live_account(db, account):
    from app.services.account_service import load_live_account

    return await load_live_account(db, account.id)


def _default_transport(live):
    from app.services.account_service import get_transport

    return get_transport(live)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _bump(counter: dict, key: str) -> None:
    counter[key] = counter.get(key, 0) + 1
