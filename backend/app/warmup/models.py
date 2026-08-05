"""
Account activity ledger.

Every action an account performs — a like during warm-up, an engagement from
the tracked-page pipeline, later an invitation — is written here. Three things
depend on it:

- **Warm-up graduation.** Stages require cumulative work ("30 likes, 8
  comments"), and Redis rate-limit counters expire after a week, so they cannot
  answer "has this account done enough yet".
- **The funnel.** Sent → accepted → replied is computed from this ledger.
- **Outcome attribution.** Each row can carry the ``variant`` used, so the
  system can eventually tell which angle actually works.

It is also the audit trail. ``EngagementAction`` records what the *pipeline*
did; this records what the *account* did, across every source. When an account
gets restricted, this is the record of exactly what it had been doing.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ActivityStatus:
    OK = "ok"
    FAILED = "failed"
    BLOCKED = "blocked"


class AccountActivity(Base):
    """One thing an account did, or tried to do."""

    __tablename__ = "account_activity"
    __table_args__ = (
        Index("ix_activity_account_action", "account_id", "action"),
        Index("ix_activity_account_time", "account_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )

    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ActivityStatus.OK)

    # Which warm-up stage the account was in. Makes the ramp auditable after
    # the fact, which is what you want when explaining a restriction.
    stage: Mapped[str | None] = mapped_column(String(32))

    subject_urn: Mapped[str | None] = mapped_column(String(512))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # The copy/angle variant used, for outcome attribution.
    variant: Mapped[str | None] = mapped_column(String(64))

    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
