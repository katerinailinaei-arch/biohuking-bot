from dataclasses import FrozenInstanceError, replace

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.workflow import (
    Actor,
    WorkflowPolicy,
    WorkflowState,
    WorkflowStatus,
    content_hash,
)


def state_for(status: WorkflowStatus) -> WorkflowState:
    return WorkflowState(
        status=status,
        current_version_id="version-1",
        current_hash="hash-1",
        review_version_id="version-1",
        review_hash="hash-1",
        approval_version_id="version-1",
        approval_hash="hash-1",
    )


def test_content_hash_is_stable_and_content_sensitive() -> None:
    assert content_hash("Точный текст") == content_hash("Точный текст")
    assert content_hash("Точный текст") != content_hash("Точный текст ")
    assert len(content_hash("Точный текст")) == 64


def test_workflow_state_is_immutable() -> None:
    state = state_for(WorkflowStatus.DRAFT)

    with pytest.raises(FrozenInstanceError):
        state.status = WorkflowStatus.APPROVED  # type: ignore[misc]


def test_schedule_requires_matching_current_approval() -> None:
    state = replace(
        state_for(WorkflowStatus.APPROVED),
        current_hash="new",
        approval_hash="old",
    )

    with pytest.raises(SafeError) as caught:
        WorkflowPolicy().transition(state, WorkflowStatus.SCHEDULED, Actor.OWNER)

    assert caught.value.code is SafeErrorCode.APPROVAL_STALE
    assert caught.value.trace_id


def test_approval_requires_matching_passed_review() -> None:
    state = replace(
        state_for(WorkflowStatus.DRAFT_REVIEW_PASSED),
        review_version_id="older-version",
    )

    with pytest.raises(SafeError) as caught:
        WorkflowPolicy().transition(state, WorkflowStatus.APPROVED, Actor.OWNER)

    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE


def test_new_draft_invalidates_review_and_approval() -> None:
    state = state_for(WorkflowStatus.APPROVED)

    result = WorkflowPolicy().transition(state, WorkflowStatus.DRAFT, Actor.OWNER)

    assert result.status is WorkflowStatus.DRAFT
    assert result.review_version_id is None
    assert result.review_hash is None
    assert result.approval_version_id is None
    assert result.approval_hash is None
    assert state.status is WorkflowStatus.APPROVED


@pytest.mark.parametrize("target", [WorkflowStatus.PUBLISHED, WorkflowStatus.SCHEDULED])
def test_delivery_unknown_has_owner_only_manual_exits(target: WorkflowStatus) -> None:
    state = state_for(WorkflowStatus.DELIVERY_UNKNOWN)

    with pytest.raises(SafeError):
        WorkflowPolicy().transition(state, target, Actor.WORKER)

    assert WorkflowPolicy().transition(state, target, Actor.OWNER).status is target


def test_failed_retry_requires_proof_that_message_was_not_sent() -> None:
    state = state_for(WorkflowStatus.FAILED)

    with pytest.raises(SafeError) as caught:
        WorkflowPolicy().transition(state, WorkflowStatus.SCHEDULED, Actor.OWNER)

    assert caught.value.code is SafeErrorCode.DELIVERY_UNKNOWN
    proven = replace(state, delivery_confirmed_not_sent=True)
    assert (
        WorkflowPolicy().transition(proven, WorkflowStatus.SCHEDULED, Actor.OWNER).status
        is WorkflowStatus.SCHEDULED
    )

