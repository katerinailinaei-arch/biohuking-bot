"""Require active style profiles to retain their calibration evidence.

Revision ID: 0008_style_profile_binding
Revises: 0007_style_calibration_report
"""
from __future__ import annotations

from alembic import op

revision: str = "0008_style_profile_binding"
down_revision: str | None = "0007_style_calibration_report"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM style_profiles
                WHERE status = 'active'
                  AND (
                      activated_at IS NULL
                      OR calibration_report_id IS NULL
                      OR calibration_report_hash IS NULL
                  )
            ) THEN
                RAISE EXCEPTION
                    'style migration preflight failed: active style profiles '
                    'require calibration report binding';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM style_profiles
                WHERE (calibration_report_id IS NULL) <> (calibration_report_hash IS NULL)
            ) THEN
                RAISE EXCEPTION
                    'style migration preflight failed: calibration report id/hash must be paired';
            END IF;
        END $$;
        """
    )
    op.create_check_constraint(
        "style_profile_calibration_report_pair",
        "style_profiles",
        "(calibration_report_id IS NULL) = (calibration_report_hash IS NULL)",
    )
    op.create_check_constraint(
        "style_profile_active_calibration_bound",
        "style_profiles",
        "status <> 'active' OR (activated_at IS NOT NULL "
        "AND calibration_report_id IS NOT NULL "
        "AND calibration_report_hash IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "style_profile_active_calibration_bound", "style_profiles", type_="check"
    )
    op.drop_constraint(
        "style_profile_calibration_report_pair", "style_profiles", type_="check"
    )
