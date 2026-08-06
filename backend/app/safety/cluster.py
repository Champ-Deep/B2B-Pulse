"""
Cluster safety: stopping five accounts from looking like one operation.

Every other safety control in this system protects an account from itself.
This one protects the accounts from *each other*, and it is the risk that
actually matters when one org runs several real people's logins.

The problem
-----------
When a tracked page posts, the pipeline fans it out to every subscribed member.
Five colleagues then like and comment on the same post, from comments generated
by the same model on the same prompt, often within the same working hours.
Stagger delays spread the *timing*, but they do not change the underlying
signal — and detection does not need to catch one account. It catches the
cluster, and then all five go at once. That is a far worse outcome than any
single restriction, and no per-account cap prevents it.

Bulk approval makes this load-bearing rather than theoretical: one click can
now approve five accounts engaging with the same post, so the sampling has to
happen *before* the queue is built. An admin should be approving "these three
accounts engage with this post", never "all five do".

Four controls
-------------
1. **Participation sampling** — only a subset of eligible accounts engages with
   any given post, chosen deterministically so the decision is stable and
   auditable rather than a fresh coin flip on every retry.
2. **Cluster caps** — a hard ceiling on how many of our accounts may touch one
   post, and one company per day, on top of each account's own caps.
3. **Independent schedules** — each account gets its own activity window,
   derived from its id, so they are not all working 9-to-5 together.
4. **Correlation monitoring** — a measured overlap score, so drift toward a
   detectable pattern is visible before the platform acts on it.

Why sampling is deterministic
-----------------------------
``sample_participants`` seeds from ``(post, account)``, so asking twice gives
the same answer. That matters more than it sounds: a Celery task that retries
must not re-roll and pick a different set, an admin reviewing a queue must see
what will actually happen, and an investigator after the fact must be able to
recompute why a given account did or didn't engage. No storage required, and
no drift between what was shown and what ran.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

# What fraction of eligible accounts engage with any one post. 0.6 means a
# typical post gets three of five accounts — enough to amplify, not so many
# that the group moves as one.
DEFAULT_PARTICIPATION_RATE = 0.6

# Hard ceilings, applied on top of the participation rate.
MAX_ACCOUNTS_PER_POST = 3
MAX_ACCOUNTS_PER_COMPANY_PER_DAY = 4

# Above this overlap between two accounts' engagement history, they are
# behaving as one actor and the org should be told.
CORRELATION_WARN = 0.6
CORRELATION_DANGER = 0.8


@dataclass
class ParticipationDecision:
    """Which accounts engage with a post, and why the others don't."""

    selected: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    rate: float = DEFAULT_PARTICIPATION_RATE
    reason: str = ""

    def includes(self, account_id) -> bool:
        return str(account_id) in {str(a) for a in self.selected}


def _score(post_key: str, account_id: str) -> float:
    """
    A stable pseudo-random score in [0, 1) for a (post, account) pair.

    Deterministic so the same pair always scores the same — see the module
    docstring on why that matters.
    """
    digest = hashlib.sha256(f"{post_key}:{account_id}".encode()).hexdigest()
    return int(digest[:12], 16) / float(1 << 48)


def sample_participants(
    post_key: str,
    account_ids: list,
    *,
    rate: float = DEFAULT_PARTICIPATION_RATE,
    max_accounts: int = MAX_ACCOUNTS_PER_POST,
) -> ParticipationDecision:
    """
    Choose which accounts engage with one post.

    Sorting by the stable score and taking the top N means the *same* accounts
    aren't always chosen — a different post produces a different ordering — so
    participation rotates naturally without anyone tracking whose turn it is.
    """
    ids = [str(a) for a in account_ids]
    if not ids:
        return ParticipationDecision(rate=rate, reason="no eligible accounts")

    target = min(max_accounts, max(1, round(len(ids) * rate)))

    ranked = sorted(ids, key=lambda a: _score(post_key, a))
    selected = ranked[:target]
    skipped = {a: "not sampled for this post" for a in ranked[target:]}

    return ParticipationDecision(
        selected=selected,
        skipped=skipped,
        rate=rate,
        reason=(
            f"{len(selected)} of {len(ids)} accounts sampled for this post "
            f"— spreading engagement so the group doesn't move as one"
        ),
    )


async def post_headroom(db, post_id, *, limit: int = MAX_ACCOUNTS_PER_POST) -> int:
    """How many more of our accounts may touch this post."""
    from app.models.engagement import EngagementAction

    used = (
        await db.execute(
            select(func.count(func.distinct(EngagementAction.user_id))).where(
                EngagementAction.post_id == post_id
            )
        )
    ).scalar() or 0
    return max(0, limit - int(used))


async def company_headroom(
    db, tracked_page_id, *, limit: int = MAX_ACCOUNTS_PER_COMPANY_PER_DAY
) -> int:
    """
    How many more of our accounts may engage with this company today.

    Per-post limits alone don't stop five accounts working through six posts
    from the same company in an afternoon, which reads exactly as coordinated.
    """
    from app.models.engagement import EngagementAction
    from app.models.post import Post

    since = datetime.now(UTC) - timedelta(hours=24)
    used = (
        await db.execute(
            select(func.count(func.distinct(EngagementAction.user_id)))
            .join(Post, Post.id == EngagementAction.post_id)
            .where(
                Post.tracked_page_id == tracked_page_id,
                EngagementAction.created_at >= since,
            )
        )
    ).scalar() or 0
    return max(0, limit - int(used))


async def eligible_participants(
    db, post_id, tracked_page_id, candidate_user_ids: list
) -> ParticipationDecision:
    """
    The full participation decision for one post.

    Applies sampling, then the post and company ceilings, and reports what each
    control removed so the reason is never mysterious.
    """
    decision = sample_participants(str(post_id), candidate_user_ids)

    post_left = await post_headroom(db, post_id)
    company_left = await company_headroom(db, tracked_page_id)
    ceiling = min(post_left, company_left)

    if ceiling <= 0:
        for account in decision.selected:
            decision.skipped[account] = (
                "post already at the cluster limit"
                if post_left <= 0
                else "company already at today's cluster limit"
            )
        decision.selected = []
        decision.reason = (
            "cluster limit reached — enough of our accounts have already "
            "engaged here"
        )
        return decision

    if len(decision.selected) > ceiling:
        for account in decision.selected[ceiling:]:
            decision.skipped[account] = "cluster limit reached for this post/company"
        decision.selected = decision.selected[:ceiling]

    return decision


# ----------------------------------------------------------------------
# Independent schedules
# ----------------------------------------------------------------------


def schedule_window(account_id: str, base: tuple = (8, 19)) -> tuple:
    """
    A per-account activity window, derived from its id.

    Accounts that all work exactly 09:00-17:00 are their own signature. This
    shifts each one by a few hours and varies its length, deterministically, so
    the group's activity smears across the day instead of pulsing together.
    """
    digest = hashlib.sha256(f"{account_id}:window".encode()).hexdigest()
    shift = int(digest[:2], 16) % 4 - 1        # -1..+2 hours
    length = 9 + (int(digest[2:4], 16) % 3)    # 9..11 hours

    start = max(6, min(11, base[0] + shift))
    return start, min(22, start + length)


# ----------------------------------------------------------------------
# Correlation monitoring
# ----------------------------------------------------------------------


@dataclass
class CorrelationReport:
    """How similarly the org's accounts are behaving."""

    verdict: str = "healthy"
    max_overlap: float = 0.0
    average_overlap: float = 0.0
    pairs: list = field(default_factory=list)
    headline: str = ""
    advice: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "max_overlap": round(self.max_overlap * 100, 1),
            "average_overlap": round(self.average_overlap * 100, 1),
            "pairs": self.pairs,
            "headline": self.headline,
            "advice": self.advice,
        }


async def measure_correlation(db, org_id, *, since_days: int = 30) -> CorrelationReport:
    """
    Measure how much the org's accounts overlap in what they engage with.

    Jaccard similarity per pair: shared posts over total distinct posts. Two
    accounts at 90% are effectively one actor as far as detection is concerned,
    however careful each of them is individually.
    """
    from app.models.engagement import EngagementAction
    from app.models.user import User

    since = datetime.now(UTC) - timedelta(days=since_days)

    rows = (
        await db.execute(
            select(EngagementAction.user_id, EngagementAction.post_id)
            .join(User, User.id == EngagementAction.user_id)
            .where(User.org_id == org_id, EngagementAction.created_at >= since)
        )
    ).all()

    by_user: dict = {}
    for user_id, post_id in rows:
        by_user.setdefault(str(user_id), set()).add(str(post_id))

    users = [u for u, posts in by_user.items() if posts]
    if len(users) < 2:
        return CorrelationReport(
            verdict="unknown",
            headline="Not enough accounts with activity to compare behaviour yet",
        )

    pairs = []
    for i, a in enumerate(users):
        for b in users[i + 1:]:
            union = by_user[a] | by_user[b]
            if not union:
                continue
            overlap = len(by_user[a] & by_user[b]) / len(union)
            pairs.append({"a": a, "b": b, "overlap": round(overlap * 100, 1)})

    if not pairs:
        return CorrelationReport(
            verdict="unknown", headline="No shared activity to compare yet"
        )

    values = [p["overlap"] / 100 for p in pairs]
    report = CorrelationReport(
        max_overlap=max(values),
        average_overlap=sum(values) / len(values),
        pairs=sorted(pairs, key=lambda p: -p["overlap"])[:10],
    )

    if report.max_overlap >= CORRELATION_DANGER:
        report.verdict = "danger"
        report.headline = (
            f"Two accounts overlap {report.max_overlap:.0%} of the time — "
            f"they are behaving as one actor"
        )
        report.advice = [
            "Lower the participation rate so fewer accounts engage with each post.",
            "Give the accounts different tracked-page subscriptions — total overlap "
            "in what they follow guarantees overlap in what they do.",
            "Check that personas are producing genuinely different comments rather "
            "than the same angle with a different name on it.",
        ]
    elif report.max_overlap >= CORRELATION_WARN:
        report.verdict = "caution"
        report.headline = (
            f"Accounts overlap up to {report.max_overlap:.0%} — worth spreading out"
        )
        report.advice = [
            "Consider a lower participation rate, or splitting tracked pages "
            "across accounts rather than subscribing everyone to everything.",
        ]
    else:
        report.headline = (
            f"Accounts overlap {report.max_overlap:.0%} at most — behaving independently"
        )

    return report
