"""
The safety gate at the moment of acting, not just at scheduling time.

The audit that produced this file found a gap with real consequences. The
tracked-page pipeline asked the warm-up gate before *creating* an engagement
action, then queued it on Celery with a stagger, an inter-user offset and a
quiet-hours delay — which together can be nine hours or more. Nothing re-asked
before the action actually ran.

Three things fell through that window:

* **The console's stop button did not stop anything already queued.** Its
  docstring promises pausing "reaches every account immediately"; in practice
  it set a flag that the executing task never read.
* **A health collapse or a stage demotion arrived too late.** The acceptance
  governor could drop an account into danger and its queued work still went
  out.
* **No rate-limit slot was ever consumed on this path.** ``gate.consume``
  existed, and its docstring said the slot must be taken at execution time, and
  nothing called it. The hourly cap, the cooldown and the rolling weekly cap
  were therefore unenforced on the pipeline, and the pipeline and the warm-up
  runner spent the same account's allowance from two uncoordinated counters.

Every test here is about the window between "we decided to do this" and "we are
about to do this".
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.engagement import ActionStatus, ActionType, EngagementAction
from app.models.integration import IntegrationAccount, Platform
from app.models.post import Post
from app.models.tracked_page import TrackedPage
from app.models.user import User
from app.warmup import planner, program
from app.workers.engagement_tasks import _refuse_if_unsafe


class FakeLimiter:
    """
    An in-memory stand-in for the Redis limiter, with real consumption.

    Records every consumed slot so a test can assert that the pipeline and the
    warm-up runner draw from the *same* counter — which is the whole point of
    the limiter being global.
    """

    def __init__(self, *, per_day_used=0, allow=True):
        self.consumed: list[tuple[str, str]] = []
        self.per_day_used = per_day_used
        self.allow = allow

    async def usage(self, account_id, action):
        used = self.per_day_used + sum(
            1 for a, act in self.consumed if a == account_id and act == action
        )
        return {"hour_used": used, "day_used": used, "week_used": used}

    async def check_and_consume(self, account_id, action, **kwargs):
        from app.safety.rate_policy import RateDecision

        if not self.allow:
            return RateDecision(allowed=False, reason="daily_cap")
        self.consumed.append((account_id, action))
        return RateDecision(allowed=True, reason="ok")


@pytest.fixture
async def account(db, client, auth_headers):
    """A fully warmed account — everything is unlocked, nothing is throttled."""
    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(me["id"])))
    ).scalar_one()

    record = IntegrationAccount(
        id=uuid.uuid4(), user_id=user.id, platform=Platform.LINKEDIN,
        is_active=True, warmup_state={}, daily_caps={},
    )
    planner.set_stage(record, program.FINAL_STAGE)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    record._user = user
    return record


@pytest.fixture
async def action(db, account):
    """A queued like, exactly as the scheduler would have left it."""
    page = TrackedPage(
        id=uuid.uuid4(), org_id=account._user.org_id,
        url="https://www.linkedin.com/company/northwind",
        name="Northwind", platform=Platform.LINKEDIN,
    )
    db.add(page)
    await db.flush()

    post = Post(
        id=uuid.uuid4(), tracked_page_id=page.id, platform=Platform.LINKEDIN,
        url="https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000000",
        external_post_id="urn:li:activity:7100000000000000000",
        content_text="We shipped the activation work.",
    )
    db.add(post)
    await db.flush()

    row = EngagementAction(
        id=uuid.uuid4(), post_id=post.id, user_id=account.user_id,
        action_type=ActionType.LIKE, status=ActionStatus.PENDING,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.fixture
def limiter(monkeypatch):
    """Install a working limiter on the path the executor actually uses."""
    fake = FakeLimiter()
    monkeypatch.setattr("app.safety.rate_policy.get_limiter", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# The window between scheduling and acting
# ---------------------------------------------------------------------------


async def test_a_healthy_account_is_allowed_through(db, account, action, limiter):
    """The control: without this passing, every refusal below is meaningless."""
    assert await _refuse_if_unsafe(db, action, "like") is None


async def test_pausing_after_scheduling_stops_the_queued_action(
    db, account, action, limiter
):
    """
    The console's stop button, as promised.

    An operator who sees a warning on one account and hits pause expects the
    likes queued three hours ago not to go out. Before this check they did.
    """
    planner.set_paused(account, True, "investigating a warning")
    await db.commit()

    reason = await _refuse_if_unsafe(db, action, "like")
    assert reason is not None
    assert "paused" in reason
    assert "investigating a warning" in reason


async def test_a_stage_demotion_after_scheduling_stops_the_action(
    db, account, action, limiter
):
    """
    A challenge demotes the account back to observing. Work that was legal
    when it was queued is not legal any more.
    """
    planner.set_stage(account, program.FIRST_STAGE)
    await db.commit()

    comment = EngagementAction(
        id=uuid.uuid4(), post_id=action.post_id, user_id=account.user_id,
        action_type=ActionType.COMMENT, status=ActionStatus.PENDING,
    )
    db.add(comment)
    await db.commit()

    reason = await _refuse_if_unsafe(db, comment, "comment")
    assert reason is not None
    assert "unlocked" in reason


async def test_disconnecting_the_account_stops_queued_work(
    db, account, action, limiter
):
    account.is_active = False
    await db.commit()

    reason = await _refuse_if_unsafe(db, action, "like")
    assert reason is not None
    assert "no active LinkedIn account" in reason


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


async def test_an_unreachable_limiter_refuses_rather_than_proceeding(
    db, account, action, monkeypatch
):
    """
    Without the shared counters we cannot tell whether this account already
    spent its day in the warm-up runner. A LinkedIn account is worth more than
    one like, so the unverifiable case is a refusal.
    """
    monkeypatch.setattr("app.safety.rate_policy.get_limiter", lambda: None)

    reason = await _refuse_if_unsafe(db, action, "like")
    assert reason is not None
    assert "rate limiter unavailable" in reason


async def test_an_error_inside_the_gate_refuses(db, account, action, monkeypatch):
    """A bug in the safety layer must not read as permission."""

    def _boom():
        raise RuntimeError("redis exploded")

    monkeypatch.setattr("app.safety.rate_policy.get_limiter", _boom)

    reason = await _refuse_if_unsafe(db, action, "like")
    assert reason is not None
    assert "could not be evaluated" in reason


async def test_a_cap_reached_between_scheduling_and_acting_refuses(
    db, account, action, monkeypatch
):
    exhausted = FakeLimiter(allow=False)
    monkeypatch.setattr("app.safety.rate_policy.get_limiter", lambda: exhausted)

    reason = await _refuse_if_unsafe(db, action, "like")
    assert reason is not None
    assert "cap reached" in reason


# ---------------------------------------------------------------------------
# The slot is actually taken
# ---------------------------------------------------------------------------


async def test_acting_consumes_a_slot(db, account, action, limiter):
    """
    The gap that made the hourly cap, the cooldown and the rolling weekly cap
    all unenforced on this path: nothing ever consumed.
    """
    assert limiter.consumed == []
    await _refuse_if_unsafe(db, action, "like")
    assert limiter.consumed == [(str(account.id), program.LIKE)]


async def test_a_refused_action_does_not_consume_a_slot(db, account, action, limiter):
    """A refusal must not spend allowance the account never used."""
    planner.set_paused(account, True, "hold")
    await db.commit()

    await _refuse_if_unsafe(db, action, "like")
    assert limiter.consumed == []


async def test_the_pipeline_and_the_warmup_runner_share_one_counter(
    db, account, action, limiter
):
    """
    Two counters would mean an account could do a full warm-up day *and* a full
    pipeline day — double the volume the programme thinks it is allowing.
    """
    from app.warmup.runner import _consume_slot

    await _consume_slot(account, program.LIKE, limiter, 1.0)
    await _refuse_if_unsafe(db, action, "like")

    assert limiter.consumed == [
        (str(account.id), program.LIKE),
        (str(account.id), program.LIKE),
    ]
    usage = await limiter.usage(str(account.id), program.LIKE)
    assert usage["day_used"] == 2


# ---------------------------------------------------------------------------
# A refusal is not a failure
# ---------------------------------------------------------------------------


async def test_a_refusal_is_a_distinct_terminal_state():
    """
    SKIPPED rather than FAILED, for two reasons that both bite: the stale-action
    sweeper re-queues FAILED rows, so a paused account's work would loop back
    into the state that refused it; and the health funnel reads failures as
    evidence the account is in trouble.
    """
    assert ActionStatus.SKIPPED.value == "skipped"
    assert ActionStatus.SKIPPED != ActionStatus.FAILED


async def test_the_executor_actually_calls_the_gate(db, account, action, monkeypatch):
    """
    The property the rest of this file rests on.

    ``gate.consume`` was correct, documented and *uncalled* — which is how the
    caps went unenforced for the whole of the pipeline's life. A helper that
    refuses correctly is worth nothing if the execution path doesn't run it, so
    assert on the real entry point: a paused account's queued like must reach
    neither the LinkedIn API nor Playwright.
    """
    from app.workers import engagement_tasks

    attempted = []
    monkeypatch.setattr(
        engagement_tasks, "_execute_like",
        lambda *a, **k: attempted.append("like") or True,
    )
    monkeypatch.setattr(
        engagement_tasks, "_execute_comment",
        lambda *a, **k: attempted.append("comment") or True,
    )

    fake = FakeLimiter()
    monkeypatch.setattr("app.safety.rate_policy.get_limiter", lambda: fake)

    planner.set_paused(account, True, "stop button")
    await db.commit()

    # The executor opens its own session, so point it at the test one.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(engagement_tasks, "get_task_session", _session, raising=False)
    monkeypatch.setattr("app.database.get_task_session", _session)

    await engagement_tasks._execute_engagement(str(action.id))

    await db.refresh(action)
    assert action.status == ActionStatus.SKIPPED
    assert "stop button" in (action.error_message or "")
    assert attempted == [], "a paused account still reached LinkedIn"
    assert fake.consumed == []


async def test_the_retry_sweeper_leaves_skipped_actions_alone(db, account, action):
    """
    Retrying a refusal is not merely wasteful — it is the pipeline arguing with
    the safety layer once a minute.
    """
    import inspect

    from app.workers import stale_actions

    action.status = ActionStatus.SKIPPED
    await db.commit()

    source = inspect.getsource(stale_actions)
    # The sweeper selects on FAILED; assert it has no path that picks up
    # SKIPPED rows, so this property can't be lost in a later edit.
    assert "ActionStatus.SKIPPED" not in source
