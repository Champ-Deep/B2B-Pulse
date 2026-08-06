"""Add the SKIPPED engagement-action status.

An action the safety gate refuses at execution time is not a failure. Before
this, the only terminal state available was ``failed``, which meant two wrong
things at once: the stale-action sweeper re-queued refusals back into the state
that caused them, and a paused account's untaken work counted against its
health funnel.

Revision ID: 012_action_skipped
Revises: 011_outbound
"""

from alembic import op

revision = "012_action_skipped"
down_revision = "011_outbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # Postgres, and alembic wraps migrations in one. IF NOT EXISTS keeps this
    # idempotent for re-runs.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE actionstatus ADD VALUE IF NOT EXISTS 'skipped'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type. Any row still carrying it
    # is moved to 'failed' so the column stays valid if the type is ever
    # rebuilt; the type itself keeps the (now unused) label.
    op.execute("UPDATE engagement_actions SET status = 'failed' WHERE status = 'skipped'")
