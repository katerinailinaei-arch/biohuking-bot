import pytest
from hypothesis import given
from hypothesis import strategies as st

from bodrye_bot.domain.errors import SafeError
from bodrye_bot.domain.workflow import (
    ALLOWED_TRANSITIONS,
    Actor,
    WorkflowPolicy,
    WorkflowState,
    WorkflowStatus,
)


def workflow_state(status: WorkflowStatus) -> WorkflowState:
    return WorkflowState(
        status=status,
        current_version_id="v1",
        current_hash="h1",
        review_version_id="v1",
        review_hash="h1",
        approval_version_id="v1",
        approval_hash="h1",
        delivery_confirmed_not_sent=True,
    )


@given(st.sampled_from(list(WorkflowStatus)), st.sampled_from(list(WorkflowStatus)))
def test_forbidden_transition_never_mutates_status(
    source: WorkflowStatus, target: WorkflowStatus
) -> None:
    state = workflow_state(source)

    try:
        result = WorkflowPolicy().transition(state, target, Actor.OWNER)
    except SafeError:
        assert state.status is source
    else:
        assert (source, target) in ALLOWED_TRANSITIONS
        assert result.status is target
        assert state.status is source


@pytest.mark.parametrize(
    "source",
    [
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
    ],
)
def test_rejected_is_allowed_only_before_processing(source: WorkflowStatus) -> None:
    result = WorkflowPolicy().transition(
        workflow_state(source), WorkflowStatus.REJECTED, Actor.OWNER
    )

    assert result.status is WorkflowStatus.REJECTED


@pytest.mark.parametrize(
    "source",
    [
        WorkflowStatus.PROCESSING,
        WorkflowStatus.PUBLISHED,
        WorkflowStatus.FAILED,
        WorkflowStatus.DELIVERY_UNKNOWN,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.REJECTED,
    ],
)
def test_rejected_is_forbidden_after_processing(source: WorkflowStatus) -> None:
    with pytest.raises(SafeError):
        WorkflowPolicy().transition(
            workflow_state(source), WorkflowStatus.REJECTED, Actor.OWNER
        )
