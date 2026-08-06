"""
The daily activity planner.

Turns "this account is on day 3 of the react stage" into "today, do 9 likes and
2 follows, spread between 09:12 and 17:40". This is what makes a warming account
look like a person using LinkedIn rather than a process executing a quota.

Three properties matter more than the exact numbers:

- **Variance.** Volumes are sampled from a band, not fixed. An account that
  does exactly 12 likes every day is as detectable as one that does 500.
- **Quiet days.** Optional actions carry a probability below 1.0, so some days
  produce nothing. Real people miss days; schedulers don't.
- **Irregular timing.** Actions are scattered across the active-hours window
  with uneven gaps, never on a fixed interval.

The plan is *deterministic per (account, day)*: the same account asked twice on
the same day gets the same plan, so re-running the planner is safe and the
result is testable. It changes tomorrow because the seed includes the date.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

from app.safety import caps as caps_policy
from app.warmup import program


@dataclass
class PlannedAction:
    """One scheduled action in today's plan."""

    action: str
    at: datetime
    # Why this is happening, for the activity feed.
    reason: str = ""


@dataclass
class DailyPlan:
    """Everything an account should do today."""

    account_id: str
    day: date
    stage: str
    stage_name: str
    intent: str
    actions: List[PlannedAction] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def counts(self) -> dict:
        totals: dict = {}
        for item in self.actions:
            totals[item.action] = totals.get(item.action, 0) + 1
        return totals

    def for_action(self, action: str) -> List[PlannedAction]:
        return [a for a in self.actions if a.action == action]


def _seeded(account_id: str, day: date) -> random.Random:
    """Deterministic RNG per account per day."""
    digest = hashlib.sha256(f"{account_id}:{day.isoformat()}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _sample(band: program.VolumeBand, rng: random.Random) -> int:
    """Draw a day's volume from a band, honouring its quiet-day probability."""
    if band.probability < 1.0 and rng.random() > band.probability:
        return 0
    return rng.randint(band.low, band.high)


def _scatter(
    count: int,
    start_hour: int,
    end_hour: int,
    day: date,
    rng: random.Random,
    tz: timezone = timezone.utc,
) -> List[datetime]:
    """
    Place ``count`` actions unevenly across the working window.

    Uniform random placement, sorted, with a minimum gap enforced afterwards —
    this produces the clustered-then-quiet rhythm real usage has, rather than
    the even spacing a naive scheduler would emit.
    """
    if count <= 0:
        return []

    window_start = datetime.combine(day, time(hour=start_hour), tzinfo=tz)
    window_seconds = max(1, (end_hour - start_hour) * 3600)

    offsets = sorted(rng.uniform(0, window_seconds) for _ in range(count))

    # Enforce a minimum gap so nothing fires in a burst.
    min_gap = min(180, window_seconds // max(count, 1))
    spaced: List[float] = []
    for offset in offsets:
        if spaced and offset - spaced[-1] < min_gap:
            offset = spaced[-1] + min_gap
        spaced.append(min(offset, window_seconds))

    return [window_start + timedelta(seconds=int(o)) for o in spaced]


def plan_day(
    account,
    *,
    day: Optional[date] = None,
    stage_key: Optional[str] = None,
    throttle: float = 1.0,
    now: Optional[datetime] = None,
) -> DailyPlan:
    """
    Build today's activity plan for an account.

    The stage decides *what* is permitted; the caps decide the ceiling; the
    throttle (from the acceptance governor) can pull everything down. Weekends
    are heavily reduced rather than skipped, because an account that works
    exactly Monday-to-Friday is its own signature.
    """
    now = now or datetime.now(timezone.utc)
    day = day or now.date()
    account_id = str(getattr(account, "id", "unknown"))

    stage = program.stage_for(stage_key or current_stage(account))
    rng = _seeded(account_id, day)
    start_hour, end_hour = _working_window(account, account_id)

    plan = DailyPlan(
        account_id=account_id,
        day=day,
        stage=stage.key,
        stage_name=stage.name,
        intent=stage.intent,
    )

    weekend = day.weekday() >= 5
    weekend_factor = caps_policy.WEEKEND_MULTIPLIER if weekend else 1.0
    if weekend:
        plan.notes.append("Weekend — activity reduced, not stopped")
    if throttle < 1.0:
        plan.notes.append(
            f"Throttled to {throttle:.0%} of normal volume by account health"
        )

    if _is_day_off(account_id, day):
        plan.notes.append(
            "A day off — nobody engages every single day for a month"
        )
        return plan

    for action in sorted(stage.allowed):
        band = stage.volumes.get(action)
        if band is None:
            continue

        count = _daily_count(account, action, band, day, throttle=throttle)
        if count == 0:
            continue

        # And never exceed what is left in the rolling week.
        count = min(count, _weekly_headroom(account, action, stage, day, throttle))
        if count == 0:
            plan.notes.append(
                f"No {action} today — the rolling weekly allowance is spent"
            )
            continue

        for at in _scatter(count, start_hour, end_hour, day, rng):
            plan.actions.append(
                PlannedAction(action=action, at=at, reason=_reason_for(action, stage))
            )

    plan.actions.sort(key=lambda item: item.at)

    if not plan.actions:
        plan.notes.append("A deliberately quiet day — real accounts have them")
    return plan


# Roughly one working day in nine is skipped entirely. Real people are ill,
# travelling, in back-to-back meetings, or simply don't open LinkedIn — and an
# account that engages every single day for a month is a machine signature
# independent of how modest its daily volume is.
#
# A thirty-day simulation caught exactly that: the observe stage had a quiet-day
# probability, but every stage after it fired every day, so a graduating account
# went from "sometimes quiet" to "never misses" the moment it started doing
# anything interesting.
DAY_OFF_PROBABILITY = 0.11


def _is_day_off(account_id: str, day: date) -> bool:
    """
    Is this account simply not around today?

    Seeded separately from the volume RNG so adding or removing an action band
    can't shift which days are off, and so two accounts' days off are
    uncorrelated — five colleagues taking the same Thursday off would be its
    own cluster signal.
    """
    digest = hashlib.sha256(f"{account_id}:dayoff:{day.isoformat()}".encode()).hexdigest()
    return (int(digest[:8], 16) % 1000) < DAY_OFF_PROBABILITY * 1000


def _daily_count(account, action: str, band, day: date, *, throttle: float) -> int:
    """
    How many of ``action`` this account does on ``day``, before weekly limits.

    Factored out of :func:`plan_day` so :func:`_weekly_headroom` can replay a
    day without recursing into the full planner — and, more importantly, so the
    replay cannot drift from the real thing.
    """
    if _is_day_off(str(getattr(account, "id", "unknown")), day):
        return 0

    rng = _seeded(str(getattr(account, "id", "unknown")), day)
    count = _sample(band, rng)
    if count == 0:
        return 0

    weekend_factor = caps_policy.WEEKEND_MULTIPLIER if day.weekday() >= 5 else 1.0
    caps = caps_policy.caps_for(account, action, throttle=throttle)
    ceiling = max(0, int(caps.per_day * weekend_factor * throttle))
    return min(count, ceiling) if ceiling else 0


def _weekly_headroom(account, action: str, stage, day: date, throttle: float) -> int:
    """
    What is left of the rolling weekly allowance, after the previous six days.

    The planner used to reason only in days. That is not how LinkedIn counts
    invitations — the limit is a rolling week — so a tier permitting 30 a day
    could plan five working days and land at 101, over the threshold where
    accounts get restricted. The limiter would have refused the overflow, but
    silently: the plan would look fine, the day's work would quietly evaporate,
    and nobody would learn why.

    Replays the previous six days' volumes rather than reading the ledger,
    because the planner is deterministic and must stay callable without a
    database (the console previews plans, and tests plan without persisting).
    """
    caps = caps_policy.caps_for(account, action, throttle=throttle)
    if not caps.per_week:
        return 10**6  # no weekly limit modelled for this action

    band = stage.volumes.get(action)
    if band is None:
        return 0

    spent = sum(
        _daily_count(account, action, band, day - timedelta(days=back), throttle=throttle)
        for back in range(1, 7)
    )
    return max(0, caps.per_week - spent)


def _working_window(account, account_id: str) -> tuple:
    """
    The hours this account is active.

    An explicit per-account ``active_hours`` setting wins — an operator who has
    told us when this person works knows better than a hash. Otherwise the
    window comes from ``safety.cluster``, which derives a distinct one per
    account so a team's activity smears across the day instead of pulsing
    together.

    Before this, every account fell back to the same 8-19 default: the cluster
    module computed per-account windows, the console displayed them, and the
    planner ignored them. A simulation of five colleagues found all five active
    together in 239 of 331 hours.
    """
    explicit = caps_policy._settings(account).get("active_hours")
    if isinstance(explicit, (list, tuple)) and len(explicit) == 2:
        return int(explicit[0]), int(explicit[1])

    from app.safety import cluster

    return cluster.schedule_window(account_id, base=caps_policy.DEFAULT_ACTIVE_HOURS)


def _reason_for(action: str, stage: program.Stage) -> str:
    return {
        program.LIKE: "Engaging with ICP content to build a real interest graph",
        program.FOLLOW: "Following people in your space so the feed stays relevant",
        program.COMMENT: "Adding a genuine comment — this is what earns profile views",
        program.POST: "Publishing so the account contributes, not just consumes",
        program.CONNECT: "Inviting someone whose content this account has engaged with",
        program.MESSAGE: "Following up with a new connection",
    }.get(action, stage.intent)


# ---------------------------------------------------------------------------
# Stage state (stored on the account's settings blob — no schema change needed)
# ---------------------------------------------------------------------------


def _warmup_state(account) -> dict:
    """
    The account's warm-up state.

    Lives in its own ``warmup_state`` column here. (In Social Bot it had to
    hide inside the caps blob because that codebase had no migrations; B2B
    Pulse does, so Phase 1 gave it a real column.)
    """
    state = getattr(account, "warmup_state", None)
    return state if isinstance(state, dict) else {}


def _write_state(account, state: dict) -> None:
    # Reassign rather than mutate: SQLAlchemy does not track in-place changes
    # to a JSONB dict, so a mutated dict would silently never persist.
    account.warmup_state = dict(state)


def current_stage(account) -> str:
    """Which warm-up stage this account is currently in."""
    return _warmup_state(account).get("stage") or program.FIRST_STAGE


def stage_since(account) -> Optional[datetime]:
    """When the account entered its current stage."""
    raw = _warmup_state(account).get("since")
    if not raw:
        return getattr(account, "created_at", None)
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return getattr(account, "created_at", None)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def days_in_stage(account, now: Optional[datetime] = None) -> int:
    since = stage_since(account)
    if since is None:
        return 0
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return max(0, ((now or datetime.now(timezone.utc)) - since).days)


def set_stage(account, stage_key: str, *, now: Optional[datetime] = None) -> None:
    """Move an account to a stage, recording when it happened."""
    state = dict(_warmup_state(account))
    moment = now or datetime.now(timezone.utc)

    history = list(state.get("history") or [])
    if state.get("stage") and state["stage"] != stage_key:
        history.append({"stage": state["stage"], "left": moment.isoformat()})

    state["stage"] = stage_key
    state["since"] = moment.isoformat()
    state["history"] = history[-20:]
    _write_state(account, state)


def paused(account) -> bool:
    """Is the warm-up programme paused (manually or by a safety trip)?"""
    return bool(_warmup_state(account).get("paused"))


def set_paused(account, value: bool, reason: str = "") -> None:
    """Pause or resume all warm-up activity for an account."""
    state = dict(_warmup_state(account))
    state["paused"] = bool(value)
    if reason:
        state["paused_reason"] = reason
    elif not value:
        state.pop("paused_reason", None)
    _write_state(account, state)
