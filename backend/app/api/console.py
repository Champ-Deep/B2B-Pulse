"""
The admin console: one view over every account in the org.

The requirement this answers: *"about four or five LinkedIn accounts that
belong to real people — I need to see what is happening across all of them and
guide them in the right direction."*

One call returns every account with its warm-up stage, health verdict, funnel
and today's headroom, plus the two things that only exist at the org level —
how correlated the accounts' behaviour is, and how much cluster headroom is
left. Those two are the difference between an admin console and five dashboards
in a trenchcoat: they are questions no single account can answer about itself.

The steering endpoints follow the same principle as persona inheritance and
cap merging: a central control moves everyone who hasn't overridden it, and an
individual override survives. Nothing here can loosen a per-account safety
setting — the org can only make things stricter.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk import RequestContext, get_request_context
from app.database import get_db
from app.models.user import User
from app.outbound import approvals
from app.personas import service as personas
from app.safety import cluster
from app.safety import health as health_module
from app.services.account_service import get_account, list_org_accounts
from app.warmup import planner, program

router = APIRouter(prefix="/console", tags=["console"])


class SteerRequest(BaseModel):
    """A central instruction applied across the org's accounts."""

    pause: bool | None = Field(None, description="Pause or resume every account")
    reason: str = Field("", max_length=500)
    participation_rate: float | None = Field(
        None, ge=0.1, le=1.0,
        description="Fraction of accounts that engage with any one post",
    )


class BulkDecision(BaseModel):
    suggestion_ids: list[str] = Field(..., min_length=1, max_length=500)


@router.get("/overview")
async def overview(
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Everything an operator needs in one call.

    Per-account: who they are, where they are in warm-up, their health verdict
    and funnel. Org-level: behaviour correlation and what is waiting for review.
    """
    accounts = await list_org_accounts(db, ctx.org_id)

    rows = []
    totals = {
        "accounts": len(accounts),
        "warming_up": 0,
        "paused": 0,
        "needs_attention": 0,
        "invites_sent": 0,
        "invites_accepted": 0,
        "likes_sent": 0,
        "comments_sent": 0,
    }

    for account in accounts:
        report = await health_module.account_health(db, account)
        stage = program.stage_for(planner.current_stage(account))
        persona = await personas.resolve(db, ctx.org_id, account.id)

        owner = (
            await db.execute(select(User).where(User.id == account.user_id))
        ).scalar_one_or_none()

        row = {
            "account_id": str(account.id),
            "user_id": str(account.user_id),
            "name": (
                account.linkedin_user_name
                or (owner.full_name if owner else None)
                or persona.name
            ),
            "email": owner.email if owner else None,
            "persona": persona.name or None,
            "persona_overridden": persona.overridden,
            "stage": stage.key,
            "stage_name": stage.name,
            "days_in_stage": planner.days_in_stage(account),
            "allowed_actions": sorted(stage.allowed),
            "paused": planner.paused(account),
            "is_active": account.is_active,
            "health": report.as_dict(),
            "schedule_window": list(cluster.schedule_window(str(account.id))),
        }
        rows.append(row)

        if stage.key != program.FINAL_STAGE:
            totals["warming_up"] += 1
        if row["paused"]:
            totals["paused"] += 1
        if report.verdict == health_module.DANGER:
            totals["needs_attention"] += 1

        funnel = report.funnel
        totals["invites_sent"] += funnel.invites_sent
        totals["invites_accepted"] += funnel.invites_accepted
        totals["likes_sent"] += funnel.likes_sent
        totals["comments_sent"] += funnel.comments_sent

    if totals["invites_sent"]:
        totals["acceptance_rate"] = round(
            100 * totals["invites_accepted"] / totals["invites_sent"], 1
        )

    correlation = await cluster.measure_correlation(db, ctx.org_id)

    engagement_queue = await approvals.queue(db, ctx.org_id, kind="engagement", limit=500)
    messaging_queue = await approvals.queue(db, ctx.org_id, kind="messaging", limit=500)

    return {
        "accounts": rows,
        "totals": totals,
        # The two questions no single account can answer about itself.
        "correlation": correlation.as_dict(),
        "review": {
            "engagement_pending": len(engagement_queue),
            "messaging_pending": len(messaging_queue),
            # Engagement is bulk-approvable org-wide; messaging is per account,
            # so the UI shows where each one is reviewed.
            "engagement_is_bulk": True,
            "messaging_by_account": _count_by_account(messaging_queue),
        },
        "cluster_policy": {
            "participation_rate": cluster.DEFAULT_PARTICIPATION_RATE,
            "max_accounts_per_post": cluster.MAX_ACCOUNTS_PER_POST,
            "max_accounts_per_company_per_day": cluster.MAX_ACCOUNTS_PER_COMPANY_PER_DAY,
        },
    }


@router.get("/queue/engagement")
async def engagement_queue(
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    The org-wide engagement queue, bulk-approvable.

    Safe to approve in a sweep only because participation was already sampled
    before this queue was built — the admin is approving "these accounts engage
    with this post", not "every account does".
    """
    rows = await approvals.queue(db, ctx.org_id, kind="engagement")
    return {
        "suggestions": [await _serialize(db, r) for r in rows],
        "total": len(rows),
        "bulk_approvable": True,
    }


@router.get("/queue/messaging")
async def messaging_queue(
    account_id: str | None = None,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    The messaging queue, reviewed per account.

    A direct message goes out under one named person's identity to someone who
    will reply to them, so these are never bulk-approved.
    """
    rows = await approvals.queue(
        db,
        ctx.org_id,
        kind="messaging",
        account_id=uuid.UUID(account_id) if account_id else None,
    )
    return {
        "suggestions": [await _serialize(db, r) for r in rows],
        "total": len(rows),
        "bulk_approvable": False,
        "note": (
            "Messages go out under one person's name and are approved on their "
            "account rather than in bulk."
        ),
    }


@router.post("/queue/engagement/approve")
async def approve_engagement(
    body: BulkDecision,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve a sweep of engagement suggestions."""
    result = await approvals.approve_bulk(
        db, body.suggestion_ids, org_id=ctx.org_id, reviewer_id=ctx.user.id
    )
    return result.as_dict()


@router.post("/queue/reject")
async def reject_many(
    body: BulkDecision,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reject a sweep of suggestions — allowed for every action type."""
    result = await approvals.reject_bulk(
        db, body.suggestion_ids, org_id=ctx.org_id, reviewer_id=ctx.user.id
    )
    return result.as_dict()


@router.post("/steer")
async def steer(
    body: SteerRequest,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Apply a central instruction to every account in the org.

    Pausing is the one control that must reach everything instantly — it is the
    "stop, something is wrong" button, and it would be useless if it respected
    per-account overrides.
    """
    accounts = await list_org_accounts(db, ctx.org_id)
    changed = []

    if body.pause is not None:
        for account in accounts:
            planner.set_paused(account, body.pause, body.reason or "paused from the console")
            changed.append(str(account.id))
        await db.commit()

    return {
        "accounts_changed": changed,
        "paused": body.pause,
        "note": (
            "Pausing reaches every account immediately, including ones with "
            "their own settings — it is the stop button."
            if body.pause
            else None
        ),
    }


@router.get("/accounts/{account_id}")
async def account_detail(
    account_id: str,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Everything about one account: stage, health, persona and today's plan."""
    from app.warmup import service as warmup_service

    account = await get_account(db, account_id, ctx.org_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    today = await warmup_service.today(db, account)
    persona = await personas.resolve(db, ctx.org_id, account.id)

    return {
        **today,
        "persona": persona.as_dict(),
        "schedule_window": list(cluster.schedule_window(str(account.id))),
    }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _count_by_account(rows) -> dict:
    counts: dict = {}
    for row in rows:
        key = str(row.account_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


async def _serialize(db: AsyncSession, row) -> dict:
    from app.outbound.models import OutreachTarget

    target = (
        await db.execute(
            select(OutreachTarget).where(OutreachTarget.id == row.target_id)
        )
    ).scalar_one_or_none()

    return {
        "id": str(row.id),
        "account_id": str(row.account_id),
        "action": row.action,
        "status": row.status,
        "draft_text": row.draft_text,
        "rationale": row.rationale,
        "relevance_score": row.relevance_score,
        "relevance_reasons": row.relevance_reasons or [],
        "quality_score": row.quality_score,
        "quality_warnings": row.quality_warnings or [],
        "generated_by": row.generated_by,
        "target": (
            {
                "id": str(target.id),
                "full_name": target.full_name,
                "headline": target.headline,
                "company": target.company,
                "profile_url": target.profile_url,
            }
            if target
            else None
        ),
    }
