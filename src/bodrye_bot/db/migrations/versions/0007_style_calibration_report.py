"""Bind activated style profiles to immutable calibration reports.

Revision ID: 0007_style_calibration_report
Revises: 0006_style_idempotency_bounds
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_style_calibration_report"
down_revision: str | None = "0006_style_idempotency_bounds"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("style_profiles", sa.Column("calibration_report_id", sa.Uuid()))
    op.add_column("style_profiles", sa.Column("calibration_report_hash", sa.String(64)))
    op.create_unique_constraint(
        "uq_style_profile_owner_report",
        "style_profiles",
        ["owner_id", "calibration_report_id"],
    )
    op.create_check_constraint(
        "style_profile_calibration_hash_sha256",
        "style_profiles",
        "calibration_report_hash IS NULL OR calibration_report_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "style_profile_calibration_hash_sha256", "style_profiles", type_="check"
    )
    op.drop_constraint(
        "uq_style_profile_owner_report", "style_profiles", type_="unique"
    )
    op.drop_column("style_profiles", "calibration_report_hash")
    op.drop_column("style_profiles", "calibration_report_id")
