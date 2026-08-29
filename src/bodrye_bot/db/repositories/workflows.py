from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from bodrye_bot.db.models import Approval, ContentWorkflow, DraftVersion, ReviewDecision
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.workflow import Actor, WorkflowState
from bodrye_bot.operations.audit import (
    AuditEntry,
    AuditEventType,
    AuditObjectType,
    SqlAlchemyAuditWriter,
)
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
        current_draft: DraftVersion | None = None
        latest_review: ReviewDecision | None = None
        active_approval: Approval | None = None
        if workflow.current_version_id is not None:
            current_draft = await self._session.scalar(
                select(DraftVersion).where(
                    DraftVersion.id == workflow.current_version_id,
                    DraftVersion.workflow_id == workflow.id,
                    DraftVersion.owner_id == owner_id,
                )
            )
            latest_review = await self._session.scalar(
                select(ReviewDecision)
                .where(
                    ReviewDecision.draft_version_id == workflow.current_version_id,
                    ReviewDecision.owner_id == owner_id,
                )
                .order_by(
                    ReviewDecision.reviewed_at.desc(),
                    ReviewDecision.created_at.desc(),
                    ReviewDecision.id.desc(),
                )
                .limit(1)
            )
            active_approval = await self._session.scalar(
                select(Approval).where(
                    Approval.workflow_id == workflow.id,
                    Approval.draft_version_id == workflow.current_version_id,
                    Approval.owner_id == owner_id,
                    Approval.revoked_at.is_(None),
                )
            )
        return _to_state(workflow, current_draft, latest_review, active_approval)

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
                .values(
                    status=workflow.status,
                    current_version_id=(
                        UUID(workflow.current_version_id)
                        if workflow.current_version_id is not None
                        else None
                    ),
                    version=expected_version + 1,
                )
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
                object_type=AuditObjectType.WORKFLOW,
                object_id=workflow.id,
                metadata={
                    "previous_version": expected_version,
                    "new_version": expected_version + 1,
                    "status": workflow.status.value,
                },
            )
        )


def _to_state(
    workflow: ContentWorkflow,
    current_draft: DraftVersion | None,
    latest_review: ReviewDecision | None,
    active_approval: Approval | None,
) -> WorkflowState:
    current_version_id = (
        str(workflow.current_version_id) if workflow.current_version_id is not None else None
    )
    current_hash = current_draft.body_hash if current_draft is not None else None
    review_passed = latest_review is not None and latest_review.status == "passed"
    return WorkflowState(
        status=workflow.status,
        current_version_id=current_version_id,
        current_hash=current_hash,
        review_version_id=current_version_id if review_passed else None,
        review_hash=current_hash if review_passed else None,
        approval_version_id=(
            str(active_approval.draft_version_id) if active_approval is not None else None
        ),
        approval_hash=(
            active_approval.content_hash if active_approval is not None else None
        ),
        id=workflow.id,
        owner_id=workflow.owner_id,
        version=workflow.version,
    )


__all__ = ["SqlAlchemyWorkflowRepository"]
