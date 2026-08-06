"""
Outreach sequences: connect → accepted → follow up → qualify → book.

The rule that matters most in this file is stated once and enforced everywhere:

    **A reply stops the sequence. Immediately, permanently, no exceptions.**

Nothing does more damage to a real conversation than an automated follow-up
landing after someone has already answered. It is the single clearest tell that
a person was talking to software, and it costs the meeting that the whole
sequence existed to book. So ``advance`` refuses to produce a next step for any
target a human now owns, and the sync job that detects replies flips that state
before the scheduler ever runs.

The second rule concerns scheduler links. A booking link in a first touch is
spam — the quality gate blocks it outright. But once someone has replied and
shown interest, sending a link is exactly what they want. The step definitions
below carry ``allow_scheduler_link``, which is only true after a reply, and the
quality gate honours that flag rather than applying one blanket rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.outbound.models import SuggestionAction
from app.outbound.models import HUMAN_OWNED, TargetStatus


@dataclass(frozen=True)
class SequenceStep:
    """One step in a cadence."""

    key: str
    action: str
    # What has to be true of the target before this step may run.
    requires_status: tuple
    # Days to wait after the previous step landed.
    wait_days: int
    # Brief for the copywriter — what this message is for.
    objective: str
    allow_scheduler_link: bool = False
    # Steps that are only offered, never auto-scheduled.
    manual_only: bool = False


# The default cadence. Deliberately short: three touches after acceptance, then
# stop. Sequences that run six or seven messages into silence are how a brand
# gets a reputation, and the marginal reply rate past the third message is
# close to zero.
DEFAULT_SEQUENCE: List[SequenceStep] = [
    SequenceStep(
        key="invite",
        action=SuggestionAction.CONNECT,
        requires_status=(TargetStatus.NEW, TargetStatus.SCORED, TargetStatus.SUGGESTED),
        wait_days=0,
        objective=(
            "A connection note that gives one specific, true reason for "
            "connecting with this person. No pitch, no ask."
        ),
    ),
    SequenceStep(
        key="welcome",
        action=SuggestionAction.MESSAGE,
        requires_status=(TargetStatus.CONNECTED,),
        # Messaging the instant an invitation is accepted is the most obvious
        # automation tell there is. Wait a couple of days.
        wait_days=2,
        objective=(
            "A short thank-you-for-connecting that opens a conversation about "
            "their work. One genuine question, no pitch, no link."
        ),
    ),
    SequenceStep(
        key="value",
        action=SuggestionAction.MESSAGE,
        requires_status=(TargetStatus.CONNECTED,),
        wait_days=5,
        objective=(
            "Share one concrete, useful observation relevant to their role — "
            "something they could act on even if they never reply. Still no ask."
        ),
    ),
    SequenceStep(
        key="ask",
        action=SuggestionAction.MESSAGE,
        requires_status=(TargetStatus.CONNECTED,),
        wait_days=6,
        objective=(
            "A direct but low-pressure ask: is this relevant to them right now? "
            "Make it genuinely easy to say no. This is the last automated touch."
        ),
    ),
    # Everything past here happens only after a human reply, and is offered for
    # approval rather than scheduled.
    SequenceStep(
        key="qualify",
        action=SuggestionAction.MESSAGE,
        requires_status=(TargetStatus.REPLIED,),
        wait_days=0,
        objective=(
            "They replied. Ask the one or two questions that establish whether "
            "this is a real fit before proposing any meeting."
        ),
        manual_only=True,
    ),
    SequenceStep(
        key="book",
        action=SuggestionAction.MESSAGE,
        requires_status=(TargetStatus.INTERESTED,),
        wait_days=0,
        objective=(
            "They're a fit and they're interested. Propose a specific time and "
            "include the scheduler link."
        ),
        allow_scheduler_link=True,
        manual_only=True,
    ),
]

STEPS_BY_KEY = {step.key: step for step in DEFAULT_SEQUENCE}

# Steps that may be scheduled without a human asking for them each time.
AUTOMATED_STEPS = [s for s in DEFAULT_SEQUENCE if not s.manual_only]


@dataclass
class NextStep:
    """The step a target is due for, if any."""

    step: Optional[SequenceStep] = None
    due_at: Optional[datetime] = None
    blocked_reason: str = ""

    def __bool__(self) -> bool:
        return self.step is not None


def steps_completed(target, suggestions) -> set:
    """Which sequence steps have already been sent to this target."""
    from app.outbound.models import SuggestionStatus

    done = set()
    for suggestion in suggestions:
        if suggestion.target_id != target.id:
            continue
        if suggestion.status != SuggestionStatus.SENT:
            continue
        key = (suggestion.result or {}).get("step") if suggestion.result else None
        done.add(key or suggestion.action)
    return done


def advance(
    target,
    *,
    completed: set,
    last_touch_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
    include_manual: bool = False,
) -> NextStep:
    """
    Decide the next sequence step for a target.

    Returns an empty :class:`NextStep` — with a reason — when the target should
    be left alone. The most important of those reasons is that they replied.
    """
    now = now or datetime.now(timezone.utc)

    if target.status in HUMAN_OWNED and not include_manual:
        return NextStep(
            blocked_reason=(
                "This person replied — the sequence is stopped so a human can "
                "take the conversation"
                if target.status == TargetStatus.REPLIED
                else f"Sequence stopped: target is {target.status}"
            )
        )

    if target.status == TargetStatus.SUPPRESSED:
        return NextStep(blocked_reason="Target is suppressed")

    candidates = DEFAULT_SEQUENCE if include_manual else AUTOMATED_STEPS

    for step in candidates:
        if step.key in completed:
            continue
        if target.status not in step.requires_status:
            continue

        anchor = last_touch_at or getattr(target, "last_touched_at", None)
        if step.wait_days and anchor is not None:
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            due = anchor + timedelta(days=step.wait_days)
            if due > now:
                return NextStep(step=step, due_at=due, blocked_reason="waiting")
            return NextStep(step=step, due_at=due)
        return NextStep(step=step, due_at=now)

    # Nothing matched. Distinguish "still waiting on them" from "we're done",
    # because those look identical in the data and completely different to a
    # person reading the UI.
    if target.status == TargetStatus.CONTACTED:
        return NextStep(
            blocked_reason=(
                "Invitation sent — waiting for them to accept before anything else"
            )
        )
    return NextStep(blocked_reason="Sequence complete — no further automated touches")


def objective_for(step_key: str) -> str:
    """The copywriter brief for a step."""
    step = STEPS_BY_KEY.get(step_key)
    return step.objective if step else ""


def allows_scheduler_link(step_key: Optional[str]) -> bool:
    """
    May this step include a booking link?

    False for every automated touch. A scheduler link is only appropriate once
    the other person has replied and shown interest — before that it is the
    fastest way to get an invitation reported.
    """
    step = STEPS_BY_KEY.get(step_key or "")
    return bool(step and step.allow_scheduler_link)


def describe_sequence() -> list:
    """The cadence, for the UI to render."""
    return [
        {
            "key": s.key,
            "action": s.action,
            "wait_days": s.wait_days,
            "objective": s.objective,
            "requires_status": list(s.requires_status),
            "manual_only": s.manual_only,
            "allow_scheduler_link": s.allow_scheduler_link,
        }
        for s in DEFAULT_SEQUENCE
    ]
