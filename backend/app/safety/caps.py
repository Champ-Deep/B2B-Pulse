"""
Per-account activity caps, warm-up ramp, and human pacing.

The numbers here are set from how LinkedIn actually behaves, not from what
automation vendors advertise. Two findings drive the whole design:

1. **Invitations are capped weekly, not daily — roughly 100/week, and the same
   for Free, Premium and Sales Navigator.** Premium does not buy more
   invitations. An account can sit under any daily cap and still get restricted
   by the end of the week, which is why :class:`ActionCaps` carries ``per_week``
   and why the limiter enforces a rolling seven-day window.

2. **Acceptance rate restricts accounts more reliably than volume does.** Below
   ~15% acceptance LinkedIn treats the account as spam regardless of how modest
   the volume is; a meaningful share of restricted accounts were inside the
   published limits the whole time. So caps here are a ceiling, and
   ``outreach/health.py`` scales them *down* based on measured acceptance.

Consequence for planning: a target like "800 connections/month" (~185/week) is
about double the standard allowance and is only reachable by an aged, high-SSI
account. The AGGRESSIVE tier below exists so that can be chosen deliberately —
but it is never the default, and the acceptance governor will still pull it back
if the audience isn't responding.

Every number is overridable per account (``ConnectedAccount.daily_caps``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class ActionCaps:
    """Caps and pacing for a single action type."""

    per_hour: int
    per_day: int
    # 0 = no weekly limit modelled for this action. Only invitations really
    # need it, but the field is general.
    per_week: int = 0
    # Minimum seconds between two actions of this type. Real people don't fire
    # invitations four seconds apart.
    cooldown_seconds: int = 60


# ---------------------------------------------------------------------------
# Volume tiers
# ---------------------------------------------------------------------------
#
# A tier is the *ceiling* for an account. Warm-up walks a new account up through
# the lower tiers; the acceptance governor can pull any account back down.

TIERS = {
    # Brand-new, freshly restricted, or recently reconnected accounts.
    # Research consensus: start at 10-15 invitations/day and ramp by ~5/week.
    "warmup": {
        "connect": ActionCaps(per_hour=3, per_day=10, per_week=50, cooldown_seconds=600),
        "message": ActionCaps(per_hour=5, per_day=20, cooldown_seconds=240),
        "comment": ActionCaps(per_hour=3, per_day=10, cooldown_seconds=420),
        "like": ActionCaps(per_hour=10, per_day=40, cooldown_seconds=60),
    },
    # The steady state for a healthy, established account. Comfortably inside
    # the ~100/week invitation cap.
    "standard": {
        "connect": ActionCaps(per_hour=5, per_day=18, per_week=90, cooldown_seconds=420),
        "message": ActionCaps(per_hour=8, per_day=35, cooldown_seconds=180),
        "comment": ActionCaps(per_hour=5, per_day=18, cooldown_seconds=300),
        "like": ActionCaps(per_hour=15, per_day=60, cooldown_seconds=45),
    },
    # For aged accounts with a strong SSI and a proven acceptance rate. This is
    # the tier that approaches vendor-quoted volumes (~185/week). Opt in
    # deliberately; the governor still applies.
    "aggressive": {
        "connect": ActionCaps(per_hour=8, per_day=30, per_week=185, cooldown_seconds=240),
        "message": ActionCaps(per_hour=12, per_day=60, cooldown_seconds=120),
        "comment": ActionCaps(per_hour=8, per_day=25, cooldown_seconds=240),
        "like": ActionCaps(per_hour=20, per_day=90, cooldown_seconds=30),
    },
}

DEFAULT_TIER = "warmup"

# Actions without a tier entry fall back to these.
_FALLBACK = {
    "follow": ActionCaps(per_hour=10, per_day=40, cooldown_seconds=60),
    "post": ActionCaps(per_hour=1, per_day=1, cooldown_seconds=86400),
    "fetch_profile": ActionCaps(per_hour=40, per_day=150, cooldown_seconds=5),
    "fetch_inbox": ActionCaps(per_hour=20, per_day=120, cooldown_seconds=30),
}

# The warm-up ramp: how many days at each tier before an account graduates,
# assuming its acceptance rate stays healthy. Roughly "+5 invitations/day per
# week", expressed as tier promotions.
WARMUP_DAYS = 14
WARMUP_STEP_DAYS = 7

# Ceiling on how many suggestions we put in front of a user per account per day.
# Approval fatigue is a real failure mode: a queue of 200 items gets rubber
# stamped, which defeats the point of human review.
DEFAULT_SUGGESTION_BUDGET = 25

# Hours (account-local) during which actions may run. Outreach that lands at
# 3am reads as automated to both LinkedIn and the recipient.
DEFAULT_ACTIVE_HOURS = (8, 19)

# Weekend activity is heavily reduced rather than zero -- some people do post on
# a Sunday, but a bot that works exactly 7 days a week is conspicuous.
WEEKEND_MULTIPLIER = 0.25


def _settings(account) -> dict:
    raw = getattr(account, "daily_caps", None)
    return raw if isinstance(raw, dict) else {}


def tier_of(account) -> str:
    """
    The volume tier this account is currently allowed to run at.

    An explicit ``tier`` override wins. Otherwise the account is walked up the
    ramp based on how long it has been connected: brand-new accounts sit in
    ``warmup`` and graduate to ``standard`` once they've been active long
    enough without trouble.
    """
    settings = _settings(account)
    explicit = settings.get("tier")
    if explicit in TIERS:
        return explicit

    created = getattr(account, "created_at", None)
    if created is None:
        return DEFAULT_TIER
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    age_days = (datetime.now(timezone.utc) - created).days
    return "standard" if age_days >= WARMUP_DAYS else DEFAULT_TIER


def caps_for(account, action: str, *, throttle: float = 1.0) -> ActionCaps:
    """
    Resolve the effective caps for ``account`` + ``action``.

    Order: per-account override -> tier -> fallback, then ``throttle`` scales
    the result down. ``throttle`` comes from the acceptance-rate governor and is
    only ever <= 1.0: measured behaviour can slow an account, never speed it up
    past its tier.
    """
    tier = TIERS.get(tier_of(account), TIERS[DEFAULT_TIER])
    base = tier.get(action) or _FALLBACK.get(
        action, ActionCaps(per_hour=5, per_day=20, cooldown_seconds=120)
    )

    settings = _settings(account)
    entry = settings.get(action)
    if isinstance(entry, dict):
        base = ActionCaps(
            per_hour=int(entry.get("per_hour", base.per_hour)),
            per_day=int(entry.get("per_day", base.per_day)),
            per_week=int(entry.get("per_week", base.per_week)),
            cooldown_seconds=int(entry.get("cooldown_seconds", base.cooldown_seconds)),
        )
    elif isinstance(entry, int):
        # Shorthand: a bare integer sets the daily cap only.
        base = ActionCaps(
            per_hour=min(base.per_hour, entry),
            per_day=entry,
            per_week=base.per_week,
            cooldown_seconds=base.cooldown_seconds,
        )

    if throttle < 1.0:
        base = ActionCaps(
            per_hour=max(1, int(base.per_hour * throttle)),
            per_day=max(1, int(base.per_day * throttle)),
            per_week=max(1, int(base.per_week * throttle)) if base.per_week else 0,
            # Slower cadence as well as lower volume: a throttled account should
            # look calmer, not just do less in the same bursts.
            cooldown_seconds=int(base.cooldown_seconds / max(throttle, 0.1)),
        )
    return base


def suggestion_budget(account) -> int:
    """How many new suggestions may be generated for this account today."""
    settings = _settings(account)
    if settings.get("suggestion_budget"):
        return int(settings["suggestion_budget"])
    return DEFAULT_SUGGESTION_BUDGET


def active_hours(account) -> tuple:
    """The (start_hour, end_hour) window during which this account acts."""
    window = _settings(account).get("active_hours")
    if isinstance(window, (list, tuple)) and len(window) == 2:
        return int(window[0]), int(window[1])
    return DEFAULT_ACTIVE_HOURS


def timezone_of(account) -> Optional[str]:
    """IANA timezone the account's active hours are interpreted in."""
    return _settings(account).get("timezone")


def default_caps_payload(tier: str = DEFAULT_TIER) -> dict:
    """The settings blob written onto a newly connected account."""
    return {
        "tier": tier,
        "suggestion_budget": DEFAULT_SUGGESTION_BUDGET,
        "active_hours": list(DEFAULT_ACTIVE_HOURS),
    }


def describe(account, *, throttle: float = 1.0) -> dict:
    """Human-readable snapshot of an account's effective policy (for the UI)."""
    actions = ("connect", "message", "comment", "like")
    tier = tier_of(account)
    return {
        "tier": tier,
        "warmup": tier == "warmup",
        "actions": {a: asdict(caps_for(account, a, throttle=throttle)) for a in actions},
        "suggestion_budget": suggestion_budget(account),
        "active_hours": list(active_hours(account)),
        "timezone": timezone_of(account) or "UTC",
        "throttle": round(throttle, 2),
        "weekend_multiplier": WEEKEND_MULTIPLIER,
    }
