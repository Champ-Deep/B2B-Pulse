"""Add linkedin_user_id, linkedin_user_name, session_expires_at, last_session_check to integration_accounts.

Revision ID: 006_integration_linkedin_fields
Revises: 005_poll_status_persistent
Create Date: 2026-02-27
"""

import sqlalchemy as sa
from alembic import op

revision = "006_integration_linkedin_fields"
down_revision = "005_poll_status_persistent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_accounts",
        sa.Column("linkedin_user_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("linkedin_user_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("session_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("last_session_check", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("integration_accounts", "last_session_check")
    op.drop_column("integration_accounts", "session_expires_at")
    op.drop_column("integration_accounts", "linkedin_user_name")
    op.drop_column("integration_accounts", "linkedin_user_id")
