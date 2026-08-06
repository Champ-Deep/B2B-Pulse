"""
Human pacing: when an approved action is actually allowed to happen.

Caps answer "how much"; pacing answers "when", and the second question is the
one bot-detection actually watches. Twenty invitations spread naturally across
a Tuesday afternoon is a person. The same twenty fired in ninety seconds at
04:12 is software, regardless of whether it stayed under the daily cap.

Rules applied here:
- Actions only run inside the account's configured active hours.
- Weekends are heavily reduced rather than zero — a schedule that is exactly
  Monday-to-Friday, 9-to-5, every week, is its own kind of signature.
- Every send gets randomized jitter, so intervals never form a pattern.
- Consecutive sends are spaced by at least the action's cooldown.

Randomness is injectable so tests are deterministic.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional

from app.safety import caps as caps_policy

# Spread across the working window rather than firing at the boundary.
_MIN_JITTER_SECONDS = 45
_MAX_JITTER_SECONDS = 900


def _tz(account) -> timezone:
    """
    The account's timezone.

    Falls back to UTC. (A real IANA implementation via zoneinfo is a small
    upgrade; the offset only shifts the activity window, and getting the window
    roughly right already removes the 3am-outreach failure mode.)
    """
    name = caps_policy.timezone_of(account)
    if not name:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def is_within_active_hours(account, at: Optional[datetime] = None) -> bool:
    """Is ``at`` inside the account's configured activity window?"""
    tz = _tz(account)
    moment = (at or datetime.now(timezone.utc)).astimezone(tz)
    start, end = caps_policy.active_hours(account)
    return start <= moment.hour < end


def next_active_moment(account, after: Optional[datetime] = None) -> datetime:
    """The first instant at or after ``after`` inside the activity window."""
    tz = _tz(account)
    start, end = caps_policy.active_hours(account)
    moment = (after or datetime.now(timezone.utc)).astimezone(tz)

    if moment.hour < start:
        moment = moment.replace(hour=start, minute=0, second=0, microsecond=0)
    elif moment.hour >= end:
        moment = (moment + timedelta(days=1)).replace(
            hour=start, minute=0, second=0, microsecond=0
        )
    return moment.astimezone(timezone.utc)


def schedule_next(
    account,
    action: str,
    *,
    after: Optional[datetime] = None,
    last_sent_at: Optional[datetime] = None,
    rng: Optional[Callable[[int, int], int]] = None,
) -> datetime:
    """
    Compute when an approved action should fire.

    Honors the action cooldown relative to ``last_sent_at``, adds jitter, and
    pushes the result into the next active window if it would land outside one.
    """
    randint = rng or random.randint
    now = after or datetime.now(timezone.utc)
    caps = caps_policy.caps_for(account, action)

    earliest = now
    if last_sent_at is not None:
        cooldown_end = _aware(last_sent_at) + timedelta(seconds=caps.cooldown_seconds)
        earliest = max(earliest, cooldown_end)

    jitter = randint(_MIN_JITTER_SECONDS, max(_MIN_JITTER_SECONDS + 1, _MAX_JITTER_SECONDS))
    candidate = earliest + timedelta(seconds=jitter)

    if not is_within_active_hours(account, candidate):
        candidate = next_active_moment(account, candidate)
        # Don't have everything queue up on the stroke of the opening hour.
        candidate += timedelta(seconds=randint(0, 3600))

    return candidate


def weekend_allowance(account, at: Optional[datetime] = None) -> float:
    """
    Multiplier applied to the day's allowance on weekends.

    Returns 1.0 on weekdays, a reduced fraction on Saturday and Sunday.
    """
    moment = (at or datetime.now(timezone.utc)).astimezone(_tz(account))
    if moment.weekday() >= 5:
        return caps_policy.WEEKEND_MULTIPLIER
    return 1.0


def _aware(value: datetime) -> datetime:
    """Treat naive datetimes (SQLite round-trips) as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def describe_schedule(account) -> dict:
    """Human-readable pacing summary for the UI."""
    start, end = caps_policy.active_hours(account)
    return {
        "active_hours": [start, end],
        "timezone": caps_policy.timezone_of(account) or "UTC",
        "weekend_multiplier": caps_policy.WEEKEND_MULTIPLIER,
        "jitter_seconds": [_MIN_JITTER_SECONDS, _MAX_JITTER_SECONDS],
    }


__all__ = [
    "is_within_active_hours",
    "next_active_moment",
    "schedule_next",
    "weekend_allowance",
    "describe_schedule",
    "time",  # re-exported for callers building custom windows
]
