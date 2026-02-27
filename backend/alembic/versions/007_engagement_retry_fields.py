"""Add retry_count and last_retry_at to engagement_actions.

Revision ID: 007_engagement_retry_fields
Revises: 006_integration_linkedin_fields
Create Date: 2026-02-27
"""

import sqlalchemy as sa
from alembic import op

revision = "007_engagement_retry_fields"
down_revision = "006_integration_linkedin_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "engagement_actions",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "engagement_actions",
        sa.Column("last_retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("engagement_actions", "last_retry_at")
    op.drop_column("engagement_actions", "retry_count")
