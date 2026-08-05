"""
The warm-up gate on B2B Pulse's engagement pipeline.

This is the merge's load-bearing safety integration. B2B Pulse fans a
tracked-page post out to every subscribed user and has them all like and
comment on it. That is fine for an established account and dangerous for a new
one, so the gate decides whether an account has earned each action before the
pipeline creates work for it.

These tests are mostly about what the gate *refuses*.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.integration import IntegrationAccount, Platform
from app.models.user import User
from app.warmup import gate, planner, program


@pytest.fixture
async def account(db, client, auth_headers):
    """A connected LinkedIn account belonging to an authenticated user."""
    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(me["id"])))
    ).scalar_one()

    record = IntegrationAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        platform=Platform.LINKEDIN,
        is_active=True,
        warmup_state={},
        daily_caps={},
    )
    planner.set_stage(record, program.FIRST_STAGE)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


class FakeLimiter:
    """A limiter with a fixed answer, so cap behaviour is testable."""

    def __init__(self, day_used=0, week_used=0, broken=False):
        self.day_used = day_used
        self.week_used = week_used
        self.broken = broken

    async def usage(self, account_id, action):
        if self.broken:
            raise RuntimeError("redis is down")
        return {"day_used": self.day_used, "week_used": self.week_used}


# ---------------------------------------------------------------------------
# The gate refuses what it should
# ---------------------------------------------------------------------------


async def test_a_user_with_no_connected_account_is_refused(db, client, auth_headers):
    """A subscription without an account used to fail later, in a browser."""
    me = (await client.get("/api/auth/me", headers=auth_headers)).json()

    decision = await gate.check(db, uuid.UUID(me["id"]), "like")
    assert not decision
    assert "no active LinkedIn account" in decision.reason


async def test_a_new_account_may_like_but_not_comment(db, account):
    """
    The headline property.

    Commenting unlocks two stages after liking, so a fresh account can be
    building history for a week before it is allowed to say anything.
    """
    like = await gate.check(db, account.user_id, "like", account=account)
    comment = await gate.check(db, account.user_id, "comment", account=account)

    assert like, like.reason
    assert not comment
    assert "isn't unlocked yet" in comment.reason
    assert "observing" in comment.reason


async def test_the_gate_understands_the_pipeline_action_names(db, account):
    """The pipeline uses ActionType.LIKE/COMMENT casing; the gate maps both."""
    for name in ("like", "LIKE"):
        assert await gate.check(db, account.user_id, name, account=account)
    for name in ("comment", "COMMENT"):
        assert not await gate.check(db, account.user_id, name, account=account)


async def test_a_warmed_account_may_comment(db, account):
    planner.set_stage(account, "converse")
    await db.commit()

    decision = await gate.check(db, account.user_id, "comment", account=account)
    assert decision, decision.reason


async def test_pausing_an_account_stops_everything(db, account):
    planner.set_stage(account, program.FINAL_STAGE)
    planner.set_paused(account, True, "investigating a warning")
    await db.commit()

    decision = await gate.check(db, account.user_id, "like", account=account)
    assert not decision
    assert "paused" in decision.reason
    assert "investigating a warning" in decision.reason


async def test_an_inactive_account_is_treated_as_challenged(db, account):
    """
    Deactivation is how a challenge is recorded, so it must halt invitations.

    Likes continue: the account still exists and still needs to look alive,
    and it is invitations that carry the restriction risk.
    """
    planner.set_stage(account, program.FINAL_STAGE)
    account.is_active = False
    await db.commit()

    decision = await gate.check(db, account.user_id, "connect", account=account)
    assert not decision
    assert "challenged" in decision.reason.lower()


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


async def test_the_daily_cap_is_enforced_at_the_gate(db, account):
    planner.set_stage(account, program.FINAL_STAGE)
    await db.commit()

    from app.safety import caps as caps_policy

    caps = caps_policy.caps_for(account, "like")
    decision = await gate.check(
        db, account.user_id, "like",
        account=account, rate_limiter=FakeLimiter(day_used=caps.per_day),
    )

    assert not decision
    assert "cap reached" in decision.reason
    assert decision.remaining_today == 0


async def test_headroom_is_reported_when_there_is_some(db, account):
    planner.set_stage(account, program.FINAL_STAGE)
    await db.commit()

    decision = await gate.check(
        db, account.user_id, "like", account=account, rate_limiter=FakeLimiter(day_used=1)
    )
    assert decision
    assert decision.remaining_today is not None
    assert decision.remaining_today > 0


async def test_a_broken_limiter_fails_closed(db, account):
    """
    A component whose whole job is to stop over-sending must never fail open.

    If we cannot prove the account is under its cap, we do not act.
    """
    planner.set_stage(account, program.FINAL_STAGE)
    await db.commit()

    decision = await gate.check(
        db, account.user_id, "like", account=account, rate_limiter=FakeLimiter(broken=True)
    )
    assert not decision
    assert "rate limiter unavailable" in decision.reason


async def test_a_warming_account_is_capped_below_the_pipeline_default(db, account):
    """
    The pipeline allowed 50 likes and 20 comments a day regardless of tenure.

    A warming account has to be well under that, or the gate is decorative.
    """
    from app.safety import caps as caps_policy

    warm = caps_policy.caps_for(account, "like")  # observe stage, warmup tier
    assert warm.per_day < 50

    planner.set_stage(account, "converse")
    assert caps_policy.caps_for(account, "comment").per_day < 20


async def test_the_merge_never_loosens_an_existing_cap(db, account):
    """
    Two safety systems merged must take the stricter limit, not the newer one.

    The warm-up 'standard' tier allows 60 likes/day; the pipeline allowed 50.
    An established account must not silently gain headroom it never had.
    """
    account.daily_caps = {"tier": "standard"}
    planner.set_stage(account, program.FINAL_STAGE)
    await db.commit()

    from app.safety import caps as caps_policy

    warm_cap = caps_policy.caps_for(account, "like").per_day
    assert warm_cap > 50, "precondition: the tier is looser than the pipeline"

    # One under the pipeline's 50 is allowed...
    assert await gate.check(
        db, account.user_id, "like", account=account, rate_limiter=FakeLimiter(day_used=49)
    )
    # ...but 50 is not, even though the warm-up tier would still permit it.
    refused = await gate.check(
        db, account.user_id, "like", account=account, rate_limiter=FakeLimiter(day_used=50)
    )
    assert not refused
    assert "cap reached" in refused.reason


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


async def test_pipeline_actions_reach_the_ledger(db, account):
    """
    Otherwise warm-up graduation is blind to the pipeline's work.

    An account could engage all day through tracked pages and still sit at the
    observe stage forever, because the programme counts from the ledger.
    """
    from app.warmup import service as warmup_service

    await gate.record(db, account, "like", ok=True, subject="urn:li:activity:1")
    await gate.record(db, account, "like", ok=True, subject="urn:li:activity:2")

    totals = await warmup_service.totals_for(db, account)
    assert totals.get("like") == 2


async def test_failed_actions_do_not_count_toward_graduation(db, account):
    from app.warmup import service as warmup_service

    await gate.record(db, account, "like", ok=False, subject="urn:li:activity:1",
                      error="button not found")

    totals = await warmup_service.totals_for(db, account)
    assert totals.get("like", 0) == 0


async def test_the_ledger_records_which_stage_an_action_happened_in(db, account):
    """Makes the ramp auditable after the fact, which is what you want when
    explaining a restriction."""
    from app.warmup.models import AccountActivity

    await gate.record(db, account, "like", ok=True, subject="urn:li:activity:1")

    row = (
        await db.execute(
            select(AccountActivity).where(AccountActivity.account_id == account.id)
        )
    ).scalar_one()
    assert row.stage == program.FIRST_STAGE
    assert row.detail["source"] == "engagement_pipeline"
    assert row.org_id is not None
