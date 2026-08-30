from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import (
    AngleBrief,
    RuleScope,
    RuleStatus,
    StyleExample,
    StyleProfile,
    StyleProfileStatus,
    StyleRule,
)
from bodrye_bot.style.context import StyleContextBuilder


@dataclass
class InMemoryStyleContextRepository:
    profile: StyleProfile
    rules: tuple[StyleRule, ...]
    examples: tuple[StyleExample, ...]
    requested_owner_ids: list[int]

    async def get_profile(self, *, owner_id: int, profile_id: UUID) -> StyleProfile:
        self.requested_owner_ids.append(owner_id)
        assert profile_id == self.profile.id
        assert owner_id == self.profile.owner_id
        return self.profile

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleRule, ...]:
        assert owner_id == self.profile.owner_id
        assert profile_id == self.profile.id
        return self.rules

    async def approved_examples(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleExample, ...]:
        assert owner_id == self.profile.owner_id
        assert profile_id == self.profile.id
        return self.examples


def _rule(*, scope: RuleScope, text: str, format: str | None = None) -> StyleRule:
    return StyleRule(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        scope=scope,
        text=text,
        origin="owner_confirmation",
        status=RuleStatus.ACTIVE,
        confirmed_at=datetime.now(UTC),
        format=format,
        risks=("medium",),
        tags=("sleep", "energy"),
    )


PROFILE_ID = uuid4()
CALIBRATION_REPORT_ID = uuid4()
CALIBRATION_REPORT_HASH = "a" * 64


def _example(
    *, text: str, tags: tuple[str, ...], rating: int, risks: tuple[str, ...] = ("medium",)
) -> StyleExample:
    return StyleExample(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        text=text,
        rubric="energy",
        format="post",
        tags=tags,
        rating=rating,
        risks=risks,
    )


@pytest.mark.asyncio
async def test_context_is_owner_scoped_deterministic_and_excludes_chat_history() -> None:
    repository = InMemoryStyleContextRepository(
        profile=StyleProfile(
            id=PROFILE_ID,
            owner_id=42,
            version=1,
            status=StyleProfileStatus.ACTIVE,
            activated_at=datetime.now(UTC),
            calibration_report_id=CALIBRATION_REPORT_ID,
            calibration_report_hash=CALIBRATION_REPORT_HASH,
        ),
        rules=(
            _rule(scope=RuleScope.HARD, text="Не обещать лечение."),
            _rule(scope=RuleScope.FORMAT, text="Короткий абзац.", format="post"),
            _rule(scope=RuleScope.FORMAT, text="Чужой формат.", format="card"),
            StyleRule(
                id=uuid4(),
                owner_id=42,
                profile_id=PROFILE_ID,
                scope=RuleScope.HARD,
                text="Неподтверждённое правило.",
                origin="migration_error",
                status=RuleStatus.ACTIVE,
            ),
        ),
        examples=(
            _example(text="A", tags=("sleep",), rating=5),
            _example(text="B", tags=("sleep", "energy"), rating=4),
            _example(text="C", tags=("sleep",), rating=5),
            _example(text="D", tags=("sleep",), rating=4),
            _example(text="E", tags=("sleep",), rating=5),
            _example(text="not approved", tags=("sleep",), rating=3),
        ),
        requested_owner_ids=[],
    )
    builder = StyleContextBuilder(owner_id=42, repository=repository)

    context = await builder.build(
        profile_id=PROFILE_ID,
        rubric="energy",
        format="post",
        risk="medium",
        tags=("sleep", "energy"),
        selected_angle=AngleBrief(id="angle-1", name="Практичный шаг"),
        medical_constraints=("Не ставить диагноз.",),
    )

    assert repository.requested_owner_ids == [42]
    assert tuple(rule.text for rule in context.hard_rules) == ("Не обещать лечение.",)
    assert tuple(rule.text for rule in context.format_rules) == ("Короткий абзац.",)
    assert tuple(example.text for example in context.positive_examples) == (
        "B",
        "A",
        "C",
        "D",
        "E",
    )
    assert tuple(example.text for example in context.negative_examples) == ("not approved",)
    assert context.selected_angle.name == "Практичный шаг"
    assert context.medical_constraints == ("Не ставить диагноз.",)
    assert "history" not in context.__dataclass_fields__


@pytest.mark.asyncio
async def test_context_rejects_a_profile_that_is_not_active_with_safe_russian_error() -> None:
    repository = InMemoryStyleContextRepository(
        profile=StyleProfile(
            id=PROFILE_ID,
            owner_id=42,
            version=1,
            status=StyleProfileStatus.CALIBRATING,
        ),
        rules=(),
        examples=(
            _example(text="A", tags=("sleep",), rating=5),
            _example(text="B", tags=("sleep",), rating=5),
            _example(text="C", tags=("sleep",), rating=4),
        ),
        requested_owner_ids=[],
    )

    with pytest.raises(SafeError) as caught:
        await StyleContextBuilder(owner_id=42, repository=repository).build(
            profile_id=PROFILE_ID,
            rubric="energy",
            format="post",
            risk="medium",
            tags=("sleep",),
            selected_angle=AngleBrief(id="angle", name="Практично"),
            medical_constraints=(),
        )

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY
    assert "Профиль стиля пока не готов" in caught.value.user_message


@pytest.mark.asyncio
async def test_context_rejects_active_profile_without_report_binding() -> None:
    """Removing a persisted calibration binding must block style use."""
    repository = InMemoryStyleContextRepository(
        profile=StyleProfile(
            id=PROFILE_ID,
            owner_id=42,
            version=1,
            status=StyleProfileStatus.ACTIVE,
            activated_at=datetime.now(UTC),
        ),
        rules=(),
        examples=(
            _example(text="A", tags=("sleep",), rating=5),
            _example(text="B", tags=("sleep",), rating=5),
            _example(text="C", tags=("sleep",), rating=4),
        ),
        requested_owner_ids=[],
    )

    with pytest.raises(SafeError) as caught:
        await StyleContextBuilder(owner_id=42, repository=repository).build(
            profile_id=PROFILE_ID,
            rubric="energy",
            format="post",
            risk="medium",
            tags=("sleep",),
            selected_angle=AngleBrief(id="angle", name="Практично"),
            medical_constraints=(),
        )

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY


@pytest.mark.asyncio
async def test_context_fails_closed_on_cross_owner_or_cross_profile_repository_records() -> None:
    profile = StyleProfile(
        id=PROFILE_ID,
        owner_id=42,
        version=1,
        status=StyleProfileStatus.ACTIVE,
        activated_at=datetime.now(UTC),
        calibration_report_id=CALIBRATION_REPORT_ID,
        calibration_report_hash=CALIBRATION_REPORT_HASH,
    )
    contaminated_rule = StyleRule(
        id=uuid4(),
        owner_id=999,
        profile_id=PROFILE_ID,
        scope=RuleScope.HARD,
        text="Не раскрывать.",
        origin="repository",
        status=RuleStatus.ACTIVE,
        confirmed_at=datetime.now(UTC),
    )
    repository = InMemoryStyleContextRepository(
        profile=profile,
        rules=(contaminated_rule,),
        examples=(),
        requested_owner_ids=[],
    )

    with pytest.raises(SafeError) as caught:
        await StyleContextBuilder(owner_id=42, repository=repository).build(
            profile_id=PROFILE_ID,
            rubric="energy",
            format="post",
            risk="medium",
            tags=("sleep",),
            selected_angle=AngleBrief(id="angle", name="Практично"),
            medical_constraints=(),
        )

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_context_fails_closed_on_cross_profile_example_before_selection() -> None:
    profile = StyleProfile(
        id=PROFILE_ID,
        owner_id=42,
        version=1,
        status=StyleProfileStatus.ACTIVE,
        activated_at=datetime.now(UTC),
        calibration_report_id=CALIBRATION_REPORT_ID,
        calibration_report_hash=CALIBRATION_REPORT_HASH,
    )
    repository = InMemoryStyleContextRepository(
        profile=profile,
        rules=(),
        examples=(
            StyleExample(
                id=uuid4(),
                owner_id=42,
                profile_id=uuid4(),
                text="Чужой пример.",
                rubric="energy",
                format="post",
                tags=("sleep",),
                rating=5,
                risks=("medium",),
            ),
        ),
        requested_owner_ids=[],
    )

    with pytest.raises(SafeError) as caught:
        await StyleContextBuilder(owner_id=42, repository=repository).build(
            profile_id=PROFILE_ID,
            rubric="energy",
            format="post",
            risk="medium",
            tags=("sleep",),
            selected_angle=AngleBrief(id="angle", name="Практично"),
            medical_constraints=(),
        )

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY


@pytest.mark.asyncio
async def test_context_filters_format_and_risk_and_bounds_negative_examples() -> None:
    profile = StyleProfile(
        id=PROFILE_ID,
        owner_id=42,
        version=1,
        status=StyleProfileStatus.ACTIVE,
        activated_at=datetime.now(UTC),
        calibration_report_id=CALIBRATION_REPORT_ID,
        calibration_report_hash=CALIBRATION_REPORT_HASH,
    )
    negatives = tuple(
        _example(text=f"negative-{index}", tags=("sleep",), rating=3)
        for index in range(6)
    )
    repository = InMemoryStyleContextRepository(
        profile=profile,
        rules=(
            _rule(scope=RuleScope.HARD, text="Hard"),
            _rule(scope=RuleScope.FORMAT, text="Post", format="post"),
            _rule(scope=RuleScope.FORMAT, text="Card", format="card"),
        ),
        examples=(
            _example(text="positive-a", tags=("sleep",), rating=5),
            _example(text="positive-b", tags=("sleep",), rating=5),
            _example(text="positive-c", tags=("sleep",), rating=4),
            _example(text="high-risk", tags=("sleep",), rating=5, risks=("high",)),
        )
        + negatives,
        requested_owner_ids=[],
    )

    context = await StyleContextBuilder(owner_id=42, repository=repository).build(
        profile_id=PROFILE_ID,
        rubric="energy",
        format="post",
        risk="medium",
        tags=("sleep",),
        selected_angle=AngleBrief(id="angle", name="Практично"),
        medical_constraints=(),
    )

    assert tuple(rule.text for rule in context.format_rules) == ("Post",)
    assert tuple(example.text for example in context.positive_examples) == (
        "positive-a",
        "positive-b",
        "positive-c",
    )
    assert len(context.negative_examples) == 5
