"""Constrain audit envelope fields at the database boundary.

Revision ID: 0004_audit_envelope_constraints
Revises: 0003_harden_audit_workflow_link
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision: str = "0004_audit_envelope_constraints"
down_revision: str | None = "0003_harden_audit_workflow_link"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "audit_object_type_known",
        "audit_events",
        "object_type IN ("
        "'workflow', 'configuration', 'style_rule', 'approval', "
        "'schedule', 'deletion', 'delivery', 'backup'"
        ")",
    )
    op.create_check_constraint(
        "audit_trace_id_safe",
        "audit_events",
        "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "audit_trace_id_safe", "audit_events", type_="check"
    )
    op.drop_constraint(
        "audit_object_type_known", "audit_events", type_="check"
    )
