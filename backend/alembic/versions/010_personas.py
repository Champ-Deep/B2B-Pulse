"""Personas.

An org-level persona holds the shared direction; each account's persona
inherits from it and overrides only what makes that person distinct. That is
what lets one admin steer five accounts without flattening them into one voice.

It is also a cluster-safety control: five accounts generating comments from one
prompt produce five near-identical comments, which is precisely the correlation
signal ``safety/cluster.py`` measures.

Revision ID: 010_personas
Revises: 009_account_activity_ledger
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "010_personas"
down_revision = "009_account_activity_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Null account_id = the org-level persona others inherit from.
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("integration_accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("personas.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("voice", JSONB(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("expertise", JSONB(), nullable=True),
        sa.Column("content_pillars", JSONB(), nullable=True),
        sa.Column("icp", JSONB(), nullable=True),
        sa.Column("guardrails", JSONB(), nullable=True),
        sa.Column("autonomy", JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_personas_org_id", "personas", ["org_id"])
    op.create_index("ix_personas_account_id", "personas", ["account_id"])

    # One org-level persona per org, and one persona per account. Without
    # these, resolution would have to pick arbitrarily between duplicates.
    op.create_index(
        "uq_persona_org_level",
        "personas",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NULL"),
    )
    op.create_index(
        "uq_persona_per_account",
        "personas",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_persona_per_account", table_name="personas")
    op.drop_index("uq_persona_org_level", table_name="personas")
    op.drop_index("ix_personas_account_id", table_name="personas")
    op.drop_index("ix_personas_org_id", table_name="personas")
    op.drop_table("personas")
