"""Clerk auth identifiers and per-account warm-up/safety state.

Two changes land together because they are one decision: the merge of Social
Bot into B2B Pulse.

1. **Clerk becomes the identity provider.** ``users.clerk_user_id`` and
   ``orgs.clerk_org_id`` are the join keys to a Clerk token. Both are nullable
   so existing LinkedIn-OAuth users survive the migration — they simply get
   linked on their next sign-in, matched by email.

   ``users.hashed_password`` is dropped: with Clerk, we never see a password.
   Keeping a password column we no longer write is a liability, not a fallback.

2. **Accounts gain warm-up and safety state.** Every integration account now
   carries a stable device fingerprint, its effective caps, its position in the
   warm-up programme, and an optional per-account proxy.

   The default for existing rows is deliberate: they are backfilled into the
   **final** warm-up stage, not the first. These accounts have been running for
   weeks and already have real history — putting them back at "observe" would
   stop live automation dead. New accounts start at the beginning.

Revision ID: 008_clerk_auth_and_warmup
Revises: 007_engagement_retry_fields
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "008_clerk_auth_and_warmup"
down_revision = "007_engagement_retry_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Clerk identity ---
    op.add_column("users", sa.Column("clerk_user_id", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_users_clerk_user_id", "users", ["clerk_user_id"])
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"])

    op.add_column("orgs", sa.Column("clerk_org_id", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_orgs_clerk_org_id", "orgs", ["clerk_org_id"])
    op.create_index("ix_orgs_clerk_org_id", "orgs", ["clerk_org_id"])

    # Clerk owns credentials now; we should not be storing password hashes.
    op.drop_column("users", "hashed_password")

    # --- Per-account warm-up and safety state ---
    op.add_column(
        "integration_accounts",
        sa.Column("device_fingerprint", JSONB(), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("daily_caps", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("warmup_state", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("integration_accounts", sa.Column("mode", sa.String(64), nullable=True))
    op.add_column("integration_accounts", sa.Column("proxy", JSONB(), nullable=True))
    op.add_column(
        "integration_accounts",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("last_post_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Existing accounts have real history. Start them at the end of the
    # programme so live automation is not interrupted by the merge.
    op.execute(
        """
        UPDATE integration_accounts
        SET warmup_state = jsonb_build_object(
                'stage', 'full',
                'since', to_char(
                    COALESCE(created_at, now()) AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'
                ),
                'history', '[]'::jsonb,
                'backfilled', true
            ),
            daily_caps = jsonb_build_object('tier', 'standard'),
            mode = COALESCE(mode, 'account_based_engagement')
        WHERE warmup_state IS NULL OR warmup_state = '{}'::jsonb
        """
    )


def downgrade() -> None:
    op.drop_column("integration_accounts", "last_post_at")
    op.drop_column("integration_accounts", "last_active_at")
    op.drop_column("integration_accounts", "proxy")
    op.drop_column("integration_accounts", "mode")
    op.drop_column("integration_accounts", "warmup_state")
    op.drop_column("integration_accounts", "daily_caps")
    op.drop_column("integration_accounts", "device_fingerprint")

    op.add_column("users", sa.Column("hashed_password", sa.String(255), nullable=True))

    op.drop_index("ix_orgs_clerk_org_id", table_name="orgs")
    op.drop_constraint("uq_orgs_clerk_org_id", "orgs", type_="unique")
    op.drop_column("orgs", "clerk_org_id")

    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_constraint("uq_users_clerk_user_id", "users", type_="unique")
    op.drop_column("users", "clerk_user_id")
