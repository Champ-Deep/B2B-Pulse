"""
Global, per-LinkedIn-account rate policy.

The legacy limiter in ``interaction_agent.py`` tracks usage in in-memory dicts
on each agent instance. Because the interaction pool runs 2-8 instances, the
real caps get multiplied by the instance count and reset on restart -- so
"150 likes/day" was never actually enforced per account.

``AccountRateLimiter`` fixes that by keeping the sliding-window counters in
Redis, keyed per ``connected_account_id`` + action. Every interaction agent
instance shares the same counters, so the cap is a true global cap for the
LinkedIn account no matter how many workers are running.

Semantics that matter for a cap enforcer:
- A slot is consumed ONLY when the action is allowed. A rejected check never
  inflates the window (unlike the legacy ``RateLimiter.check_rate_limit``).
- Hourly cap, daily cap, and inter-action cooldown are evaluated together.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

HOUR_SECONDS = 3600
DAY_SECONDS = 86400
# LinkedIn enforces invitations on a *rolling week*, not a calendar week: the
# allowance frees up seven days after each invitation was sent. Modelling this
# as a sliding window is the only way to respect the limit that actually gets
# accounts restricted.
WEEK_SECONDS = 604800


@dataclass(frozen=True)
class RateDecision:
    """Result of a rate check."""
    allowed: bool
    reason: str = ""
    hour_used: int = 0
    day_used: int = 0
    week_used: int = 0
    retry_after_seconds: int = 0

    def __bool__(self) -> bool:  # allow `if decision:`
        return self.allowed


class AccountRateLimiter:
    """
    Redis-backed sliding-window limiter keyed per (account, action).

    One sorted set per (account, action) holds the unix timestamps of allowed
    actions, scored by timestamp. Hourly usage = members in the last hour;
    daily usage = members in the last day; cooldown = time since the newest
    member. Old members are trimmed on every check so the set stays bounded.
    """

    def __init__(self, redis_client, clock: Optional[Callable[[], float]] = None):
        """
        Args:
            redis_client: an ``redis.asyncio.Redis`` (or compatible) client.
            clock: callable returning unix seconds; injectable for tests.
        """
        self.redis = redis_client
        self._clock = clock or time.time

    @staticmethod
    def _key(account_id: str, action: str) -> str:
        return f"rl:{account_id}:{action}"

    async def check_and_consume(
        self,
        account_id: str,
        action: str,
        per_hour: int,
        per_day: int,
        cooldown_seconds: int = 0,
        per_week: int = 0,
    ) -> RateDecision:
        """
        Check the caps and, if allowed, atomically consume one slot.

        Returns a :class:`RateDecision`. When ``allowed`` is False no slot is
        consumed and ``retry_after_seconds`` is a best-effort hint.

        ``per_week`` matters most for invitations: LinkedIn's real invitation
        limit is weekly, so an account can sit comfortably under its daily cap
        every day and still be restricted by Friday.
        """
        now = int(self._clock())
        key = self._key(account_id, action)
        hour_start = now - HOUR_SECONDS
        day_start = now - DAY_SECONDS
        week_start = now - WEEK_SECONDS

        # Retain a full week so the weekly window can be evaluated, then read
        # usage across all three windows plus the newest timestamp.
        trim_pipe = self.redis.pipeline()
        trim_pipe.zremrangebyscore(key, 0, week_start)
        trim_pipe.zcount(key, hour_start, now)          # hourly usage
        trim_pipe.zcount(key, day_start, now)           # daily usage
        trim_pipe.zcard(key)                            # weekly usage (post-trim)
        trim_pipe.zrange(key, -1, -1, withscores=True)  # newest entry
        _, hour_used, day_used, week_used, newest = await trim_pipe.execute()

        def decision(reason: str, retry_after: int) -> RateDecision:
            return RateDecision(
                allowed=False,
                reason=reason,
                hour_used=hour_used,
                day_used=day_used,
                week_used=week_used,
                retry_after_seconds=retry_after,
            )

        # Cooldown: seconds since the most recent allowed action.
        if cooldown_seconds and newest:
            last_ts = int(newest[0][1])
            elapsed = now - last_ts
            if elapsed < cooldown_seconds:
                return decision("cooldown", cooldown_seconds - elapsed)

        if per_week and week_used >= per_week:
            # Retry when the oldest entry in the window ages out, not in a
            # flat week -- a rolling window frees up continuously.
            oldest = await self.redis.zrange(key, 0, 0, withscores=True)
            retry = WEEK_SECONDS
            if oldest:
                retry = max(60, int(oldest[0][1]) + WEEK_SECONDS - now)
            return decision("weekly_cap", retry)

        if per_day and day_used >= per_day:
            return decision("daily_cap", DAY_SECONDS)

        if per_hour and hour_used >= per_hour:
            return decision("hourly_cap", HOUR_SECONDS)

        # Allowed -> consume one slot. Member must be unique; use a monotonic
        # suffix so two actions in the same second don't collapse to one entry.
        member = f"{now}:{await self.redis.incr(f'{key}:seq')}"
        consume_pipe = self.redis.pipeline()
        consume_pipe.zadd(key, {member: now})
        consume_pipe.expire(key, WEEK_SECONDS)
        consume_pipe.expire(f"{key}:seq", WEEK_SECONDS)
        await consume_pipe.execute()

        return RateDecision(
            allowed=True,
            reason="ok",
            hour_used=hour_used + 1,
            day_used=day_used + 1,
            week_used=week_used + 1,
        )

    async def usage(self, account_id: str, action: str) -> dict:
        """Read current hourly/daily/weekly usage without consuming a slot."""
        now = int(self._clock())
        key = self._key(account_id, action)
        pipe = self.redis.pipeline()
        pipe.zcount(key, now - HOUR_SECONDS, now)
        pipe.zcount(key, now - DAY_SECONDS, now)
        pipe.zcount(key, now - WEEK_SECONDS, now)
        hour_used, day_used, week_used = await pipe.execute()
        return {"hour_used": hour_used, "day_used": day_used, "week_used": week_used}
