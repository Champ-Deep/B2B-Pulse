"""Account activity ledger.

A durable record of every action an account performs, across every source.

This is distinct from ``engagement_actions``, and the distinction matters:
that table records what the *pipeline* did (one row per post per user), while
this records what the *account* did. Warm-up graduation asks "has this account
done thirty likes yet" — a question the pipeline table cannot answer, because
warm-up activity does not originate from a tracked-page post, and because
Redis rate-limit counters expire after a week.

It is also the audit trail. If an account gets restricted, this is the record
of exactly what it had been doing and when.

Revision ID: 009_account_activity_ledger
Revises: 008_clerk_auth_and_warmup
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "009_account_activity_ledger"
down_revision = "008_clerk_auth_and_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_activity",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("integration_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("subject_urn", sa.String(512), nullable=True),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("variant", sa.String(64), nullable=True),
        sa.Column("detail", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("ix_activity_account_action", "account_activity", ["account_id", "action"])
    op.create_index("ix_activity_account_time", "account_activity", ["account_id", "created_at"])
    op.create_index("ix_account_activity_org_id", "account_activity", ["org_id"])
    op.create_index("ix_account_activity_target_id", "account_activity", ["target_id"])
    op.create_index("ix_account_activity_created_at", "account_activity", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_account_activity_created_at", table_name="account_activity")
    op.drop_index("ix_account_activity_target_id", table_name="account_activity")
    op.drop_index("ix_account_activity_org_id", table_name="account_activity")
    op.drop_index("ix_activity_account_time", table_name="account_activity")
    op.drop_index("ix_activity_account_action", table_name="account_activity")
    op.drop_table("account_activity")
