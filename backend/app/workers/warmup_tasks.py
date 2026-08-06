"""
Scheduled warm-up work.

This is the piece that makes warm-up *autonomous*. Social Bot could compute a
daily activity plan but had nothing to execute it on a tick, so the plan was
intent rather than behaviour — the single biggest gap on that side of the
merge. B2B Pulse already runs Celery Beat, so warm-up simply becomes another
scheduled job and the gap closes for free.

Two jobs, deliberately separate:

- ``run_warmup_activity`` (every 20 min) performs whatever is due right now.
  The planner has already scattered actions across the day, so a frequent tick
  does not mean frequent activity — it means the account acts close to the
  minute it was scheduled to, instead of in a clump at the top of the hour.
- ``evaluate_warmup_stages`` (daily) advances or demotes accounts. Stage
  changes are a once-a-day decision; doing it on every tick would just be
  twenty times the database work for the same answer.

Both iterate every active LinkedIn account across every org. One account
failing must never stop the others, so each is wrapped individually.
"""

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.warmup_tasks.run_warmup_activity",
    soft_time_limit=540,
    time_limit=600,
)
def run_warmup_activity():
    """Perform each warming account's due activity."""
    return asyncio.run(_run_warmup_activity())


async def _run_warmup_activity() -> dict:
    from app.database import get_task_session
    from app.warmup import runner

    summary = {"accounts": 0, "performed": 0, "skipped": 0, "errors": 0}

    async with get_task_session() as db:
        accounts = await _active_accounts(db)
        limiter = _rate_limiter()

        for account in accounts:
            summary["accounts"] += 1
            try:
                result = await runner.run_today(db, account, rate_limiter=limiter)
                summary["performed"] += len(result.get("performed") or [])
                summary["skipped"] += sum((result.get("skipped") or {}).values())
                if result.get("performed"):
                    logger.info(
                        "Warm-up: account %s — %s", account.id, result["message"]
                    )
            except Exception as exc:
                # One bad account must not stop the rest.
                summary["errors"] += 1
                logger.warning("Warm-up run failed for account %s: %s", account.id, exc)

    logger.info("Warm-up tick: %s", summary)
    return summary


@celery_app.task(
    name="app.workers.warmup_tasks.evaluate_warmup_stages",
    soft_time_limit=300,
    time_limit=360,
)
def evaluate_warmup_stages():
    """Advance or demote accounts through the warm-up programme."""
    return asyncio.run(_evaluate_warmup_stages())


async def _evaluate_warmup_stages() -> dict:
    from app.database import get_task_session
    from app.warmup import service as warmup_service

    summary = {"accounts": 0, "advanced": 0, "demoted": 0, "errors": 0}

    async with get_task_session() as db:
        for account in await _active_accounts(db):
            summary["accounts"] += 1
            try:
                report = await warmup_service.evaluate(db, account)
                change = report.get("changed")
                if not change:
                    continue
                if change["direction"] == "forward":
                    summary["advanced"] += 1
                    logger.info(
                        "Account %s advanced: %s -> %s",
                        account.id, change["from"], change["to"],
                    )
                else:
                    summary["demoted"] += 1
                    # A demotion is the system protecting an account before
                    # LinkedIn does. It should be loud.
                    logger.warning(
                        "Account %s stepped back: %s -> %s (%s)",
                        account.id, change["from"], change["to"],
                        "; ".join(report.get("blockers") or []),
                    )
            except Exception as exc:
                summary["errors"] += 1
                logger.warning("Stage evaluation failed for %s: %s", account.id, exc)

    logger.info("Warm-up stage evaluation: %s", summary)
    return summary


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


async def _active_accounts(db) -> list:
    """Every active LinkedIn account, across every org."""
    from sqlalchemy import select

    from app.models.integration import IntegrationAccount, Platform

    return list(
        (
            await db.execute(
                select(IntegrationAccount).where(
                    IntegrationAccount.platform == Platform.LINKEDIN,
                    IntegrationAccount.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )


def _rate_limiter():
    """
    The global per-account limiter, shared with the engagement pipeline.

    Returns None if Redis is unreachable, which the runner treats as "cannot
    prove safety, so do nothing" — failing closed is the only sane default for
    a component whose job is to stop over-sending.

    It matters that this is the *same* limiter the tracked-page pipeline uses.
    Warm-up activity and pipeline engagement both spend one real LinkedIn
    account's daily allowance, so two independent counters would let a single
    account do a full warm-up day *and* a full pipeline day.
    """
    from app.safety.rate_policy import get_limiter

    return get_limiter()
