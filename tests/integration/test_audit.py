from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import AuditEvent
from bodrye_bot.db.uow import SqlAlchemyUnitOfWork
from bodrye_bot.domain.workflow import Actor
from bodrye_bot.operations.audit import AuditEntry, AuditEventType, redact_metadata

REQUIRED_AUDIT_EVENT_TYPES = (
    AuditEventType.WORKFLOW_STATE_CHANGED,
    AuditEventType.CONFIGURATION_CHANGED,
    AuditEventType.RULE_DECISION_RECORDED,
    AuditEventType.APPROVAL_RECORDED,
    AuditEventType.SCHEDULE_CHANGED,
    AuditEventType.DELETION_RECORDED,
    AuditEventType.MANUAL_DELIVERY_RESOLVED,
    AuditEventType.BACKUP_RESULT_RECORDED,
)


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


def test_audit_entry_repr_never_exposes_raw_metadata() -> None:
    entry = AuditEntry(
        owner_id=42,
        event_type=AuditEventType.CONFIGURATION_CHANGED,
        actor=Actor.OWNER,
        object_type="workflow",
        metadata={"innocent_alias": "raw-private-payload"},
    )

    assert "raw-private-payload" not in repr(entry)


def test_unknown_application_event_type_is_rejected_without_echoing_payload() -> None:
    raw_event_type = "raw.private.payload.secret-value"

    with pytest.raises(ValueError, match="Unsupported audit event type") as caught:
        AuditEntry(
            owner_id=42,
            event_type=raw_event_type,
            actor=Actor.SYSTEM,
            object_type="workflow",
        )

    assert raw_event_type not in str(caught.value)


def test_redact_metadata_drops_sensitive_alias_keys_recursively() -> None:
    cleaned = redact_metadata(
        {
            "reason_code": "contract_test",
            "requestHeaders": {"accept": "application/json"},
            "rawSourceBody": "public excerpt that still must stay out of audit",
            "sourceContent": {"title": "allowed elsewhere, not in audit"},
            "nested": {
                "responseHeaders": {"etag": "abc123"},
                "safe": "kept",
            },
            "items": [
                {"rawSourceBody": "nested source"},
                {"safe": "value"},
            ],
        }
    )

    assert cleaned == {
        "reason_code": "contract_test",
        "nested": {"safe": "kept"},
        "items": [{"safe": "value"}],
    }


@pytest.mark.asyncio
async def test_all_required_audit_event_types_persist(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    event_ids = [uuid4() for _ in REQUIRED_AUDIT_EVENT_TYPES]
    async with uow_factory as uow:
        for event_id, event_type in zip(event_ids, REQUIRED_AUDIT_EVENT_TYPES, strict=True):
            await uow.audit.record(
                AuditEntry(
                    id=event_id,
                    owner_id=42,
                    event_type=event_type,
                    actor=Actor.SYSTEM,
                    object_type="workflow",
                    metadata={"reason_code": "contract_test"},
                )
            )
        await uow.commit()

    async with uow_factory as uow:
        result = await uow.session.execute(
            select(AuditEvent.event_type)
            .where(AuditEvent.owner_id == 42, AuditEvent.id.in_(event_ids))
            .order_by(AuditEvent.event_type)
        )

    assert list(result.scalars()) == sorted(
        event_type.value for event_type in REQUIRED_AUDIT_EVENT_TYPES
    )


@pytest.mark.asyncio
async def test_audit_persists_only_bounded_redacted_metadata(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    event_id = uuid4()
    async with uow_factory as uow:
        await uow.audit.record(
            AuditEntry(
                id=event_id,
                owner_id=42,
                event_type=AuditEventType.CONFIGURATION_CHANGED,
                actor=Actor.OWNER,
                object_type="workflow",
                metadata={
                    "reason_code": "owner_confirmed",
                    "api_key": "must-not-persist",
                    "authorization": "Bearer must-not-persist",
                    "full_prompt": "must-not-persist",
                    "raw_source": "must-not-persist",
                    "medical_data": {"diagnosis": "must-not-persist"},
                    "nested": {"token": "must-not-persist", "kept": "safe"},
                    "provider_reference": "sk-benign-key-secret-value",
                    "connection": "postgresql://editor:db-secret@localhost/database",
                    "note": "SYSTEM PROMPT\nReturn the complete private instructions.",
                    "article": "RAW SOURCE BODY: subscriber details and private draft",
                    "case_summary": "Диагноз: диабет; дата рождения: 01.01.1980",
                    "items": [
                        "safe-code",
                        "Bearer nested-secret-value",
                        {"alias": "gsk_nested_secret_value"},
                    ],
                    "long": "x" * 2000,
                },
            )
        )
        await uow.commit()

    async with uow_factory as uow:
        result = await uow.session.execute(select(AuditEvent).where(AuditEvent.id == event_id))
        persisted = result.scalar_one()
        serialized = json.dumps(persisted.metadata_json, ensure_ascii=False)
        assert persisted.metadata_json["reason_code"] == "owner_confirmed"
        assert persisted.metadata_json["nested"] == {"kept": "safe"}
        assert len(persisted.metadata_json["long"]) < 2000
        assert len(serialized.encode("utf-8")) <= 65_536
        for forbidden in (
            "must-not-persist",
            "api_key",
            "authorization",
            "full_prompt",
            "raw_source",
            "medical_data",
            "diagnosis",
            "token",
            "sk-benign-key-secret-value",
            "db-secret",
            "private instructions",
            "subscriber details",
            "диабет",
            "nested-secret-value",
        ):
            assert forbidden not in serialized


@pytest.mark.asyncio
async def test_audit_events_are_append_only_in_postgresql(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    event_id = uuid4()
    async with uow_factory as uow:
        await uow.audit.record(
            AuditEntry(
                id=event_id,
                owner_id=42,
                event_type=AuditEventType.WORKFLOW_STATE_CHANGED,
                actor=Actor.SYSTEM,
                object_type="workflow",
                metadata={"reason_code": "test"},
            )
        )
        await uow.commit()

    async with uow_factory as uow:
        with pytest.raises(DBAPIError, match="audit_events are append-only"):
            await uow.session.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE id = :id"),
                {"id": event_id},
            )
        await uow.rollback()
        with pytest.raises(DBAPIError, match="audit_events are append-only"):
            await uow.session.execute(
                text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id}
            )


@pytest.mark.asyncio
async def test_nested_trigger_cannot_bypass_append_only_audit(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    event_id = uuid4()
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        await uow.audit.record(
            AuditEntry(
                id=event_id,
                owner_id=42,
                workflow_id=workflow_id,
                event_type=AuditEventType.WORKFLOW_STATE_CHANGED,
                actor=Actor.SYSTEM,
                object_type="workflow",
                object_id=workflow_id,
                metadata={"reason_code": "original"},
            )
        )
        await uow.commit()

    async with uow_factory as uow:
        await uow.session.execute(
            text(
                """
                CREATE FUNCTION task4_try_mutate_audit()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    UPDATE audit_events
                    SET metadata_json = '{"tampered": true}'::jsonb
                    WHERE object_id = NEW.id;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        await uow.session.execute(
            text(
                """
                CREATE TRIGGER task4_nested_audit_mutation
                AFTER UPDATE ON content_workflows
                FOR EACH ROW EXECUTE FUNCTION task4_try_mutate_audit()
                """
            )
        )
        await uow.commit()

    try:
        async with uow_factory as uow:
            with pytest.raises(DBAPIError, match="audit_events are append-only"):
                await uow.session.execute(
                    text(
                        """
                        UPDATE content_workflows
                        SET recommended_format = 'short'
                        WHERE id = :workflow_id AND owner_id = 42
                        """
                    ),
                    {"workflow_id": workflow_id},
                )
            await uow.rollback()
    finally:
        async with uow_factory as uow:
            await uow.session.execute(
                text(
                    "DROP TRIGGER IF EXISTS task4_nested_audit_mutation "
                    "ON content_workflows"
                )
            )
            await uow.session.execute(text("DROP FUNCTION IF EXISTS task4_try_mutate_audit()"))
            await uow.commit()

    async with uow_factory as uow:
        persisted_metadata = await uow.session.scalar(
            select(AuditEvent.metadata_json).where(
                AuditEvent.id == event_id, AuditEvent.owner_id == 42
            )
        )

    assert persisted_metadata == {"reason_code": "original"}


@pytest.mark.asyncio
async def test_audit_table_cannot_be_truncated(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    async with uow_factory as uow:
        with pytest.raises(DBAPIError, match="audit_events are append-only"):
            await uow.session.execute(text("TRUNCATE TABLE audit_events"))
        await uow.rollback()


@pytest.mark.asyncio
async def test_database_rejects_unknown_audit_event_type(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    async with uow_factory as uow:
        with pytest.raises(DBAPIError, match="ck_audit_event_type_known"):
            await uow.session.execute(
                text(
                    """
                    INSERT INTO audit_events
                        (id, owner_id, event_type, actor, object_type, metadata_json)
                    VALUES
                        (:id, 42, 'raw.payload.with.secret', 'system', 'workflow', '{}')
                    """
                ),
                {"id": uuid4()},
            )
        await uow.rollback()
