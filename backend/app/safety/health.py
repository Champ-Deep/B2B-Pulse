"""
Account health: the acceptance-rate governor.

Most automation tooling governs on volume, because volume is easy to count.
That is the wrong variable. LinkedIn restricts accounts primarily on **negative
recipient feedback** — invitations ignored, or worse, marked "I don't know this
person". An account sending 15 invitations a day at 8% acceptance is in far
more danger than one sending 25 a day at 45%, and a substantial share of
restricted accounts never exceeded the published limits at all.

So this computes what the audience is actually doing and converts it into a
**throttle** that scales caps down, plus a **verdict** the UI can show:

    acceptance >= 30%   healthy    full tier volume
    15% - 30%           caution    volume reduced, worth reviewing targeting
    < 15%               danger     invitations stop entirely

The throttle only ever reduces. Nothing here can raise an account above the
ceiling its warm-up stage and tier already allow.

Status in B2B Pulse today
-------------------------
B2B Pulse does not send invitations yet — that arrives with outbound in Phase
5. Until then there is no acceptance rate to measure, and the governor
correctly reports ``unknown`` and applies no throttle. That is not a stub: the
*other* half of it is live and load-bearing right now, because a LinkedIn
challenge or a dead session is a danger signal regardless of whether any
invitation was ever sent.

The funnel is read through :class:`FunnelSource`, so Phase 5 supplies
invitation and reply counts by extending one function rather than by touching
the governor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.warmup.program import ACCEPTANCE_CAUTION, ACCEPTANCE_DANGER

# Below this many invitations, an acceptance rate is noise. Two rejections out
# of three is not a 33% acceptance rate, it is a small sample.
MIN_SAMPLE = 15

HEALTHY = "healthy"
CAUTION = "caution"
DANGER = "danger"
UNKNOWN = "unknown"


@dataclass
class Funnel:
    """The outreach funnel, measured rather than projected."""

    invites_sent: int = 0
    invites_accepted: int = 0
    messages_sent: int = 0
    replies: int = 0
    interested: int = 0
    booked: int = 0

    # Engagement volume, which exists in B2B Pulse today even though the
    # invitation funnel does not.
    likes_sent: int = 0
    comments_sent: int = 0
    failures: int = 0

    @property
    def acceptance_rate(self) -> float | None:
        if not self.invites_sent:
            return None
        return self.invites_accepted / self.invites_sent

    @property
    def reply_rate(self) -> float | None:
        if not self.messages_sent:
            return None
        return self.replies / self.messages_sent

    @property
    def booking_rate(self) -> float | None:
        if not self.invites_sent:
            return None
        return self.booked / self.invites_sent

    def as_dict(self) -> dict:
        return {
            "invites_sent": self.invites_sent,
            "invites_accepted": self.invites_accepted,
            "messages_sent": self.messages_sent,
            "replies": self.replies,
            "interested": self.interested,
            "booked": self.booked,
            "likes_sent": self.likes_sent,
            "comments_sent": self.comments_sent,
            "failures": self.failures,
            "acceptance_rate": _pct(self.acceptance_rate),
            "reply_rate": _pct(self.reply_rate),
            "booking_rate": _pct(self.booking_rate),
        }


@dataclass
class HealthReport:
    """What the numbers say, and what to do about it."""

    verdict: str = UNKNOWN
    throttle: float = 1.0
    # Actions the account may not perform right now, regardless of stage/caps.
    suspended_actions: frozenset = frozenset()
    funnel: Funnel = field(default_factory=Funnel)
    headline: str = ""
    advice: list = field(default_factory=list)
    had_challenge: bool = False

    def blocks(self, action: str) -> bool:
        return action in self.suspended_actions

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "throttle": round(self.throttle, 2),
            "suspended_actions": sorted(self.suspended_actions),
            "headline": self.headline,
            "advice": self.advice,
            "had_challenge": self.had_challenge,
            "funnel": self.funnel.as_dict(),
        }


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 1)


async def measure_funnel(db, account, *, since_days: int = 90) -> Funnel:
    """
    Compute an account's funnel from the activity ledger.

    Phase 5 extends this with invitation and reply counts from the outbound
    models; everything else about the governor stays as it is.
    """
    from app.warmup.models import AccountActivity, ActivityStatus

    since = datetime.now(UTC) - timedelta(days=since_days)

    rows = (
        await db.execute(
            select(AccountActivity.action, AccountActivity.status, func.count())
            .where(
                AccountActivity.account_id == account.id,
                AccountActivity.created_at >= since,
            )
            .group_by(AccountActivity.action, AccountActivity.status)
        )
    ).all()

    funnel = Funnel()
    for action, status, count in rows:
        count = int(count)
        if status != ActivityStatus.OK:
            funnel.failures += count
            continue
        if action == "like":
            funnel.likes_sent += count
        elif action == "comment":
            funnel.comments_sent += count
        elif action == "connect":
            funnel.invites_sent += count
        elif action == "message":
            funnel.messages_sent += count

    return funnel


def assess(funnel: Funnel, *, had_challenge: bool = False) -> HealthReport:
    """
    Turn measured outcomes into a throttle and a verdict.

    A pure function of the funnel, so it is trivially testable and the same
    numbers always produce the same governance decision.
    """
    report = HealthReport(funnel=funnel, had_challenge=had_challenge)

    if had_challenge:
        report.verdict = DANGER
        report.throttle = 0.25
        report.suspended_actions = frozenset({"connect"})
        report.headline = "LinkedIn challenged this account — outreach paused"
        report.advice = [
            "Sign in to LinkedIn in a browser and clear the checkpoint.",
            "Leave invitations off for at least 48 hours after clearing it.",
            "Re-verify the account here once the session works again.",
        ]
        return report

    rate = funnel.acceptance_rate

    if rate is None or funnel.invites_sent < MIN_SAMPLE:
        report.verdict = UNKNOWN
        report.throttle = 1.0
        if funnel.invites_sent == 0:
            report.headline = "No invitations sent yet — nothing to judge acceptance on"
        else:
            remaining = MIN_SAMPLE - funnel.invites_sent
            report.headline = (
                f"Not enough invitations yet to judge acceptance "
                f"({funnel.invites_sent} sent, {remaining} more for a reading)"
            )
        return report

    if rate < ACCEPTANCE_DANGER:
        report.verdict = DANGER
        report.throttle = 0.0
        report.suspended_actions = frozenset({"connect"})
        report.headline = (
            f"Acceptance rate {rate:.0%} — invitations stopped to protect the account"
        )
        report.advice = [
            "Below ~15% acceptance, LinkedIn starts treating the account as spam. "
            "This is a targeting problem, not a volume problem.",
            "Tighten the ICP: the people being invited don't recognise why you're "
            "reaching out.",
            "Engage with a prospect's posts before inviting them — warm invitations "
            "are accepted far more often than cold ones.",
            "Existing connections can still be messaged; only new invitations stop.",
        ]
        return report

    if rate < ACCEPTANCE_CAUTION:
        report.verdict = CAUTION
        # Scale smoothly between the danger and caution lines rather than
        # dropping off a cliff.
        span = ACCEPTANCE_CAUTION - ACCEPTANCE_DANGER
        report.throttle = round(0.4 + 0.5 * ((rate - ACCEPTANCE_DANGER) / span), 2)
        report.headline = f"Acceptance rate {rate:.0%} — volume reduced while this recovers"
        report.advice = [
            "Healthy accounts sit above 30%. Below that, invitation volume is "
            "reduced automatically until it recovers.",
            "Check whether the connection note gives a specific reason for "
            "reaching out to that person.",
        ]
        return report

    report.verdict = HEALTHY
    report.throttle = 1.0
    report.headline = f"Acceptance rate {rate:.0%} — healthy"
    if funnel.reply_rate is not None and funnel.reply_rate < 0.10 and funnel.messages_sent >= 10:
        report.advice.append(
            f"Invitations are landing, but only {funnel.reply_rate:.0%} of follow-up "
            f"messages get a reply. The opener is the thing to change."
        )
    return report


async def account_health(db, account, *, had_challenge: bool | None = None) -> HealthReport:
    """Measure and assess one account in a single call."""
    if had_challenge is None:
        # An account we deactivated, or one whose session has gone invalid, is
        # exactly the "LinkedIn pushed back" signal the governor cares about.
        had_challenge = not getattr(account, "is_active", True)

    funnel = await measure_funnel(db, account)
    return assess(funnel, had_challenge=had_challenge)


async def org_health(db, org_id: uuid.UUID) -> list[dict]:
    """
    Health for every connected account in an org.

    This is the query the admin console is built on: one row per real person's
    account, so a problem on any of them is visible without going looking.
    """
    from app.services.account_service import list_org_accounts

    out = []
    for account in await list_org_accounts(db, org_id):
        report = await account_health(db, account)
        out.append(
            {
                "account_id": str(account.id),
                "user_id": str(account.user_id),
                "name": account.linkedin_user_name,
                **report.as_dict(),
            }
        )
    return out
