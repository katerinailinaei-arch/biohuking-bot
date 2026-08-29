from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode


class WorkflowStatus(StrEnum):
    INGESTED = "ingested"
    EXTRACTED = "extracted"
    EXTRACTION_CONFIRMED = "extraction_confirmed"
    CLAIMS_REVIEW_PENDING = "claims_review_pending"
    CLAIMS_REVIEW_PASSED = "claims_review_passed"
    CLAIMS_REVIEW_BLOCKED = "claims_review_blocked"
    ANGLES_READY = "angles_ready"
    ANGLE_SELECTED = "angle_selected"
    DRAFT = "draft"
    DRAFT_REVIEW_PENDING = "draft_review_pending"
    DRAFT_REVIEW_PASSED = "draft_review_passed"
    DRAFT_REVIEW_BLOCKED = "draft_review_blocked"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Actor(StrEnum):
    OWNER = "owner"
    SYSTEM = "system"
    WORKER = "worker"


@dataclass(frozen=True)
class WorkflowState:
    status: WorkflowStatus
    current_version_id: str | None = None
    current_hash: str | None = None
    review_version_id: str | None = None
    review_hash: str | None = None
    approval_version_id: str | None = None
    approval_hash: str | None = None
    delivery_confirmed_not_sent: bool = False
    id: UUID | None = None
    owner_id: int | None = None
    version: int | None = None


@dataclass(frozen=True)
class Transition:
    source: WorkflowStatus
    target: WorkflowStatus
    actor: Actor


_LINEAR_TRANSITIONS = {
    (WorkflowStatus.INGESTED, WorkflowStatus.EXTRACTED),
    (WorkflowStatus.EXTRACTED, WorkflowStatus.EXTRACTION_CONFIRMED),
    (WorkflowStatus.EXTRACTION_CONFIRMED, WorkflowStatus.CLAIMS_REVIEW_PENDING),
    (WorkflowStatus.CLAIMS_REVIEW_PENDING, WorkflowStatus.CLAIMS_REVIEW_PASSED),
    (WorkflowStatus.CLAIMS_REVIEW_PENDING, WorkflowStatus.CLAIMS_REVIEW_BLOCKED),
    (WorkflowStatus.CLAIMS_REVIEW_PASSED, WorkflowStatus.ANGLES_READY),
    (WorkflowStatus.ANGLES_READY, WorkflowStatus.ANGLE_SELECTED),
    (WorkflowStatus.ANGLE_SELECTED, WorkflowStatus.DRAFT),
    (WorkflowStatus.DRAFT, WorkflowStatus.DRAFT_REVIEW_PENDING),
    (WorkflowStatus.DRAFT_REVIEW_PENDING, WorkflowStatus.DRAFT_REVIEW_PASSED),
    (WorkflowStatus.DRAFT_REVIEW_PENDING, WorkflowStatus.DRAFT_REVIEW_BLOCKED),
    (WorkflowStatus.DRAFT_REVIEW_PASSED, WorkflowStatus.APPROVED),
    (WorkflowStatus.APPROVED, WorkflowStatus.SCHEDULED),
    (WorkflowStatus.SCHEDULED, WorkflowStatus.PROCESSING),
    (WorkflowStatus.PROCESSING, WorkflowStatus.PUBLISHED),
    (WorkflowStatus.PROCESSING, WorkflowStatus.FAILED),
    (WorkflowStatus.PROCESSING, WorkflowStatus.DELIVERY_UNKNOWN),
    (WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED),
}

_RETURN_TRANSITIONS = {
    (WorkflowStatus.EXTRACTED, WorkflowStatus.INGESTED),
    (WorkflowStatus.CLAIMS_REVIEW_BLOCKED, WorkflowStatus.EXTRACTION_CONFIRMED),
    (WorkflowStatus.DRAFT_REVIEW_BLOCKED, WorkflowStatus.DRAFT),
    (WorkflowStatus.DRAFT_REVIEW_PASSED, WorkflowStatus.DRAFT),
    (WorkflowStatus.APPROVED, WorkflowStatus.DRAFT),
    (WorkflowStatus.FAILED, WorkflowStatus.SCHEDULED),
    (WorkflowStatus.DELIVERY_UNKNOWN, WorkflowStatus.PUBLISHED),
    (WorkflowStatus.DELIVERY_UNKNOWN, WorkflowStatus.SCHEDULED),
}

_PRE_PROCESSING = {
    WorkflowStatus.INGESTED,
    WorkflowStatus.EXTRACTED,
    WorkflowStatus.EXTRACTION_CONFIRMED,
    WorkflowStatus.CLAIMS_REVIEW_PENDING,
    WorkflowStatus.CLAIMS_REVIEW_PASSED,
    WorkflowStatus.CLAIMS_REVIEW_BLOCKED,
    WorkflowStatus.ANGLES_READY,
    WorkflowStatus.ANGLE_SELECTED,
    WorkflowStatus.DRAFT,
    WorkflowStatus.DRAFT_REVIEW_PENDING,
    WorkflowStatus.DRAFT_REVIEW_PASSED,
    WorkflowStatus.DRAFT_REVIEW_BLOCKED,
    WorkflowStatus.APPROVED,
    WorkflowStatus.SCHEDULED,
}

ALLOWED_TRANSITIONS = frozenset(
    _LINEAR_TRANSITIONS
    | _RETURN_TRANSITIONS
    | {(status, WorkflowStatus.REJECTED) for status in _PRE_PROCESSING}
)


class WorkflowPolicy:
    def transition(
        self, state: WorkflowState, target: WorkflowStatus, actor: Actor
    ) -> WorkflowState:
        pair = (state.status, target)
        if pair not in ALLOWED_TRANSITIONS:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)

        if state.status is WorkflowStatus.DELIVERY_UNKNOWN and actor is not Actor.OWNER:
            raise SafeError.for_code(SafeErrorCode.DELIVERY_UNKNOWN)

        if pair == (WorkflowStatus.FAILED, WorkflowStatus.SCHEDULED):
            if not state.delivery_confirmed_not_sent:
                raise SafeError.for_code(SafeErrorCode.DELIVERY_UNKNOWN)

        if target is WorkflowStatus.APPROVED and not self._review_matches_current(state):
            raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)

        if target is WorkflowStatus.SCHEDULED and not self._approval_matches_current(state):
            raise SafeError.for_code(SafeErrorCode.APPROVAL_STALE)

        if target is WorkflowStatus.DRAFT:
            return replace(
                state,
                status=target,
                review_version_id=None,
                review_hash=None,
                approval_version_id=None,
                approval_hash=None,
            )

        return replace(state, status=target)

    @staticmethod
    def _review_matches_current(state: WorkflowState) -> bool:
        return (
            state.current_version_id is not None
            and state.current_hash is not None
            and state.review_version_id == state.current_version_id
            and state.review_hash == state.current_hash
        )

    @staticmethod
    def _approval_matches_current(state: WorkflowState) -> bool:
        return (
            state.current_version_id is not None
            and state.current_hash is not None
            and state.approval_version_id == state.current_version_id
            and state.approval_hash == state.current_hash
        )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "Actor",
    "Transition",
    "WorkflowPolicy",
    "WorkflowState",
    "WorkflowStatus",
    "content_hash",
]
