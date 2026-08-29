from __future__ import annotations

from typing import Protocol, Self
from uuid import UUID

from bodrye_bot.domain.workflow import WorkflowState
from bodrye_bot.operations.audit import AuditEntry


class ConcurrentUpdate(Exception):
    """Raised when an optimistic workflow update loses a concurrent race."""


class WorkflowRepository(Protocol):
    async def get(self, owner_id: int, workflow_id: UUID) -> WorkflowState: ...

    async def save(self, workflow: WorkflowState, expected_version: int) -> None: ...


class AuditWriter(Protocol):
    async def record(self, event: AuditEntry) -> None: ...

    async def for_object(self, *, owner_id: int, object_id: UUID) -> list[AuditEntry]: ...


class UnitOfWork(Protocol):
    workflows: WorkflowRepository
    audit: AuditWriter

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: object, exc: object, traceback: object
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


__all__ = ["AuditWriter", "ConcurrentUpdate", "UnitOfWork", "WorkflowRepository"]
