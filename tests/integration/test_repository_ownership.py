from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import Approval, ContentWorkflow, DraftVersion, ReviewDecision
from bodrye_bot.db.uow import ConcurrentUpdate, SqlAlchemyUnitOfWork
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.workflow import Actor, WorkflowPolicy, WorkflowStatus
from bodrye_bot.operations.audit import AuditEventType


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


@pytest.mark.asyncio
async def test_repository_checks_owner_inside_query(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        with pytest.raises(SafeError) as caught:
            await uow.workflows.get(owner_id=999, workflow_id=workflow_id)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_missing_and_cross_owner_reads_return_the_same_neutral_error(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    async with uow_factory as uow:
        failures: list[SafeError] = []
        for owner_id, workflow_id in (
            (999, seeded_workflow.id),
            (42, uuid4()),
        ):
            with pytest.raises(SafeError) as caught:
                await uow.workflows.get(owner_id=owner_id, workflow_id=workflow_id)
            failures.append(caught.value)

    assert [failure.code for failure in failures] == [
        SafeErrorCode.OWNER_FORBIDDEN,
        SafeErrorCode.OWNER_FORBIDDEN,
    ]
    assert {
        (failure.message_ru, failure.preserved_ru, failure.next_action_ru)
        for failure in failures
    } == {
        (
            failures[0].message_ru,
            failures[0].preserved_ru,
            failures[0].next_action_ru,
        )
    }


@pytest.mark.asyncio
async def test_repository_rejects_cross_owner_write_without_disclosing_existence(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        owned = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        foreign_copy = replace(owned, owner_id=999)
        with pytest.raises(SafeError) as caught:
            await uow.workflows.save(foreign_copy, expected_version=1)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_stale_update_is_rejected(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        first = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        second = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        await uow.workflows.save(
            replace(first, status=WorkflowStatus.EXTRACTED), expected_version=1
        )
        with pytest.raises(ConcurrentUpdate):
            await uow.workflows.save(second, expected_version=1)


@pytest.mark.asyncio
async def test_stale_state_cannot_bypass_lock_with_current_expected_version(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        stale = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        await uow.workflows.save(
            replace(stale, status=WorkflowStatus.EXTRACTED), expected_version=1
        )

        with pytest.raises(ConcurrentUpdate):
            await uow.workflows.save(
                replace(stale, status=WorkflowStatus.REJECTED), expected_version=2
            )


@pytest.mark.asyncio
async def test_failed_mutation_never_creates_an_audit_event(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        owned = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        with pytest.raises(SafeError) as caught:
            await uow.workflows.save(replace(owned, owner_id=999), expected_version=1)
        events = await uow.audit.for_object(owner_id=42, object_id=workflow_id)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN
    assert events == []


@pytest.mark.asyncio
async def test_state_save_and_its_audit_event_commit_together(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    async with uow_factory as uow:
        workflow = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        await uow.workflows.save(
            replace(workflow, status=WorkflowStatus.EXTRACTED), expected_version=1
        )
        await uow.commit()

    async with uow_factory as uow:
        stored = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        events = await uow.audit.for_object(owner_id=42, object_id=workflow_id)

    assert stored.status is WorkflowStatus.EXTRACTED
    assert stored.version == 2
    assert [event.event_type for event in events] == [AuditEventType.WORKFLOW_STATE_CHANGED]


@pytest.mark.asyncio
async def test_repository_hydrates_current_review_and_active_approval_state(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    draft_id = uuid4()
    body_hash = "a" * 64
    now = datetime.now(UTC)
    async with uow_factory as uow:
        uow.session.add(
            DraftVersion(
                id=draft_id,
                owner_id=42,
                workflow_id=workflow_id,
                version_number=1,
                body="Проверенный текст",
                body_hash=body_hash,
                format="medium",
                headlines=[],
                public_sources=[],
                style_profile_version=1,
            )
        )
        await uow.session.flush()
        stored_workflow = await uow.session.get(ContentWorkflow, workflow_id)
        assert stored_workflow is not None
        stored_workflow.current_version_id = draft_id
        await uow.session.flush()
        uow.session.add(
            ReviewDecision(
                owner_id=42,
                draft_version_id=draft_id,
                status="passed",
                blocking_reasons=[],
                changed_claim_ids=[],
                reviewed_at=now,
                policy_version="medical-v1",
            )
        )
        await uow.session.flush()
        uow.session.add(
            Approval(
                owner_id=42,
                workflow_id=workflow_id,
                draft_version_id=draft_id,
                content_hash=body_hash,
                approved_by=Actor.OWNER,
                approved_at=now,
            )
        )
        await uow.session.flush()
        stored_workflow.status = WorkflowStatus.APPROVED
        await uow.commit()

    async with uow_factory as uow:
        hydrated = await uow.workflows.get(42, workflow_id)

    assert hydrated.current_version_id == str(draft_id)
    assert hydrated.current_hash == body_hash
    assert hydrated.review_version_id == str(draft_id)
    assert hydrated.review_hash == body_hash
    assert hydrated.approval_version_id == str(draft_id)
    assert hydrated.approval_hash == body_hash
    assert WorkflowPolicy().transition(
        hydrated, WorkflowStatus.SCHEDULED, Actor.OWNER
    ).status is WorkflowStatus.SCHEDULED


@pytest.mark.asyncio
async def test_repository_save_persists_the_selected_current_draft(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id
    draft_id = uuid4()
    body_hash = "b" * 64
    async with uow_factory as uow:
        uow.session.add(
            DraftVersion(
                id=draft_id,
                owner_id=42,
                workflow_id=workflow_id,
                version_number=1,
                body="Новая версия",
                body_hash=body_hash,
                format="medium",
                headlines=[],
                public_sources=[],
                style_profile_version=1,
            )
        )
        await uow.commit()

    async with uow_factory as uow:
        state = await uow.workflows.get(42, workflow_id)
        await uow.workflows.save(
            replace(
                state,
                status=WorkflowStatus.EXTRACTED,
                current_version_id=str(draft_id),
                current_hash=body_hash,
            ),
            expected_version=1,
        )
        await uow.commit()

    async with uow_factory as uow:
        persisted = await uow.workflows.get(42, workflow_id)

    assert persisted.current_version_id == str(draft_id)
    assert persisted.current_hash == body_hash
