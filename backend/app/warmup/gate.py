"""
The warm-up gate for the engagement pipeline.

This is where the merge earns its keep. B2B Pulse's engagement pipeline fans a
tracked-page post out to every subscribed user and has them all like and
comment on it. That is fine for an established account and dangerous for a new
one — a freshly connected login whose very first action is an AI comment on a
company post is the exact profile that gets restricted.

So before the pipeline creates work for an account, it asks here. The gate
answers three questions in order of severity:

1. **Is there a connected LinkedIn account at all?** A subscription without one
   is a no-op that used to fail later, in a browser, with a confusing error.
2. **Has this account earned this action?** During the ``observe`` stage a
   comment is not throttled, it is impossible.
3. **Is the account healthy, and does it have headroom today?** The
   acceptance governor and the per-account caps both apply, and they are
   *lower* than the pipeline's own 50/day and 20/day defaults during warm-up.

A refusal always carries a human-readable reason, because "why did nothing
happen for Dana today" is a question someone will ask.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.safety import caps as caps_policy
from app.safety import health as health_module
from app.warmup import planner, program
from app.warmup import service as warmup_service

logger = logging.getLogger(__name__)

# Pipeline action names -> warm-up action names.
ACTION_MAP = {
    "like": program.LIKE,
    "comment": program.COMMENT,
    "LIKE": program.LIKE,
    "COMMENT": program.COMMENT,
}


@dataclass
class GateDecision:
    """Whether an action may proceed, and why not if it may not."""

    allowed: bool
    reason: str = ""
    stage: str | None = None
    # Caps that apply, so the caller can report headroom without re-deriving it.
    remaining_today: int | None = None

    def __bool__(self) -> bool:
        return self.allowed


async def account_for_user(db, user_id: uuid.UUID):
    """The user's LinkedIn integration account, if they have an active one."""
    from app.models.integration import IntegrationAccount, Platform

    return (
        await db.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == user_id,
                IntegrationAccount.platform == Platform.LINKEDIN,
                IntegrationAccount.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


async def check(
    db,
    user_id: uuid.UUID,
    action: str,
    *,
    rate_limiter=None,
    account=None,
) -> GateDecision:
    """
    May this user's account perform ``action`` right now?

    Called by the engagement pipeline before creating work. Deliberately fails
    *closed*: anything it cannot verify is a refusal, because the cost of
    wrongly skipping one like is nothing and the cost of wrongly sending one
    from a cold account is the account.
    """
    warm_action = ACTION_MAP.get(action, action)

    account = account or await account_for_user(db, user_id)
    if account is None:
        return GateDecision(
            allowed=False,
            reason="no active LinkedIn account is connected for this user",
        )

    stage = planner.current_stage(account)

    if planner.paused(account):
        state = getattr(account, "warmup_state", None) or {}
        detail = state.get("paused_reason") or "manually paused"
        return GateDecision(False, f"warm-up is paused ({detail})", stage)

    if not program.is_allowed(stage, warm_action):
        stage_obj = program.stage_for(stage)
        return GateDecision(
            allowed=False,
            reason=(
                f"'{warm_action}' isn't unlocked yet — this account is in the "
                f"{stage_obj.name.lower()} stage of warm-up"
            ),
            stage=stage,
        )

    report = await health_module.account_health(db, account)
    if report.blocks(warm_action):
        return GateDecision(False, report.headline, stage)

    caps = caps_policy.caps_for(account, warm_action, throttle=report.throttle)

    # Take the stricter of the warm-up cap and the pipeline's own cap.
    #
    # These were tuned independently: the pipeline allowed 50 likes/day flat,
    # while the warm-up tiers allow 40 during warm-up and 60 once established.
    # Merging two safety systems must never *loosen* either one, so whichever
    # is lower wins — otherwise an established account would silently gain
    # headroom it never had before the merge.
    pipeline_cap = _pipeline_cap(account, warm_action)
    effective_per_day = min(caps.per_day, pipeline_cap) if pipeline_cap else caps.per_day

    remaining = None
    if rate_limiter is not None:
        try:
            usage = await rate_limiter.usage(str(account.id), warm_action)
            remaining = max(0, effective_per_day - int(usage.get("day_used", 0)))
            if caps.per_week:
                remaining = min(
                    remaining, max(0, caps.per_week - int(usage.get("week_used", 0)))
                )
            if remaining <= 0:
                return GateDecision(
                    allowed=False,
                    reason=(
                        f"{warm_action} cap reached for today "
                        f"({effective_per_day}/day at the {stage} stage)"
                    ),
                    stage=stage,
                    remaining_today=0,
                )
        except Exception as exc:  # a limiter hiccup must not silently uncap
            logger.warning("Rate usage lookup failed for %s: %s", account.id, exc)
            return GateDecision(
                allowed=False,
                reason="rate limiter unavailable, so caps cannot be enforced",
                stage=stage,
            )

    return GateDecision(True, "", stage, remaining)


def _pipeline_cap(account, action: str) -> int | None:
    """
    B2B Pulse's own per-day cap for an action.

    Mirrors what ``engagement_tasks`` applies, so the gate can compare against
    it rather than the two limits disagreeing silently.
    """
    settings = getattr(account, "daily_caps", None) or {}
    risk = settings.get("risk_profile", "safe")

    if action == program.LIKE:
        return 150 if risk == "aggro" else 50
    if action == program.COMMENT:
        return 60 if risk == "aggro" else 20
    return None


async def consume(db, account, action: str, rate_limiter, *, throttle: float = 1.0) -> bool:
    """
    Consume a rate-limit slot for an action about to happen.

    Separate from :func:`check` because the pipeline decides well before it
    acts: the slot should be taken at execution time, not at scheduling time,
    or a burst of scheduled work would reserve the whole day's allowance up
    front and then leak it if the work never ran.
    """
    if rate_limiter is None:
        import os

        return os.getenv("ALLOW_UNCAPPED_SENDING", "").lower() == "true"

    warm_action = ACTION_MAP.get(action, action)
    caps = caps_policy.caps_for(account, warm_action, throttle=throttle)
    decision = await rate_limiter.check_and_consume(
        str(account.id),
        warm_action,
        per_hour=caps.per_hour,
        per_day=caps.per_day,
        per_week=caps.per_week,
        cooldown_seconds=caps.cooldown_seconds,
    )
    return bool(decision.allowed)


async def record(db, account, action: str, *, ok: bool, subject: str | None = None,
                 error: str | None = None, commit: bool = True) -> None:
    """
    Write a pipeline action to the activity ledger.

    Without this the pipeline's work would be invisible to warm-up graduation,
    and an account could sit at the ``observe`` stage forever while actually
    engaging all day through the tracked-page path.
    """
    from app.warmup.models import ActivityStatus

    await warmup_service.record(
        db,
        account,
        ACTION_MAP.get(action, action),
        status=ActivityStatus.OK if ok else ActivityStatus.FAILED,
        subject_urn=subject,
        detail={"source": "engagement_pipeline"},
        error=error,
        commit=commit,
    )
