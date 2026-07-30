import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Platform(str, enum.Enum):
    LINKEDIN = "linkedin"
    META = "meta"
    WHATSAPP = "whatsapp"


class IntegrationAccount(Base):
    __tablename__ = "integration_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    session_cookies: Mapped[dict | list | str | None] = mapped_column(JSONB, nullable=True)

    # LinkedIn-specific fields (dedicated columns for better queryability)
    linkedin_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_user_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_session_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # --- Ported from Social Bot: per-account safety and behaviour state ---
    #
    # A stable device identity for the mobile transport. LinkedIn ties trust to
    # device consistency, so this is generated once from the account id and
    # never regenerated.
    device_fingerprint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Effective caps and pacing: tier, per-action overrides, active hours,
    # timezone, suggestion budget. See app/safety/caps.py.
    daily_caps: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Warm-up programme state: current stage, when it was entered, history,
    # and whether the account is paused. See app/warmup/.
    warmup_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # The account's engagement direction: outreach | account_based_engagement.
    mode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Per-account egress proxy. Strongly recommended once several accounts run
    # from one deployment -- shared egress is its own correlation signal.
    proxy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_post_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user: Mapped["User"] = relationship(back_populates="integration_accounts")  # noqa: F821
