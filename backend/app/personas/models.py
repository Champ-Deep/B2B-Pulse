"""
Personas: who each account is online.

The requirement this exists for: five real LakeB2B people, each represented
online, each with their own direction, steerable centrally by one admin without
flattening them into the same voice.

Neither half of the merge had this. B2B Pulse had ``UserProfile.tone_settings``
(voice, per user) and Social Bot had ``ICPProfile`` (who to target, per
account). A persona owns both, plus the parts neither had: what this person is
credible talking about, what they must never say, and how much they run
unattended.

Inheritance is the load-bearing idea
------------------------------------
An org-level persona holds the shared direction — the campaign, the company
line, the guardrails everyone is bound by. Each account's persona inherits from
it and overrides only what makes that person distinct.

That is what makes "one admin steering five accounts" work without making all
five identical. The admin edits the org persona and everyone shifts; a person's
own ``content_pillars`` override survives that change, because an override is
recorded as an override rather than as a copy.

It is also a cluster-safety control. Five accounts commenting from one prompt
produce five near-identical comments, which is exactly the correlation signal
``safety/cluster.py`` measures. Distinct personas are how you get five genuinely
different takes on the same post.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Persona(Base):
    """A voice and a direction, either org-wide or for one account."""

    __tablename__ = "personas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Null account_id = the org-level persona that others inherit from.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # --- Voice ---
    # How this person writes: tone, sentence length, vocabulary, and the
    # phrases they would never use.
    voice: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Free-text description in their own words. The single most useful input to
    # copy that sounds like a person rather than a template.
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Substance ---
    # What they are credible talking about. Commenting outside this is what
    # makes an account read as a bot with opinions on everything.
    expertise: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Three to five themes their posts and comments orbit.
    content_pillars: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Direction ---
    # Who they should be connecting with. Populated from the ICP in Phase 5.
    icp: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Topics to avoid, competitors not to engage, tone limits. Guardrails
    # inherit *cumulatively* — a person cannot override away an org-level
    # prohibition, only add their own.
    guardrails: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # What runs unattended versus what needs this person's approval.
    autonomy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
