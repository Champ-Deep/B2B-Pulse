"""
The account warm-up program.

A freshly-connected LinkedIn account is the most fragile thing in this system.
It has no history on this device, no recent activity, and no established
behaviour pattern — and the single strongest predictor of a restriction is a
quiet account that suddenly starts performing outreach. Volume limits don't
save you from that; the *shape* of the ramp does.

So every new account walks a fixed program before it is allowed to do outreach:

    observe → react → converse → publish → connect → full

Each stage declares **which actions are permitted at all** — not just how many.
That's the important part. During ``observe`` an invitation is not rate-limited,
it is impossible: the action isn't in the stage's allowlist, so no code path can
emit one. Capability grows with tenure rather than being available on day one
and merely discouraged.

Graduation is gated on **time AND health together**. An account that has been
connected for two weeks but is getting 8% acceptance does not advance — it goes
backwards. Time alone is the mistake that gets accounts banned in week three.

Design note on why the early stages look "unproductive": days 1-3 deliberately
generate almost nothing of commercial value. They exist so that by the time the
account sends its first invitation, LinkedIn has already seen it browsing,
reacting and commenting from this device and IP for a week. That history is
what makes the outreach look like a continuation rather than an eruption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

# Actions the warm-up program knows how to schedule.
LIKE = "like"
COMMENT = "comment"
FOLLOW = "follow"
POST = "post"
CONNECT = "connect"
MESSAGE = "message"


@dataclass(frozen=True)
class VolumeBand:
    """A daily target expressed as a range, not a number.

    Ranges matter: an account that performs exactly 12 likes every single day
    is as obviously automated as one that performs 500. The planner samples
    within the band, and sometimes lands on zero for the optional actions.
    """

    low: int
    high: int
    # Probability the action happens at all on a given day. Below 1.0 gives
    # natural "quiet days", which real people have and schedulers don't.
    probability: float = 1.0

    def __post_init__(self):
        if self.low > self.high:
            raise ValueError("VolumeBand low must be <= high")


@dataclass(frozen=True)
class Stage:
    """One stage of the warm-up program."""

    key: str
    name: str
    # What the account is doing, in plain language, for the UI.
    intent: str
    # Minimum days spent in this stage before graduation is even considered.
    min_days: int
    # Actions permitted in this stage. Anything absent is impossible, not
    # merely throttled.
    allowed: frozenset
    # Daily volume bands per action.
    volumes: dict = field(default_factory=dict)
    # Minimum cumulative actions required before graduating. Serving time is
    # not enough -- the account has to have actually done the work.
    requires: dict = field(default_factory=dict)
    # Minimum acceptance rate to graduate out of this stage. None = not
    # applicable yet (no invitations have been sent).
    min_acceptance_rate: Optional[float] = None


# ---------------------------------------------------------------------------
# The program
# ---------------------------------------------------------------------------

STAGES: Sequence[Stage] = (
    Stage(
        key="observe",
        name="Observing",
        intent=(
            "Signing in and reading the feed, nothing else. Establishes a "
            "consistent device and location before any activity happens."
        ),
        min_days=2,
        allowed=frozenset({LIKE}),
        volumes={
            # A handful of reactions, and genuinely nothing on some days.
            LIKE: VolumeBand(2, 6, probability=0.8),
        },
        requires={LIKE: 4},
    ),
    Stage(
        key="react",
        name="Reacting",
        intent=(
            "Liking and following people in your space. Builds a visible "
            "interest graph so the feed starts showing the right content."
        ),
        min_days=3,
        allowed=frozenset({LIKE, FOLLOW}),
        volumes={
            LIKE: VolumeBand(6, 14),
            FOLLOW: VolumeBand(1, 4, probability=0.7),
        },
        requires={LIKE: 30, FOLLOW: 6},
    ),
    Stage(
        key="converse",
        name="Commenting",
        intent=(
            "Adding real comments on your ICP's posts. This is what earns "
            "profile views — and it makes later outreach feel like a "
            "continuation rather than a cold approach."
        ),
        min_days=4,
        allowed=frozenset({LIKE, FOLLOW, COMMENT}),
        volumes={
            LIKE: VolumeBand(8, 16),
            FOLLOW: VolumeBand(1, 3, probability=0.6),
            COMMENT: VolumeBand(1, 3),
        },
        requires={COMMENT: 8, LIKE: 60},
    ),
    Stage(
        key="publish",
        name="Publishing",
        intent=(
            "Posting your own content and joining group conversations, while "
            "keeping daily engagement up. An account that only consumes looks "
            "different from one that contributes."
        ),
        min_days=5,
        allowed=frozenset({LIKE, FOLLOW, COMMENT, POST}),
        volumes={
            LIKE: VolumeBand(10, 18),
            FOLLOW: VolumeBand(1, 3, probability=0.5),
            COMMENT: VolumeBand(2, 4),
            # Two or three posts a week, never daily.
            POST: VolumeBand(1, 1, probability=0.35),
        },
        requires={POST: 2, COMMENT: 20},
    ),
    Stage(
        key="connect",
        name="Connecting",
        intent=(
            "First connection requests — small volume, and only to people "
            "whose posts this account has already engaged with. Warm "
            "invitations get accepted; cold ones get you reported."
        ),
        min_days=7,
        allowed=frozenset({LIKE, FOLLOW, COMMENT, POST, CONNECT}),
        volumes={
            LIKE: VolumeBand(10, 18),
            COMMENT: VolumeBand(2, 4),
            POST: VolumeBand(1, 1, probability=0.35),
            CONNECT: VolumeBand(5, 10),
        },
        requires={CONNECT: 25},
        # Before opening the taps, the audience has to actually be accepting.
        min_acceptance_rate=0.30,
    ),
    Stage(
        key="full",
        name="Full outreach",
        intent=(
            "Full programme: connection requests at tier volume, follow-up "
            "messages after acceptance, plus continued posting and engagement."
        ),
        min_days=0,  # terminal
        allowed=frozenset({LIKE, FOLLOW, COMMENT, POST, CONNECT, MESSAGE}),
        volumes={
            LIKE: VolumeBand(10, 20),
            COMMENT: VolumeBand(2, 5),
            POST: VolumeBand(1, 1, probability=0.35),
            CONNECT: VolumeBand(12, 20),
            MESSAGE: VolumeBand(5, 15),
        },
    ),
)

STAGES_BY_KEY = {stage.key: stage for stage in STAGES}
FIRST_STAGE = STAGES[0].key
FINAL_STAGE = STAGES[-1].key

# Acceptance rate below this is treated as a signal that the audience is wrong
# or the copy is bad. Continuing to send is how accounts get restricted.
ACCEPTANCE_DANGER = 0.15
ACCEPTANCE_CAUTION = 0.30


def stage_for(key: Optional[str]) -> Stage:
    """Resolve a stage key, defaulting to the first stage."""
    return STAGES_BY_KEY.get(key or "", STAGES_BY_KEY[FIRST_STAGE])


def next_stage(key: str) -> Optional[Stage]:
    """The stage after ``key``, or None if already at the end."""
    keys = [s.key for s in STAGES]
    try:
        index = keys.index(key)
    except ValueError:
        return STAGES[0]
    return STAGES[index + 1] if index + 1 < len(STAGES) else None


def previous_stage(key: str) -> Optional[Stage]:
    """The stage before ``key``, used when an account has to be walked back."""
    keys = [s.key for s in STAGES]
    try:
        index = keys.index(key)
    except ValueError:
        return None
    return STAGES[index - 1] if index > 0 else None


def is_allowed(stage_key: Optional[str], action: str) -> bool:
    """
    Is ``action`` permitted at this point in the programme?

    This is the hard gate the rest of the system asks before proposing or
    sending anything. A ``False`` here is not a rate limit — the account has
    not earned this capability yet.
    """
    return action in stage_for(stage_key).allowed


@dataclass
class Graduation:
    """Whether an account may move on, and why or why not."""

    ready: bool
    current: str
    target: Optional[str]
    reasons: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    # Set when the account should be moved *backwards* for its own safety.
    demote_to: Optional[str] = None


def evaluate_graduation(
    stage_key: str,
    *,
    days_in_stage: int,
    totals: dict,
    acceptance_rate: Optional[float] = None,
    invites_sent: int = 0,
    had_challenge: bool = False,
) -> Graduation:
    """
    Decide whether an account should advance, hold, or step back.

    Args:
        stage_key: the account's current stage.
        days_in_stage: days since it entered that stage.
        totals: cumulative action counts for this account, e.g. ``{"like": 42}``.
        acceptance_rate: invitations accepted / invitations sent, or None if
            too few invitations have been sent to mean anything.
        invites_sent: how many invitations the account has sent in total.
        had_challenge: whether LinkedIn has recently challenged this account.

    A challenge always wins: the account steps back a stage regardless of how
    well every other number looks, because a checkpoint means LinkedIn has
    already decided something is off.
    """
    stage = stage_for(stage_key)
    blockers: list = []
    reasons: list = []

    if had_challenge:
        back = previous_stage(stage.key)
        return Graduation(
            ready=False,
            current=stage.key,
            target=None,
            blockers=["LinkedIn challenged this account — backing off a stage"],
            demote_to=back.key if back else FIRST_STAGE,
        )

    # A collapsed acceptance rate is a demotion, not just a hold: continuing at
    # this volume is what turns a bad audience into a restricted account.
    if (
        acceptance_rate is not None
        and invites_sent >= 20
        and acceptance_rate < ACCEPTANCE_DANGER
    ):
        back = previous_stage(stage.key)
        return Graduation(
            ready=False,
            current=stage.key,
            target=None,
            blockers=[
                f"Acceptance rate {acceptance_rate:.0%} is below the {ACCEPTANCE_DANGER:.0%} "
                f"danger line — pausing invitations and stepping back"
            ],
            demote_to=back.key if back else stage.key,
        )

    upcoming = next_stage(stage.key)
    if upcoming is None:
        return Graduation(
            ready=False,
            current=stage.key,
            target=None,
            reasons=["Already at full outreach"],
        )

    if days_in_stage < stage.min_days:
        blockers.append(
            f"{days_in_stage} of {stage.min_days} days completed in {stage.name.lower()}"
        )
    else:
        reasons.append(f"{days_in_stage} days completed")

    for action, needed in (stage.requires or {}).items():
        done = int(totals.get(action, 0))
        if done < needed:
            blockers.append(f"{done} of {needed} {action}s completed")
        else:
            reasons.append(f"{done} {action}s completed")

    if stage.min_acceptance_rate is not None and invites_sent >= 15:
        if acceptance_rate is None or acceptance_rate < stage.min_acceptance_rate:
            shown = "unknown" if acceptance_rate is None else f"{acceptance_rate:.0%}"
            blockers.append(
                f"acceptance rate {shown} is below the "
                f"{stage.min_acceptance_rate:.0%} required to scale up"
            )
        else:
            reasons.append(f"acceptance rate {acceptance_rate:.0%}")

    return Graduation(
        ready=not blockers,
        current=stage.key,
        target=upcoming.key,
        reasons=reasons,
        blockers=blockers,
    )


def describe_program() -> list:
    """The whole programme, for the UI to render as a roadmap."""
    return [
        {
            "key": s.key,
            "name": s.name,
            "intent": s.intent,
            "min_days": s.min_days,
            "allowed": sorted(s.allowed),
            "daily": {
                action: {"low": band.low, "high": band.high, "probability": band.probability}
                for action, band in s.volumes.items()
            },
            "requires": dict(s.requires or {}),
            "min_acceptance_rate": s.min_acceptance_rate,
        }
        for s in STAGES
    ]


def estimated_days_to_outreach() -> int:
    """How long the programme takes before full outreach, at minimum."""
    return sum(s.min_days for s in STAGES)
