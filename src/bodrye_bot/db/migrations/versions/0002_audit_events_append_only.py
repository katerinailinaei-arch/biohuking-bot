"""Prevent updates and deletes of audit events.

Revision ID: 0002_audit_events_append_only
Revises: 0001_initial
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_audit_events_append_only"
down_revision: str | None = "0001_initial"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "audit_event_type_known",
        "audit_events",
        "event_type IN ("
        "'workflow.state_changed', "
        "'configuration.changed', "
        "'style.rule_decision', "
        "'publication.approval_recorded', "
        "'publication.schedule_changed', "
        "'memory.deletion_recorded', "
        "'publication.delivery_resolved_manually', "
        "'operations.backup_result_recorded'"
        ")",
    )
    op.execute(
        """
        CREATE FUNCTION reject_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND pg_trigger_depth() > 1
               AND OLD.workflow_id IS NOT NULL
               AND NEW.workflow_id IS NULL
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
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_event_mutation()")
    op.execute(
        "ALTER TABLE audit_events "
        "DROP CONSTRAINT IF EXISTS ck_audit_event_type_known"
    )
