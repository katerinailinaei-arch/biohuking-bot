"""Add durable owner-scoped digest delivery coordination.

Revision ID: 0009_digest_runs
Revises: 0008_style_profile_binding
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_digest_runs"
down_revision: str | None = "0008_style_profile_binding"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "digest_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("late", sa.Boolean()),
        sa.PrimaryKeyConstraint("id", name="pk_digest_runs"),
        sa.UniqueConstraint("owner_id", "digest_date", name="uq_digest_run_owner_date"),
        sa.CheckConstraint(
            "status IN ('processing', 'retryable', 'delivered', 'delivery_unknown')",
            name="digest_run_status_known",
        ),
    )


def downgrade() -> None:
    op.drop_table("digest_runs")
