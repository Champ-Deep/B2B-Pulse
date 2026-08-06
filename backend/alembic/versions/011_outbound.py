"""Outbound: ICP targeting, prospects, and the approval queue.

Nothing in the outbound path reaches LinkedIn without passing through an
``outreach_suggestions`` row that a human approved. That indirection is the
point: the worst possible failure of the targeting or copywriting layer is a
bad suggestion sitting in a queue, rather than a bad message in a stranger's
inbox.

Revision ID: 011_outbound
Revises: 010_personas
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "011_outbound"
down_revision = "010_personas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "icp_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("integration_accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("titles", JSONB(), nullable=True),
        sa.Column("seniorities", JSONB(), nullable=True),
        sa.Column("industries", JSONB(), nullable=True),
        sa.Column("keywords", JSONB(), nullable=True),
        sa.Column("excluded_keywords", JSONB(), nullable=True),
        sa.Column("excluded_titles", JSONB(), nullable=True),
        sa.Column("locations", JSONB(), nullable=True),
        sa.Column("company_sizes", JSONB(), nullable=True),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("relevance_floor", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_icp_profiles_org_id", "icp_profiles", ["org_id"])
    op.create_index("ix_icp_profiles_account_id", "icp_profiles", ["account_id"])

    op.create_table(
        "outreach_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("integration_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("icp_id", UUID(as_uuid=True), nullable=True),
        sa.Column("member_urn", sa.String(255), nullable=False),
        sa.Column("public_id", sa.String(255), nullable=True),
        sa.Column("profile_url", sa.String(512), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=True),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("industry", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("source_ref", sa.String(512), nullable=True),
        sa.Column("context", JSONB(), nullable=True),
        sa.Column("relevance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevance_reasons", JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("last_touched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("variant", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_outreach_targets_org_id", "outreach_targets", ["org_id"])
    op.create_index("ix_outreach_targets_status", "outreach_targets", ["status"])
    op.create_index("ix_target_account_status", "outreach_targets", ["account_id", "status"])
    # One row per person per account: the foundation of "never touch the same
    # person twice".
    op.create_index(
        "ix_target_account_member", "outreach_targets", ["account_id", "member_urn"], unique=True
    )

    op.create_table(
        "outreach_suggestions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("integration_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("outreach_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("draft_text", sa.Text(), nullable=True),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevance_reasons", JSONB(), nullable=True),
        sa.Column("quality_score", sa.Integer(), nullable=True),
        sa.Column("quality_warnings", JSONB(), nullable=True),
        sa.Column("subject_urn", sa.String(512), nullable=True),
        sa.Column("generated_by", sa.String(128), nullable=True),
        sa.Column("step", sa.String(32), nullable=True),
        sa.Column("variant", sa.String(64), nullable=True),
        sa.Column("depends_on_id", UUID(as_uuid=True), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_outreach_suggestions_org_id", "outreach_suggestions", ["org_id"])
    op.create_index("ix_outreach_suggestions_status", "outreach_suggestions", ["status"])
    op.create_index("ix_suggestion_target_action", "outreach_suggestions", ["target_id", "action"])
    op.create_index("ix_suggestion_account_status", "outreach_suggestions", ["account_id", "status"])
    op.create_index("ix_suggestion_due", "outreach_suggestions", ["status", "scheduled_for"])


def downgrade() -> None:
    op.drop_table("outreach_suggestions")
    op.drop_table("outreach_targets")
    op.drop_table("icp_profiles")
