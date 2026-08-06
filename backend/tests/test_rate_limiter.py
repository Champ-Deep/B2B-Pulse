"""
The global rate limiter, under concurrency.

The audit that produced this file found the limiter's caps were not caps. The
old implementation read the window counts in one round trip, decided in Python,
then consumed in a second round trip. That is check-then-act, and with eight
Celery workers draining a burst every caller reads the same pre-consumption
count and every caller concludes it has headroom. Measured with the harness
below: **60 actions allowed against a cap of 20**.

This is the single worst place in the product for a race. The weekly cap exists
because LinkedIn restricts accounts near ~100 invitations a week, and the
research is unambiguous that going over is what gets accounts pulled. A limiter
that can be overrun 3x under load provides no protection precisely when load —
a busy day, a backlog, a retry storm — makes protection matter most.

The fix is to do the whole decision inside a Lua script, which Redis executes
atomically. These tests are mostly about proving that under contention, because
a single-threaded test cannot see this class of bug at all.
"""

import asyncio

import fakeredis.aioredis
import pytest

from app.safety.rate_policy import (
    DAY_SECONDS,
    HOUR_SECONDS,
    WEEK_SECONDS,
    AccountRateLimiter,
    RateLimiterUnavailable,
)


class LatentPipeline:
    """A pipeline whose ``execute`` yields, as a real network round trip does."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def execute(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await self._inner.execute(*args, **kwargs)


class LatentRedis:
    """
    fakeredis, but with a scheduling point on every round trip.

    Without this the fake resolves synchronously and coroutines never interleave,
    so a check-then-act race is invisible — the test would pass against the very
    implementation that loses invitations in production. Latency is not an
    incidental detail of the bug; it *is* the bug's precondition.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def pipeline(self, *args, **kwargs):
        return LatentPipeline(self._inner.pipeline(*args, **kwargs))

    async def eval(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await self._inner.eval(*args, **kwargs)

    def register_script(self, script):
        inner_script = self._inner.register_script(script)

        async def _run(keys=None, args=None):
            await asyncio.sleep(0)
            return await inner_script(keys=keys or [], args=args or [])

        return _run


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def limiter(redis_client):
    return AccountRateLimiter(LatentRedis(redis_client))


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------


async def test_a_burst_cannot_exceed_the_daily_cap(limiter):
    """
    The headline property, and the one the old implementation failed.

    Sixty concurrent attempts, a cap of twenty. Exactly twenty may pass.
    """
    results = await asyncio.gather(
        *[
            limiter.check_and_consume("acct", "like", per_hour=1000, per_day=20)
            for _ in range(60)
        ]
    )

    allowed = sum(1 for r in results if r.allowed)
    assert allowed == 20, f"{allowed - 20} actions escaped the cap"

    usage = await limiter.usage("acct", "like")
    assert usage["day_used"] == 20


async def test_a_burst_cannot_exceed_the_weekly_cap(limiter):
    """
    The cap that actually gets accounts restricted.

    LinkedIn's invitation limit is a rolling week and applies identically to
    Free, Premium and Sales Navigator. Overrunning it is the single most
    reliable way to lose an account, so it must hold under contention.
    """
    results = await asyncio.gather(
        *[
            limiter.check_and_consume(
                "acct", "invitation", per_hour=1000, per_day=1000, per_week=90
            )
            for _ in range(300)
        ]
    )

    assert sum(1 for r in results if r.allowed) == 90
    assert (await limiter.usage("acct", "invitation"))["week_used"] == 90


async def test_a_burst_cannot_exceed_the_hourly_cap(limiter):
    results = await asyncio.gather(
        *[
            limiter.check_and_consume("acct", "like", per_hour=5, per_day=1000)
            for _ in range(50)
        ]
    )
    assert sum(1 for r in results if r.allowed) == 5


async def test_concurrent_accounts_do_not_interfere(limiter):
    """Caps are per account; five colleagues each get their own twenty."""
    results = await asyncio.gather(
        *[
            limiter.check_and_consume(f"acct-{i % 5}", "like", per_hour=1000, per_day=20)
            for i in range(200)
        ]
    )
    assert sum(1 for r in results if r.allowed) == 100

    for i in range(5):
        assert (await limiter.usage(f"acct-{i}", "like"))["day_used"] == 20


async def test_concurrent_actions_do_not_share_a_counter(limiter):
    """Likes must not eat the invitation allowance, or vice versa."""
    await asyncio.gather(
        *[limiter.check_and_consume("acct", "like", 1000, 20) for _ in range(40)],
        *[limiter.check_and_consume("acct", "invitation", 1000, 5) for _ in range(40)],
    )
    assert (await limiter.usage("acct", "like"))["day_used"] == 20
    assert (await limiter.usage("acct", "invitation"))["day_used"] == 5


async def test_two_actions_in_the_same_second_are_counted_separately(limiter):
    """
    A sorted set keyed on the timestamp alone would collapse same-second
    actions into one member, and the cap would silently under-count — the same
    class of bug as the race, just quieter.
    """
    limiter._clock = lambda: 1_700_000_000

    for _ in range(5):
        assert (await limiter.check_and_consume("acct", "like", 1000, 1000)).allowed

    assert (await limiter.usage("acct", "like"))["day_used"] == 5


# ---------------------------------------------------------------------------
# A refusal costs nothing
# ---------------------------------------------------------------------------


async def test_a_refusal_does_not_consume_a_slot(limiter):
    """
    Otherwise a hammered limiter would push its own window further out — the
    account would be punished for the caller's retry loop rather than for
    anything it did on LinkedIn.
    """
    for _ in range(3):
        await limiter.check_and_consume("acct", "like", per_hour=1000, per_day=3)

    for _ in range(20):
        decision = await limiter.check_and_consume("acct", "like", per_hour=1000, per_day=3)
        assert not decision.allowed

    assert (await limiter.usage("acct", "like"))["day_used"] == 3


async def test_a_cooldown_refusal_does_not_consume_either(limiter):
    now = [1_700_000_000]
    limiter._clock = lambda: now[0]

    assert (await limiter.check_and_consume("a", "like", 1000, 1000, cooldown_seconds=60)).allowed

    now[0] += 5
    blocked = await limiter.check_and_consume("a", "like", 1000, 1000, cooldown_seconds=60)
    assert not blocked.allowed
    assert blocked.reason == "cooldown"
    assert blocked.retry_after_seconds == 55

    assert (await limiter.usage("a", "like"))["day_used"] == 1


# ---------------------------------------------------------------------------
# The windows really roll
# ---------------------------------------------------------------------------


async def test_the_daily_window_is_rolling_not_calendar(limiter):
    now = [1_700_000_000]
    limiter._clock = lambda: now[0]

    for _ in range(5):
        await limiter.check_and_consume("a", "like", 1000, 5)
    assert not (await limiter.check_and_consume("a", "like", 1000, 5)).allowed

    # Not midnight — just far enough that the first action has aged out.
    now[0] += DAY_SECONDS + 1
    assert (await limiter.check_and_consume("a", "like", 1000, 5)).allowed


async def test_the_weekly_window_frees_up_continuously(limiter):
    """
    A rolling week, not a Monday reset: the allowance returns seven days after
    each invitation, which is how LinkedIn actually accounts for it.
    """
    now = [1_700_000_000]
    limiter._clock = lambda: now[0]

    for _ in range(3):
        await limiter.check_and_consume("a", "invitation", 1000, 1000, per_week=3)
        now[0] += 3600

    blocked = await limiter.check_and_consume("a", "invitation", 1000, 1000, per_week=3)
    assert not blocked.allowed
    assert blocked.reason == "weekly_cap"
    # The hint points at when the oldest entry ages out, not a flat week.
    assert 0 < blocked.retry_after_seconds <= WEEK_SECONDS

    now[0] += WEEK_SECONDS
    assert (await limiter.check_and_consume("a", "invitation", 1000, 1000, per_week=3)).allowed


async def test_the_hourly_window_rolls(limiter):
    now = [1_700_000_000]
    limiter._clock = lambda: now[0]

    for _ in range(3):
        await limiter.check_and_consume("a", "like", per_hour=3, per_day=1000)
    assert not (await limiter.check_and_consume("a", "like", per_hour=3, per_day=1000)).allowed

    now[0] += HOUR_SECONDS + 1
    assert (await limiter.check_and_consume("a", "like", per_hour=3, per_day=1000)).allowed


async def test_the_strictest_window_wins(limiter):
    """Under cap daily, over cap hourly: the hourly refusal must be the answer."""
    for _ in range(2):
        await limiter.check_and_consume("a", "like", per_hour=2, per_day=100)

    decision = await limiter.check_and_consume("a", "like", per_hour=2, per_day=100)
    assert not decision.allowed
    assert decision.reason == "hourly_cap"


async def test_the_weekly_cap_beats_a_generous_daily_one(limiter):
    """
    The scenario the weekly window was added for: an account comfortably under
    18 invitations a day, every day, and restricted by Friday anyway.
    """
    now = [1_700_000_000]
    limiter._clock = lambda: now[0]

    sent = 0
    for _day in range(7):
        # A real working day: eighteen invitations spread over nine hours, not
        # eighteen at one instant.
        for _ in range(18):
            if (
                await limiter.check_and_consume(
                    "a", "invitation", per_hour=100, per_day=18, per_week=90
                )
            ).allowed:
                sent += 1
            now[0] += 1800
        now[0] += DAY_SECONDS - 18 * 1800  # sleep until tomorrow

    assert sent == 90, "the weekly cap should bind before the daily one does"
    assert sent < 7 * 18, "and it should bind before the week is out"


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


async def test_an_unreachable_redis_raises_rather_than_allowing():
    """
    The failure mode that matters. Returning "allowed" on an error would mean
    the caps evaporate exactly when the infrastructure is unhealthy — and
    returning a plain refusal would hide an outage behind a normal-looking
    "over cap" message.
    """

    class DeadRedis:
        def register_script(self, script):
            raise ConnectionError("redis is down")

        async def eval(self, *args, **kwargs):
            raise ConnectionError("redis is down")

    limiter = AccountRateLimiter(DeadRedis())

    with pytest.raises(RateLimiterUnavailable):
        await limiter.check_and_consume("a", "like", 10, 10)


async def test_the_warmup_runner_treats_an_outage_as_a_refusal():
    """The exception must not become an unhandled crash mid-plan, either."""
    from app.warmup.runner import _consume_slot

    class DeadLimiter:
        async def check_and_consume(self, *args, **kwargs):
            raise RateLimiterUnavailable("redis is down")

    class _Account:
        id = "acct"
        daily_caps = {}
        warmup_state = {}

    assert await _consume_slot(_Account(), "like", DeadLimiter(), 1.0) is False


async def test_the_gate_treats_an_outage_as_a_refusal(db):
    from app.warmup import gate

    class DeadLimiter:
        async def check_and_consume(self, *args, **kwargs):
            raise RateLimiterUnavailable("redis is down")

    class _Account:
        id = "acct"
        daily_caps = {}
        warmup_state = {}

    assert await gate.consume(db, _Account(), "like", DeadLimiter()) is False


async def test_no_limiter_at_all_refuses_by_default(db):
    """
    ``ALLOW_UNCAPPED_SENDING`` is the deliberate, documented escape hatch. Its
    default must be off, or a missing Redis silently uncaps the product.
    """
    from app.warmup import gate

    assert await gate.consume(db, object(), "like", None) is False
