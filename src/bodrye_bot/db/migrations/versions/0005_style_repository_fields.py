"""Persist bounded style-learning metadata and confirmed edit observations.

Revision ID: 0005_style_repository_fields
Revises: 0004_audit_envelope_constraints
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_style_repository_fields"
down_revision: str | None = "0004_audit_envelope_constraints"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("style_rules", sa.Column("format", sa.String(16), nullable=True))
    op.add_column(
        "style_rules",
        sa.Column(
            "risks",
            postgresql.ARRAY(sa.String(32)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "style_rules",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(64)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "style_rules",
        sa.Column("pattern_key", sa.String(96), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "style_examples",
        sa.Column(
            "risks",
            postgresql.ARRAY(sa.String(32)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.create_table(
        "style_edit_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pattern_key", sa.String(96), nullable=False),
        sa.UniqueConstraint("id", "owner_id", name="uq_style_edit_observation_id_owner"),
        sa.ForeignKeyConstraint(
            ["profile_id", "owner_id"],
            ["style_profiles.id", "style_profiles.owner_id"],
            name="fk_style_edit_observation_profile_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "pattern_key ~ '^[a-z][a-z0-9-]{0,31}:[a-z][a-z0-9-]{0,63}$'",
            name="style_edit_pattern_key_canonical",
        ),
    )


def downgrade() -> None:
    op.drop_table("style_edit_observations")
    op.drop_column("style_examples", "risks")
    op.drop_column("style_rules", "pattern_key")
    op.drop_column("style_rules", "tags")
    op.drop_column("style_rules", "risks")
    op.drop_column("style_rules", "format")
