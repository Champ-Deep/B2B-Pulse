"""Add last_polled_at and last_poll_status to tracked_pages.

Revision ID: 005_poll_status_persistent
Revises: 004_teams_linkedin_auth
Create Date: 2026-02-26
"""

import sqlalchemy as sa
from alembic import op

revision = "005_poll_status_persistent"
down_revision = "004_teams_linkedin_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracked_pages",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tracked_pages",
        sa.Column("last_poll_status", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracked_pages", "last_poll_status")
    op.drop_column("tracked_pages", "last_polled_at")
