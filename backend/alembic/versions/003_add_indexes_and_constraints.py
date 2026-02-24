"""add indexes on engagement_actions.status, integration_accounts.is_active,
and unique constraint on tracked_page_subscriptions(tracked_page_id, user_id)

Revision ID: 003_indexes_constraints
Revises: 002_org_invites
Create Date: 2026-02-24
"""

from alembic import op

revision = "003_indexes_constraints"
down_revision = "002_org_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_engagement_actions_status",
        "engagement_actions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_integration_accounts_is_active",
        "integration_accounts",
        ["is_active"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_subscription_page_user",
        "tracked_page_subscriptions",
        ["tracked_page_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_subscription_page_user", "tracked_page_subscriptions", type_="unique")
    op.drop_index("ix_integration_accounts_is_active", table_name="integration_accounts")
    op.drop_index("ix_engagement_actions_status", table_name="engagement_actions")
