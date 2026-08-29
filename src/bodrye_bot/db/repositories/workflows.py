from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from bodrye_bot.db.models import ContentWorkflow
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.workflow import Actor, WorkflowState
from bodrye_bot.operations.audit import AuditEntry, AuditEventType, SqlAlchemyAuditWriter
from bodrye_bot.ports.repositories import ConcurrentUpdate


class SqlAlchemyWorkflowRepository:
    def __init__(
        self,
        session: AsyncSession,
        audit: SqlAlchemyAuditWriter,
        *,
        ensure_active: Callable[[], None],
    ) -> None:
        self._session = session
        self._audit = audit
        self._ensure_active = ensure_active

    async def get(self, owner_id: int, workflow_id: UUID) -> WorkflowState:
        self._ensure_active()
        result = await self._session.execute(
            select(ContentWorkflow).where(
                ContentWorkflow.id == workflow_id, ContentWorkflow.owner_id == owner_id
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        return _to_state(workflow)

    async def save(self, workflow: WorkflowState, expected_version: int) -> None:
        self._ensure_active()
        if workflow.id is None or workflow.owner_id is None or workflow.version is None:
            raise ValueError("Persisted WorkflowState requires id, owner_id and version")
        if workflow.version != expected_version:
            raise ConcurrentUpdate

        result = cast(
            CursorResult[object],
            await self._session.execute(
                update(ContentWorkflow)
                .where(
                    ContentWorkflow.id == workflow.id,
                    ContentWorkflow.owner_id == workflow.owner_id,
                    ContentWorkflow.version == expected_version,
                )
                .values(status=workflow.status, version=expected_version + 1)
            ),
        )
        if result.rowcount != 1:
            exists = await self._session.scalar(
                select(ContentWorkflow.id).where(
                    ContentWorkflow.id == workflow.id,
                    ContentWorkflow.owner_id == workflow.owner_id,
                )
            )
            if exists is None:
                raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
            raise ConcurrentUpdate

        await self._audit.record(
            AuditEntry(
                owner_id=workflow.owner_id,
                workflow_id=workflow.id,
                event_type=AuditEventType.WORKFLOW_STATE_CHANGED,
                actor=Actor.SYSTEM,
                object_type="workflow",
                object_id=workflow.id,
                metadata={
                    "previous_version": expected_version,
                    "new_version": expected_version + 1,
                    "status": workflow.status.value,
                },
            )
        )


def _to_state(workflow: ContentWorkflow) -> WorkflowState:
    return WorkflowState(
        status=workflow.status,
        current_version_id=(
            str(workflow.current_version_id) if workflow.current_version_id is not None else None
        ),
        id=workflow.id,
        owner_id=workflow.owner_id,
        version=workflow.version,
    )


__all__ = ["SqlAlchemyWorkflowRepository"]
