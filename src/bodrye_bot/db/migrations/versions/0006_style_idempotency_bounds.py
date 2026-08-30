"""Add idempotent source edits and bounded style metadata.

Revision ID: 0006_style_idempotency_bounds
Revises: 0005_style_repository_fields
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_style_idempotency_bounds"
down_revision: str | None = "0005_style_repository_fields"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "style_edit_observations", sa.Column("source_edit_id", sa.Uuid(), nullable=True)
    )
    op.execute(
        "UPDATE style_edit_observations SET source_edit_id = id WHERE source_edit_id IS NULL"
    )
    op.alter_column("style_edit_observations", "source_edit_id", nullable=False)
    op.create_unique_constraint(
        "uq_style_edit_observation_source",
        "style_edit_observations",
        ["owner_id", "profile_id", "source_edit_id"],
    )
    op.create_check_constraint(
        "style_rule_risks_bounded", "style_rules", "cardinality(risks) <= 16"
    )
    op.create_check_constraint("style_rule_tags_bounded", "style_rules", "cardinality(tags) <= 32")
    op.create_check_constraint(
        "style_example_risks_bounded", "style_examples", "cardinality(risks) <= 16"
    )
    op.create_index(
        "uq_style_rule_proposed_pattern",
        "style_rules",
        ["owner_id", "profile_id", "pattern_key"],
        unique=True,
        postgresql_where=sa.text("status = 'proposed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_style_rule_proposed_pattern", table_name="style_rules")
    op.drop_constraint("style_example_risks_bounded", "style_examples", type_="check")
    op.drop_constraint("style_rule_tags_bounded", "style_rules", type_="check")
    op.drop_constraint("style_rule_risks_bounded", "style_rules", type_="check")
    op.drop_constraint(
        "uq_style_edit_observation_source", "style_edit_observations", type_="unique"
    )
    op.drop_column("style_edit_observations", "source_edit_id")
