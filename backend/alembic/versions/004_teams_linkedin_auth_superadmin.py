"""Add teams, LinkedIn auth columns, and platform admin flag.

- Create teams table
- Add team_id FK to users and org_invites
- Add linkedin_id (unique) to users
- Add is_platform_admin to users
- Add team_leader to userrole enum
- Make hashed_password nullable

Revision ID: 004_teams_linkedin_auth
Revises: 003_indexes_constraints
Create Date: 2026-02-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "004_teams_linkedin_auth"
down_revision = "003_indexes_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Add enum value (must be outside transaction in older PG) ---
    op.execute("COMMIT")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'team_leader'")
    op.execute("BEGIN")

    # --- Create teams table ---
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_teams_org_id", "teams", ["org_id"])

    # --- Add team_id to users ---
    op.add_column(
        "users",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # --- Add team_id to org_invites ---
    op.add_column(
        "org_invites",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # --- Add linkedin_id to users (unique, indexed) ---
    op.add_column(
        "users",
        sa.Column("linkedin_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_users_linkedin_id", "users", ["linkedin_id"], unique=True)

    # --- Add is_platform_admin to users ---
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # --- Make hashed_password nullable (LinkedIn-only users won't have one) ---
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(255),
        nullable=True,
    )


def downgrade() -> None:
    # Reverse hashed_password to NOT NULL (fill blanks first)
    op.execute("UPDATE users SET hashed_password = '' WHERE hashed_password IS NULL")
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(255),
        nullable=False,
    )

    op.drop_column("users", "is_platform_admin")
    op.drop_index("ix_users_linkedin_id", table_name="users")
    op.drop_column("users", "linkedin_id")
    op.drop_column("org_invites", "team_id")
    op.drop_column("users", "team_id")
    op.drop_index("ix_teams_org_id", table_name="teams")
    op.drop_table("teams")
    # Note: cannot remove 'team_leader' from userrole enum in PostgreSQL
