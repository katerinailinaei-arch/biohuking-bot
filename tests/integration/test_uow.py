from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.uow import SqlAlchemyUnitOfWork
from bodrye_bot.domain.workflow import WorkflowStatus


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


@pytest.mark.asyncio
async def test_uow_rolls_back_mutation_and_audit_when_scope_raises(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id

    with pytest.raises(RuntimeError, match="abort"):
        async with uow_factory as uow:
            workflow = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
            await uow.workflows.save(
                replace(workflow, status=WorkflowStatus.EXTRACTED), expected_version=1
            )
            raise RuntimeError("abort")

    async with uow_factory as uow:
        stored = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        events = await uow.audit.for_object(owner_id=42, object_id=workflow_id)

    assert stored.status is WorkflowStatus.INGESTED
    assert stored.version == 1
    assert events == []


@pytest.mark.asyncio
async def test_uow_rolls_back_when_scope_exits_without_commit(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id

    async with uow_factory as uow:
        workflow = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        await uow.workflows.save(
            replace(workflow, status=WorkflowStatus.EXTRACTED), expected_version=1
        )

    async with uow_factory as uow:
        stored = await uow.workflows.get(owner_id=42, workflow_id=workflow_id)
        events = await uow.audit.for_object(owner_id=42, object_id=workflow_id)

    assert stored.status is WorkflowStatus.INGESTED
    assert stored.version == 1
    assert events == []


@pytest.mark.asyncio
async def test_uow_and_captured_repositories_reject_use_after_exit(
    uow_factory: SqlAlchemyUnitOfWork, seeded_workflow: object
) -> None:
    workflow_id = seeded_workflow.id

    async with uow_factory as uow:
        workflows = uow.workflows
        audit = uow.audit

    with pytest.raises(RuntimeError, match="UnitOfWork is not active"):
        await uow.commit()
    with pytest.raises(RuntimeError, match="UnitOfWork is not active"):
        await workflows.get(owner_id=42, workflow_id=workflow_id)
    with pytest.raises(RuntimeError, match="UnitOfWork is not active"):
        await audit.for_object(owner_id=42, object_id=workflow_id)


@pytest.mark.asyncio
async def test_explicit_rollback_finishes_the_active_unit_of_work(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    async with uow_factory as uow:
        await uow.rollback()
        with pytest.raises(RuntimeError, match="UnitOfWork transaction is finished"):
            await uow.commit()


@pytest.mark.asyncio
async def test_uow_rejects_nested_enter(
    uow_factory: SqlAlchemyUnitOfWork,
) -> None:
    async with uow_factory as uow:
        first_session = uow.session
        try:
            with pytest.raises(RuntimeError, match="UnitOfWork is already active"):
                await uow.__aenter__()
        finally:
            # The cleanup keeps the inherited broken implementation from leaking
            # its first session during the required RED run.
            if uow.session is not first_session:
                await first_session.close()
