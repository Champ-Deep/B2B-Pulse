import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.integration import Platform

# re-export Platform for convenience (used in queries)
__all__ = ["Post", "Platform"]


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tracked_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracked_pages.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, create_constraint=False, native_enum=False), nullable=False
    )
    external_post_id: Mapped[str] = mapped_column(
        String(512), nullable=False, index=True, unique=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_platform: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    tracked_page: Mapped["TrackedPage"] = relationship(back_populates="posts")  # noqa: F821
    engagement_actions: Mapped[list["EngagementAction"]] = relationship(  # noqa: F821
        back_populates="post"
    )
