"""
The approval queue.

Nothing outbound reaches LinkedIn without passing through here. That is the
product's central safety property, and it holds even against the reviewer: an
edit typed by a human is re-checked by the same quality gate the generated
draft went through, because a person pasting a booking link into a connection
note is exactly as much of a problem as a model doing it.

Two queues, split by action type
--------------------------------
Per the merge decision, approval is split by *what the action is* rather than
by whose account it belongs to:

**Engagement (likes, comments) — bulk approve, org-wide.** One admin sees one
queue across every account and can approve in a sweep. Five separate comment
queues would go unread by Friday, and an unread queue is the same as no review
at all.

The consequence, which is why this module cares: bulk approval removes the
per-item human pause that was implicitly slowing engagement down. A single
click can approve several accounts at once. That is only safe because
``safety/cluster.py`` has already sampled participation *before* the queue was
built — the admin is approving "these three accounts engage with this post",
never "all five do". If that sampling were ever moved after approval, this
would become the most dangerous button in the product.

**Messaging (invitations, DMs) — per account.** A direct message goes out under
one named person's identity, in their voice, to someone who will reply to
*them*. Nobody should be able to bulk-send messages as five colleagues.
:func:`approve_bulk` refuses these outright rather than quietly skipping them,
so a client cannot discover the rule by accident.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.outbound import pacing, sequences
from app.outbound.models import (
    BULK_APPROVABLE,
    HUMAN_OWNED,
    OutreachSuggestion,
    OutreachTarget,
    SuggestionAction,
    SuggestionStatus,
    TargetStatus,
)
from app.outbound.quality import check_copy

logger = logging.getLogger(__name__)


class ApprovalError(Exception):
    """An approval was refused, with a reason the caller should surface."""


@dataclass
class BulkResult:
    """Outcome of approving several suggestions at once."""

    approved: list = field(default_factory=list)
    refused: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "approved": self.approved,
            "refused": self.refused,
            "approved_count": len(self.approved),
            "refused_count": len(self.refused),
        }


def requires_individual_approval(action: str) -> bool:
    """
    Must this action be approved one account at a time?

    True for anything that speaks as a person to a person.
    """
    return action not in BULK_APPROVABLE


async def approve(
    db: AsyncSession,
    suggestion: OutreachSuggestion,
    *,
    reviewer_id: uuid.UUID | None = None,
    edited_text: str | None = None,
    account=None,
    send_at: datetime | None = None,
) -> OutreachSuggestion:
    """
    Approve one suggestion and give it a paced send time.

    An edit is re-checked by the quality gate exactly like generated copy —
    approving supplies human intent, not an exemption from the safety rules.
    """
    if suggestion.status not in (SuggestionStatus.PENDING, SuggestionStatus.BLOCKED):
        raise ApprovalError(f"cannot approve a suggestion that is {suggestion.status}")

    target = await _target(db, suggestion)
    if target is not None and target.status in HUMAN_OWNED:
        raise ApprovalError(
            "this person has replied — the conversation belongs to a human now"
        )

    text = (edited_text if edited_text is not None else suggestion.draft_text) or ""

    if suggestion.action in (
        SuggestionAction.CONNECT,
        SuggestionAction.MESSAGE,
        SuggestionAction.COMMENT,
    ):
        report = check_copy(
            text,
            suggestion.action,
            target,
            allow_scheduler_link=sequences.allows_scheduler_link(suggestion.step),
        )
        suggestion.quality_score = report.score
        suggestion.quality_warnings = report.all_issues
        if report.blockers:
            suggestion.status = SuggestionStatus.BLOCKED
            await db.commit()
            raise ApprovalError("; ".join(report.blockers))

    suggestion.final_text = text
    suggestion.status = SuggestionStatus.SCHEDULED
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.reviewed_by = reviewer_id

    account = account or await _account(db, suggestion)
    last_sent = await _last_sent_at(db, suggestion.account_id, suggestion.action)
    suggestion.scheduled_for = send_at or pacing.schedule_next(
        account, suggestion.action, last_sent_at=last_sent
    )

    if target is not None:
        target.status = TargetStatus.APPROVED

    await db.commit()
    await db.refresh(suggestion)
    return suggestion


async def approve_bulk(
    db: AsyncSession,
    suggestion_ids: list,
    *,
    org_id: uuid.UUID,
    reviewer_id: uuid.UUID | None = None,
) -> BulkResult:
    """
    Approve several engagement suggestions in one sweep.

    Refuses anything that speaks as a person — see the module docstring. The
    refusal is explicit and per-item rather than a silent skip, so a client
    cannot discover the rule by noticing some approvals didn't take.
    """
    result = BulkResult()

    rows = list(
        (
            await db.execute(
                select(OutreachSuggestion).where(
                    OutreachSuggestion.id.in_([uuid.UUID(str(i)) for i in suggestion_ids]),
                    OutreachSuggestion.org_id == org_id,
                )
            )
        )
        .scalars()
        .all()
    )
    found = {str(r.id) for r in rows}
    for missing in {str(i) for i in suggestion_ids} - found:
        result.refused[missing] = "not found in this organisation"

    for row in rows:
        if requires_individual_approval(row.action):
            result.refused[str(row.id)] = (
                f"a {row.action} goes out under one person's name and must be "
                f"approved on their account, not in bulk"
            )
            continue
        try:
            await approve(db, row, reviewer_id=reviewer_id)
            result.approved.append(str(row.id))
        except ApprovalError as exc:
            result.refused[str(row.id)] = str(exc)

    logger.info(
        "Bulk approval by %s: %d approved, %d refused",
        reviewer_id, len(result.approved), len(result.refused),
    )
    return result


async def reject(
    db: AsyncSession,
    suggestion: OutreachSuggestion,
    *,
    reviewer_id: uuid.UUID | None = None,
    suppress_target: bool = False,
) -> OutreachSuggestion:
    """
    Reject a suggestion.

    ``suppress_target`` is the "never contact this person" switch: it puts them
    permanently out of reach of every future suggestion, for every action and
    every account. That is what makes "no" mean no.
    """
    suggestion.status = SuggestionStatus.REJECTED
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.reviewed_by = reviewer_id

    target = await _target(db, suggestion)
    if target is not None:
        target.status = (
            TargetStatus.SUPPRESSED if suppress_target else TargetStatus.SCORED
        )

    await db.commit()
    await db.refresh(suggestion)
    return suggestion


async def reject_bulk(
    db: AsyncSession, suggestion_ids: list, *, org_id: uuid.UUID,
    reviewer_id: uuid.UUID | None = None,
) -> BulkResult:
    """
    Reject several suggestions at once.

    Unlike approval, bulk *rejection* is safe for every action type — saying no
    to a hundred things at once can't hurt anybody.
    """
    result = BulkResult()
    rows = list(
        (
            await db.execute(
                select(OutreachSuggestion).where(
                    OutreachSuggestion.id.in_([uuid.UUID(str(i)) for i in suggestion_ids]),
                    OutreachSuggestion.org_id == org_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await reject(db, row, reviewer_id=reviewer_id)
        result.approved.append(str(row.id))  # "processed", in the bulk sense
    return result


async def queue(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    kind: str = "engagement",
    limit: int = 100,
) -> list:
    """
    The review queue.

    ``kind`` selects which of the two queues: ``engagement`` is org-wide and
    bulk-approvable, ``messaging`` is the per-account one. They are separate
    endpoints in the UI because they carry different risk and are reviewed by
    different people.
    """
    stmt = select(OutreachSuggestion).where(
        OutreachSuggestion.org_id == org_id,
        OutreachSuggestion.status == SuggestionStatus.PENDING,
    )

    if kind == "engagement":
        stmt = stmt.where(OutreachSuggestion.action.in_(list(BULK_APPROVABLE)))
    elif kind == "messaging":
        stmt = stmt.where(OutreachSuggestion.action.not_in(list(BULK_APPROVABLE)))

    if account_id:
        stmt = stmt.where(OutreachSuggestion.account_id == account_id)

    # Best matches first: the reviewer's attention is the scarce resource.
    stmt = stmt.order_by(
        OutreachSuggestion.relevance_score.desc(),
        OutreachSuggestion.created_at.desc(),
    ).limit(limit)

    return list((await db.execute(stmt)).scalars().all())


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


async def _target(db: AsyncSession, suggestion) -> OutreachTarget | None:
    return (
        await db.execute(
            select(OutreachTarget).where(OutreachTarget.id == suggestion.target_id)
        )
    ).scalar_one_or_none()


async def _account(db: AsyncSession, suggestion):
    from app.models.integration import IntegrationAccount

    return (
        await db.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.id == suggestion.account_id
            )
        )
    ).scalar_one_or_none()


async def _last_sent_at(db: AsyncSession, account_id, action: str) -> datetime | None:
    return (
        await db.execute(
            select(OutreachSuggestion.sent_at)
            .where(
                OutreachSuggestion.account_id == account_id,
                OutreachSuggestion.action == action,
                OutreachSuggestion.sent_at.is_not(None),
            )
            .order_by(OutreachSuggestion.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
