"""Fence digest lifecycle operations with an attempt token.

Revision ID: 0010_digest_run_attempt
Revises: 0009_digest_runs
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_digest_run_attempt"
down_revision: str | None = "0009_digest_runs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("digest_runs", sa.Column("attempt_id", postgresql.UUID(as_uuid=True)))
    op.execute("UPDATE digest_runs SET attempt_id = gen_random_uuid() WHERE attempt_id IS NULL")
    op.alter_column("digest_runs", "attempt_id", nullable=False)


def downgrade() -> None:
    op.drop_column("digest_runs", "attempt_id")
