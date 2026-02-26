import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.integration import Platform


class PageType(str, enum.Enum):
    PERSONAL = "personal"
    COMPANY = "company"
    IG_BUSINESS = "ig_business"
    FB_PAGE = "fb_page"


class PollingMode(str, enum.Enum):
    NORMAL = "normal"
    HUNT = "hunt"


class TrackedPage(Base):
    __tablename__ = "tracked_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    page_type: Mapped[PageType] = mapped_column(
        Enum(PageType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=PageType.PERSONAL,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    org: Mapped["Org"] = relationship(back_populates="tracked_pages")  # noqa: F821
    subscriptions: Mapped[list["TrackedPageSubscription"]] = relationship(
        back_populates="tracked_page", cascade="all, delete-orphan"
    )
    posts: Mapped[list["Post"]] = relationship(back_populates="tracked_page")  # noqa: F821


class TrackedPageSubscription(Base):
    __tablename__ = "tracked_page_subscriptions"
    __table_args__ = (
        UniqueConstraint("tracked_page_id", "user_id", name="uq_subscription_page_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tracked_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracked_pages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    auto_like: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_comment: Mapped[bool] = mapped_column(Boolean, default=True)
    polling_mode: Mapped[PollingMode] = mapped_column(
        Enum(PollingMode, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=PollingMode.NORMAL,
    )
    tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    tracked_page: Mapped["TrackedPage"] = relationship(back_populates="subscriptions")
    user: Mapped["User"] = relationship(back_populates="subscriptions")  # noqa: F821
