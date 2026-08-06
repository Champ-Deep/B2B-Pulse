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


# The decision and the consumption, as one indivisible server-side step.
#
# Doing this in Python — read the counts, decide, then write — is a
# check-then-act race, and not a theoretical one. Under eight Celery workers
# handling a burst, every caller reads the same pre-consumption count and every
# caller concludes it has headroom. Measured against this module's own test
# harness: 60 actions allowed against a cap of 20.
#
# That is the worst possible place for a race. The weekly invitation cap exists
# precisely because LinkedIn restricts accounts near ~100 invitations a week, so
# a limiter that can be overrun 3x under load is not a limiter at all.
#
# Redis executes a script atomically, so no other client can observe or modify
# the sorted set between the count and the ZADD.
_CHECK_AND_CONSUME_LUA = """
local key = KEYS[1]
local seq_key = KEYS[2]
local now = tonumber(ARGV[1])
local hour_start = tonumber(ARGV[2])
local day_start = tonumber(ARGV[3])
local week_start = tonumber(ARGV[4])
local per_hour = tonumber(ARGV[5])
local per_day = tonumber(ARGV[6])
local per_week = tonumber(ARGV[7])
local cooldown = tonumber(ARGV[8])
local week_seconds = tonumber(ARGV[9])

redis.call('ZREMRANGEBYSCORE', key, 0, week_start)

local hour_used = redis.call('ZCOUNT', key, hour_start, now)
local day_used  = redis.call('ZCOUNT', key, day_start, now)
local week_used = redis.call('ZCARD', key)

if cooldown > 0 then
  local newest = redis.call('ZRANGE', key, -1, -1, 'WITHSCORES')
  if newest[2] then
    local elapsed = now - tonumber(newest[2])
    if elapsed < cooldown then
      return {0, 'cooldown', hour_used, day_used, week_used, cooldown - elapsed}
    end
  end
end

if per_week > 0 and week_used >= per_week then
  -- A rolling window frees up continuously: retry when the oldest entry ages
  -- out, not in a flat week.
  local retry = week_seconds
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  if oldest[2] then
    retry = math.max(60, tonumber(oldest[2]) + week_seconds - now)
  end
  return {0, 'weekly_cap', hour_used, day_used, week_used, retry}
end

if per_day > 0 and day_used >= per_day then
  return {0, 'daily_cap', hour_used, day_used, week_used, 86400}
end

if per_hour > 0 and hour_used >= per_hour then
  return {0, 'hourly_cap', hour_used, day_used, week_used, 3600}
end

-- Allowed: consume one slot. The member must be unique, or two actions in the
-- same second would collapse into a single sorted-set entry and the cap would
-- silently under-count.
local seq = redis.call('INCR', seq_key)
redis.call('ZADD', key, now, now .. ':' .. seq)
redis.call('EXPIRE', key, week_seconds)
redis.call('EXPIRE', seq_key, week_seconds)

return {1, 'ok', hour_used + 1, day_used + 1, week_used + 1, 0}
"""


def _text(value) -> str:
    """Lua returns bulk strings, which redis-py may hand back as bytes."""
    return value.decode() if isinstance(value, bytes) else str(value)


class RateLimiterUnavailable(RuntimeError):
    """
    The limiter could not evaluate a cap.

    Raised rather than returning a refusal so no caller can mistake "Redis is
    unreachable" for "you are over your cap" — one is a transient outage the
    operator should see, the other is normal operation.
    """


class AccountRateLimiter:
    """
    Redis-backed sliding-window limiter keyed per (account, action).

    One sorted set per (account, action) holds the unix timestamps of allowed
    actions, scored by timestamp. Hourly usage = members in the last hour;
    daily usage = members in the last day; weekly usage = the whole (trimmed)
    set. Old members are trimmed on every check so the set stays bounded.

    Concurrency
    -----------
    The check and the consumption happen inside one Lua script, so they are
    atomic with respect to every other worker. This is load-bearing rather than
    tidy: see the comment above :data:`_CHECK_AND_CONSUME_LUA`.
    """

    def __init__(self, redis_client, clock: Optional[Callable[[], float]] = None):
        """
        Args:
            redis_client: an ``redis.asyncio.Redis`` (or compatible) client.
            clock: callable returning unix seconds; injectable for tests.
        """
        self.redis = redis_client
        self._clock = clock or time.time
        self._script = None

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

        Raises:
            RateLimiterUnavailable: Redis could not answer. Deliberately an
                exception rather than a refusal, so no caller can read an
                outage as "over cap", and a limiter that has stopped working
                can never silently allow.
        """
        now = int(self._clock())
        key = self._key(account_id, action)

        try:
            raw = await self._eval(
                key,
                now,
                per_hour=per_hour,
                per_day=per_day,
                per_week=per_week,
                cooldown_seconds=cooldown_seconds,
            )
        except RateLimiterUnavailable:
            raise
        except Exception as exc:  # a limiter that cannot answer must not allow
            raise RateLimiterUnavailable(str(exc)) from exc

        allowed, reason, hour_used, day_used, week_used, retry_after = raw
        return RateDecision(
            allowed=bool(int(allowed)),
            reason=_text(reason),
            hour_used=int(hour_used),
            day_used=int(day_used),
            week_used=int(week_used),
            retry_after_seconds=int(retry_after),
        )

    async def _eval(
        self,
        key: str,
        now: int,
        *,
        per_hour: int,
        per_day: int,
        per_week: int,
        cooldown_seconds: int,
    ):
        """Run the atomic decide-and-consume script."""
        args = [
            now,
            now - HOUR_SECONDS,
            now - DAY_SECONDS,
            now - WEEK_SECONDS,
            per_hour or 0,
            per_day or 0,
            per_week or 0,
            cooldown_seconds or 0,
            WEEK_SECONDS,
        ]
        keys = [key, f"{key}:seq"]

        # register_script uses EVALSHA with an EVAL fallback, so the script body
        # crosses the wire once per server rather than once per action.
        if self._script is None:
            register = getattr(self.redis, "register_script", None)
            if register is not None:
                self._script = register(_CHECK_AND_CONSUME_LUA)

        if self._script is not None:
            return await self._script(keys=keys, args=args)
        return await self.redis.eval(_CHECK_AND_CONSUME_LUA, len(keys), *keys, *args)

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


def get_limiter():
    """
    The process-wide limiter, or ``None`` if Redis is unreachable.

    Lives here rather than in one worker module because *every* path that can
    reach LinkedIn has to share one set of counters — a second limiter, or a
    path that quietly runs without one, means the cap is not a cap. Callers
    treat ``None`` as "cannot prove safety, so do nothing".
    """
    import logging

    try:
        import redis.asyncio as aioredis

        from app.config import settings

        return AccountRateLimiter(
            aioredis.from_url(settings.redis_url, decode_responses=True)
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Rate limiter unavailable, capped work will not run: %s", exc
        )
        return None
