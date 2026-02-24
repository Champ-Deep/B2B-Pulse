"""add org_invites table

Revision ID: 002_org_invites
Revises: 001_initial
Create Date: 2026-02-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002_org_invites"
down_revision = "001_initial"
branch_labels = None
depends_on = None

invitestatus_enum = postgresql.ENUM(
    "pending", "accepted", "expired", "revoked",
    name="invitestatus", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    invitestatus_enum.create(bind, checkfirst=True)

    op.create_table(
        "org_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id"),
            nullable=False,
        ),
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "invite_code", sa.String(64), unique=True, nullable=False, index=True
        ),
        sa.Column("status", invitestatus_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("org_invites")
    bind = op.get_bind()
    invitestatus_enum.drop(bind, checkfirst=True)
