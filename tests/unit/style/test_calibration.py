from __future__ import annotations

from uuid import uuid4

import pytest

from bodrye_bot.domain.errors import SafeError
from bodrye_bot.domain.style import (
    CalibrationTopic,
    HoldoutPost,
    HoldoutResult,
    StyleGate,
)
from bodrye_bot.style.calibration import CalibrationService


def _topics(*, count: int = 8) -> tuple[CalibrationTopic, ...]:
    return tuple(
        CalibrationTopic(
            id=f"topic-{index}",
            risk="low" if index % 2 else "high",
            short_variants=("first", "second", "third"),
        )
        for index in range(count)
    )


def test_calibration_accepts_only_risk_diverse_eight_to_ten_topics_with_three_variants() -> None:
    service = CalibrationService()

    calibration = service.start(_topics())

    assert calibration.topics == _topics()
    with pytest.raises(SafeError):
        service.start(_topics(count=7))
    with pytest.raises(SafeError):
        service.start(
            (
                CalibrationTopic(
                    id="bad", risk="low", short_variants=("first", "second")
                ),
            )
            + _topics(count=7)
        )


def test_calibration_records_only_explicit_feedback_as_proposed_rule_inputs() -> None:
    service = CalibrationService()
    calibration = service.start(_topics())

    feedback = service.record_feedback(
        calibration,
        topic_id="topic-0",
        selected_variant=1,
        rejected_variants=(0, 2),
        edit="Сделай теплее и короче.",
    )

    assert feedback.selected_variant == 1
    assert feedback.rejected_variants == (0, 2)
    assert feedback.edit == "Сделай теплее и короче."
    assert feedback.creates_active_rule is False


def test_calibration_rejects_selected_or_duplicate_rejected_variants() -> None:
    service = CalibrationService()
    calibration = service.start(_topics())

    with pytest.raises(SafeError):
        service.record_feedback(
            calibration,
            topic_id="topic-0",
            selected_variant=1,
            rejected_variants=(1,),
            edit=None,
        )
    with pytest.raises(SafeError):
        service.record_feedback(
            calibration,
            topic_id="topic-0",
            selected_variant=None,
            rejected_variants=(0, 0),
            edit=None,
        )


def test_calibration_rejects_empty_feedback_with_safe_russian_error() -> None:
    service = CalibrationService()

    with pytest.raises(SafeError) as caught:
        service.record_feedback(
            service.start(_topics()),
            topic_id="topic-0",
            selected_variant=None,
            rejected_variants=(),
            edit="  ",
        )

    assert caught.value.code.value == "invalid_transition"
    assert caught.value.trace_id
    assert "Это действие сейчас недоступно" in caught.value.user_message


def test_holdouts_are_exactly_three_unique_unseen_full_posts() -> None:
    service = CalibrationService()
    calibration = service.start(_topics())
    holdouts = (
        HoldoutPost(id=uuid4(), topic_id="holdout-a", body="Полный пост A"),
        HoldoutPost(id=uuid4(), topic_id="holdout-b", body="Полный пост B"),
        HoldoutPost(id=uuid4(), topic_id="holdout-c", body="Полный пост C"),
    )

    service.register_holdouts(calibration, holdouts)

    with pytest.raises(SafeError):
        service.register_holdouts(
            calibration,
            (
                HoldoutPost(id=uuid4(), topic_id="topic-0", body="Полный пост"),
                holdouts[1],
                holdouts[2],
            ),
        )
    with pytest.raises(SafeError):
        service.register_holdouts(calibration, holdouts[:2])


def test_style_gate_requires_zero_hard_violations_two_holdouts_and_median_four() -> None:
    result = (
        HoldoutResult("a", rating=5, accepted_without_rewrite=True),
        HoldoutResult("b", rating=4, accepted_without_rewrite=True),
        HoldoutResult("c", rating=2, accepted_without_rewrite=False),
    )

    decision = StyleGate().evaluate(result)

    assert decision.passed is True
    assert decision.median_rating == 4
    assert decision.accepted_without_rewrite == 2
    rejected = StyleGate().evaluate(
        (
            result[0],
            result[1],
            HoldoutResult(
                "c", rating=5, accepted_without_rewrite=True, hard_rule_violations=1
            ),
        )
    )
    assert rejected.passed is False
    assert rejected.reason == "hard_rule_violations"


@pytest.mark.parametrize(
    ("rating", "violations"),
    ((0, 0), (6, 0), (4, -1)),
)
def test_holdout_result_rejects_values_outside_the_gate_domain(
    rating: int, violations: int
) -> None:
    with pytest.raises(SafeError):
        HoldoutResult(
            "invalid",
            rating=rating,
            accepted_without_rewrite=True,
            hard_rule_violations=violations,
        )


@pytest.mark.parametrize("rating, violations", ((True, 0), (4.0, 0), (4, False)))
def test_holdout_result_rejects_bool_and_float_numeric_values(
    rating: object, violations: object
) -> None:
    with pytest.raises(SafeError):
        HoldoutResult(  # type: ignore[arg-type]
            "invalid",
            rating=rating,
            accepted_without_rewrite=True,
            hard_rule_violations=violations,
        )
