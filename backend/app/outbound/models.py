"""
Outbound models: who we're trying to reach, and what we propose to send them.

Three entities, and the relationship between them is the product's central
safety property:

``ICPProfile`` defines who is worth talking to. ``OutreachTarget`` is one
concrete person. ``OutreachSuggestion`` is a *proposal* — an action toward that
person with drafted copy, waiting for a human. Nothing in the outbound path
reaches LinkedIn without passing through a suggestion someone approved.

That indirection means the worst possible failure of the targeting or
copywriting layer is a bad suggestion sitting in a queue, rather than a bad
message in a stranger's inbox.

Per the merge decisions, approval is split by action type rather than by
account: engagement is bulk-approved across accounts, messaging is approved
per account. A direct message goes out under one named person's identity to
someone who will reply to *them*, so nobody should be able to bulk-send those
as five colleagues at once.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TargetStatus:
    """
    Lifecycle of a prospect for one account.

    The order matters: everything from CONNECTED onward implies the invitation
    was accepted, and everything from REPLIED onward implies they wrote back.
    The funnel and the acceptance governor both rely on that being true.
    """

    NEW = "new"
    SCORED = "scored"
    SUGGESTED = "suggested"
    APPROVED = "approved"
    CONTACTED = "contacted"
    CONNECTED = "connected"
    REPLIED = "replied"
    INTERESTED = "interested"
    BOOKED = "booked"
    NOT_INTERESTED = "not_interested"
    SKIPPED = "skipped"
    SUPPRESSED = "suppressed"


# Once a target reaches one of these, automation stops touching them: a human
# is in the conversation, and the worst thing the system can do is talk over it.
HUMAN_OWNED = (
    TargetStatus.REPLIED,
    TargetStatus.INTERESTED,
    TargetStatus.BOOKED,
    TargetStatus.NOT_INTERESTED,
    TargetStatus.SUPPRESSED,
)


class SuggestionAction:
    CONNECT = "connect"
    MESSAGE = "message"
    COMMENT = "comment"
    LIKE = "like"
    FOLLOW = "follow"


# Which actions are bulk-approvable, per the merge decision. Messaging is
# deliberately absent: it goes out under one person's name, to someone who will
# reply to them.
BULK_APPROVABLE = (SuggestionAction.LIKE, SuggestionAction.COMMENT, SuggestionAction.FOLLOW)


class SuggestionStatus:
    PENDING = "pending"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ICPProfile(Base):
    """The definition of a good-fit person."""

    __tablename__ = "icp_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    titles: Mapped[list | None] = mapped_column(JSONB)
    seniorities: Mapped[list | None] = mapped_column(JSONB)
    industries: Mapped[list | None] = mapped_column(JSONB)
    keywords: Mapped[list | None] = mapped_column(JSONB)
    excluded_keywords: Mapped[list | None] = mapped_column(JSONB)
    excluded_titles: Mapped[list | None] = mapped_column(JSONB)
    locations: Mapped[list | None] = mapped_column(JSONB)
    company_sizes: Mapped[list | None] = mapped_column(JSONB)

    # What the user is offering, and standing direction for the copywriter.
    value_proposition: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)

    # Suggestions below this score are never shown.
    relevance_floor: Mapped[int] = mapped_column(Integer, default=60)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutreachTarget(Base):
    """One prospect, scoped to the account that will reach out to them."""

    __tablename__ = "outreach_targets"
    __table_args__ = (
        # One row per person per account: the foundation of "never touch the
        # same person twice".
        Index("ix_target_account_member", "account_id", "member_urn", unique=True),
        Index("ix_target_account_status", "account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    icp_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    member_urn: Mapped[str] = mapped_column(String(255), nullable=False)
    public_id: Mapped[str | None] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(String(512))

    full_name: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(128))
    headline: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))

    source: Mapped[str | None] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(String(512))
    # Something specific and true about them, used to personalize copy.
    context: Mapped[dict | None] = mapped_column(JSONB)

    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    relevance_reasons: Mapped[list | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(32), default=TargetStatus.NEW, index=True)
    last_touched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Funnel timestamps, kept explicitly rather than inferred from status so
    # the rates survive later status changes.
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variant: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutreachSuggestion(Base):
    """One proposed action toward one person, pending human judgement."""

    __tablename__ = "outreach_suggestions"
    __table_args__ = (
        Index("ix_suggestion_target_action", "target_id", "action"),
        Index("ix_suggestion_account_status", "account_id", "status"),
        Index("ix_suggestion_due", "status", "scheduled_for"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outreach_targets.id", ondelete="CASCADE"),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=SuggestionStatus.PENDING, index=True)

    # ``final_text`` is what actually goes out: the reviewer's edit if they
    # made one, otherwise the draft.
    draft_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)

    rationale: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    relevance_reasons: Mapped[list | None] = mapped_column(JSONB)

    quality_score: Mapped[int | None] = mapped_column(Integer)
    quality_warnings: Mapped[list | None] = mapped_column(JSONB)

    subject_urn: Mapped[str | None] = mapped_column(String(512))
    generated_by: Mapped[str | None] = mapped_column(String(128))
    step: Mapped[str | None] = mapped_column(String(32))
    variant: Mapped[str | None] = mapped_column(String(64))

    depends_on_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
