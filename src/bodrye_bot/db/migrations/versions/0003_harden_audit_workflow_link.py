"""Allow audit unlinking only after its workflow is actually deleted.

Revision ID: 0003_harden_audit_workflow_link
Revises: 0002_audit_events_append_only
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision: str = "0003_harden_audit_workflow_link"
down_revision: str | None = "0002_audit_events_append_only"
branch_labels: str | None = None
depends_on: str | None = None


_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION reject_audit_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND pg_trigger_depth() > 1
       AND OLD.workflow_id IS NOT NULL
       AND NEW.workflow_id IS NULL
       {parent_absent_clause}
       AND (to_jsonb(NEW) - 'workflow_id') =
           (to_jsonb(OLD) - 'workflow_id') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_events are append-only'
        USING ERRCODE = '23514',
              CONSTRAINT = 'trg_audit_events_append_only';
END;
$$
"""


def upgrade() -> None:
    op.execute(
        _FUNCTION_SQL.format(
            parent_absent_clause="""AND NOT EXISTS (
           SELECT 1
           FROM content_workflows
           WHERE id = OLD.workflow_id
             AND owner_id = OLD.owner_id
       )"""
        )
    )


def downgrade() -> None:
    op.execute(_FUNCTION_SQL.format(parent_absent_clause=""))
