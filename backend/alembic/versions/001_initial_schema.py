"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Define enums once — referenced by columns with create_type=False
userrole_enum = postgresql.ENUM(
    "owner", "admin", "member", "analyst", name="userrole", create_type=False
)
platform_enum = postgresql.ENUM(
    "linkedin", "meta", "whatsapp", name="platform", create_type=False
)
pagetype_enum = postgresql.ENUM(
    "personal", "company", "ig_business", "fb_page", name="pagetype", create_type=False
)
pollingmode_enum = postgresql.ENUM(
    "normal", "hunt", name="pollingmode", create_type=False
)
actiontype_enum = postgresql.ENUM(
    "like", "comment", name="actiontype", create_type=False
)
actionstatus_enum = postgresql.ENUM(
    "pending", "in_progress", "completed", "failed", name="actionstatus", create_type=False
)


def upgrade() -> None:
    # Create all enums first
    bind = op.get_bind()
    for enum in [userrole_enum, platform_enum, pagetype_enum, pollingmode_enum, actiontype_enum, actionstatus_enum]:
        enum.create(bind, checkfirst=True)

    # --- orgs ---
    op.create_table(
        "orgs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", userrole_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- user_profiles ---
    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("markdown_text", sa.Text(), nullable=True),
        sa.Column("tone_settings", postgresql.JSONB(), nullable=True),
        sa.Column("automation_settings", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- integration_accounts ---
    op.create_table(
        "integration_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_cookies", postgresql.JSONB(), nullable=True),
        sa.Column("settings", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- tracked_pages ---
    op.create_table(
        "tracked_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id"),
            nullable=False,
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("page_type", pagetype_enum, nullable=False),
        sa.Column("active", sa.Boolean(), default=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- tracked_page_subscriptions ---
    op.create_table(
        "tracked_page_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tracked_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tracked_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("auto_like", sa.Boolean(), default=True),
        sa.Column("auto_comment", sa.Boolean(), default=True),
        sa.Column("polling_mode", pollingmode_enum, nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- posts ---
    op.create_table(
        "posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tracked_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tracked_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column(
            "external_post_id",
            sa.String(512),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("created_at_platform", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- engagement_actions ---
    op.create_table(
        "engagement_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", actiontype_enum, nullable=False),
        sa.Column("status", actionstatus_enum, nullable=False),
        sa.Column("comment_text", sa.Text(), nullable=True),
        sa.Column("llm_response", postgresql.JSONB(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # --- audit_log ---
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("target_type", sa.String(100), nullable=True),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
    )

    # --- ai_avoid_phrases ---
    op.create_table(
        "ai_avoid_phrases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id"),
            nullable=True,
        ),
        sa.Column("phrase", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), default=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_avoid_phrases")
    op.drop_table("audit_log")
    op.drop_table("engagement_actions")
    op.drop_table("posts")
    op.drop_table("tracked_page_subscriptions")
    op.drop_table("tracked_pages")
    op.drop_table("integration_accounts")
    op.drop_table("user_profiles")
    op.drop_table("users")
    op.drop_table("orgs")

    # Drop enums
    bind = op.get_bind()
    for enum in [actionstatus_enum, actiontype_enum, pollingmode_enum, pagetype_enum, platform_enum, userrole_enum]:
        enum.drop(bind, checkfirst=True)
