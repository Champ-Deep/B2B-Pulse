"""
Warm-up routes: the programme, an account's position in it, and org-wide health.

``GET /warmup/program`` is deliberately unauthenticated — it describes the
schedule itself, which is a product explanation rather than customer data, and
being able to show someone the ramp before they connect an account is the point.

``GET /warmup/accounts`` is the beginning of the admin console: one row per
connected account across the whole org, with its stage and health. It is what
answers "what are all five of our accounts actually doing" without opening five
screens.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk import RequestContext, get_request_context
from app.database import get_db
from app.models.user import User
from app.safety import health as health_module
from app.services.account_service import get_account, list_org_accounts
from app.warmup import planner, program
from app.warmup import service as warmup_service

router = APIRouter(prefix="/warmup", tags=["warmup"])


class PauseRequest(BaseModel):
    paused: bool = True
    reason: str = Field("", max_length=500)


class StageOverride(BaseModel):
    stage: str = Field(..., description="Stage key to move this account to")


async def _require_account(db: AsyncSession, account_id: str, org_id: uuid.UUID):
    record = await get_account(db, account_id, org_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return record


@router.get("/program")
async def get_program() -> dict:
    """The warm-up programme every new account follows."""
    return {
        "stages": program.describe_program(),
        "minimum_days_to_outreach": program.estimated_days_to_outreach(),
        "acceptance_thresholds": {
            "caution_below": program.ACCEPTANCE_CAUTION,
            "danger_below": program.ACCEPTANCE_DANGER,
        },
        "principles": [
            "Capability is earned, not configured: an action absent from the "
            "current stage cannot be performed at all, not merely throttled.",
            "Graduation needs elapsed time AND completed activity AND a healthy "
            "acceptance rate — time alone is what gets accounts restricted.",
            "A LinkedIn challenge or an acceptance rate under 15% steps the "
            "account back a stage automatically.",
            "Where the warm-up caps and the engagement pipeline's caps disagree, "
            "the stricter one applies.",
        ],
    }


@router.get("/accounts")
async def org_accounts(
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Every connected account in the org, with stage and health.

    The admin view: one call, one row per real person's account.
    """
    accounts = await list_org_accounts(db, ctx.org_id)

    rows = []
    warming = 0
    for account in accounts:
        report = await health_module.account_health(db, account)
        stage = program.stage_for(planner.current_stage(account))
        if stage.key != program.FINAL_STAGE:
            warming += 1

        owner = (
            await db.execute(
                User.__table__.select().where(User.id == account.user_id)
            )
        ).first()

        rows.append(
            {
                "account_id": str(account.id),
                "user_id": str(account.user_id),
                "name": account.linkedin_user_name or (owner.full_name if owner else None),
                "stage": stage.key,
                "stage_name": stage.name,
                "days_in_stage": planner.days_in_stage(account),
                "paused": planner.paused(account),
                "is_active": account.is_active,
                "allowed_actions": sorted(stage.allowed),
                "health": report.as_dict(),
            }
        )

    return {
        "accounts": rows,
        "totals": {
            "accounts": len(rows),
            "warming_up": warming,
            "paused": sum(1 for r in rows if r["paused"]),
            "needs_attention": sum(
                1 for r in rows if r["health"]["verdict"] == health_module.DANGER
            ),
        },
    }


@router.get("/accounts/{account_id}")
async def account_status(
    account_id: str,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Where this account is in the programme, and what's outstanding."""
    account = await _require_account(db, account_id, ctx.org_id)
    return await warmup_service.evaluate(db, account)


@router.get("/accounts/{account_id}/today")
async def account_today(
    account_id: str,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Today's activity plan: what this account should do, and when."""
    account = await _require_account(db, account_id, ctx.org_id)
    return await warmup_service.today(db, account)


@router.post("/accounts/{account_id}/pause")
async def pause(
    account_id: str,
    payload: PauseRequest,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Pause or resume all warm-up and engagement activity for an account."""
    account = await _require_account(db, account_id, ctx.org_id)
    planner.set_paused(account, payload.paused, payload.reason)
    await db.commit()
    return {"account_id": account_id, "paused": payload.paused, "reason": payload.reason}


@router.post("/accounts/{account_id}/stage")
async def override_stage(
    account_id: str,
    payload: StageOverride,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Move an account to a specific stage.

    Intended for accounts with genuine existing history that don't need the
    full ramp. Skipping ahead on a new account is how accounts get restricted,
    so the response says so rather than silently accepting it.
    """
    account = await _require_account(db, account_id, ctx.org_id)
    if payload.stage not in program.STAGES_BY_KEY:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown stage '{payload.stage}'. Valid: "
            + ", ".join(program.STAGES_BY_KEY),
        )

    previous = planner.current_stage(account)
    planner.set_stage(account, payload.stage)
    await db.commit()

    keys = list(program.STAGES_BY_KEY)
    skipped_ahead = keys.index(payload.stage) > keys.index(previous) + 1

    return {
        "account_id": account_id,
        "from": previous,
        "to": payload.stage,
        "warning": (
            "You skipped stages. That is safe for an established account with real "
            "history, and risky for a new one — a quiet account that suddenly starts "
            "acting is the strongest predictor of a restriction."
            if skipped_ahead
            else None
        ),
    }


@router.post("/accounts/{account_id}/preflight")
async def preflight(
    account_id: str,
    ctx: RequestContext = Depends(get_request_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Validate a live account using read-only calls only.

    Nothing is liked, commented, connected or posted, so this is safe to run
    against a production account at any time — and it is the right first step
    after connecting one.
    """
    from app.safety.preflight import run_preflight

    account = await _require_account(db, account_id, ctx.org_id)
    report = await run_preflight(account)
    return report.as_dict()
