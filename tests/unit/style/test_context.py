from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from bodrye_bot.domain.style import (
    AngleBrief,
    RuleScope,
    RuleStatus,
    StyleExample,
    StyleProfile,
    StyleRule,
)
from bodrye_bot.style.context import StyleContextBuilder


@dataclass
class InMemoryStyleContextRepository:
    profile: StyleProfile
    rules: tuple[StyleRule, ...]
    examples: tuple[StyleExample, ...]
    requested_owner_ids: list[int]

    def get_profile(self, *, owner_id: int, profile_id: UUID) -> StyleProfile:
        self.requested_owner_ids.append(owner_id)
        assert profile_id == self.profile.id
        assert owner_id == self.profile.owner_id
        return self.profile

    def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleRule, ...]:
        assert owner_id == self.profile.owner_id
        assert profile_id == self.profile.id
        return self.rules

    def approved_examples(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleExample, ...]:
        assert owner_id == self.profile.owner_id
        assert profile_id == self.profile.id
        return self.examples


def _rule(*, scope: RuleScope, text: str) -> StyleRule:
    return StyleRule(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        scope=scope,
        text=text,
        origin="owner_confirmation",
        status=RuleStatus.ACTIVE,
        confirmed_at=datetime.now(UTC),
    )


PROFILE_ID = uuid4()


def _example(*, text: str, tags: tuple[str, ...], rating: int) -> StyleExample:
    return StyleExample(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        text=text,
        rubric="energy",
        format="post",
        tags=tags,
        rating=rating,
    )


def test_context_is_owner_scoped_deterministic_and_excludes_chat_history() -> None:
    repository = InMemoryStyleContextRepository(
        profile=StyleProfile(id=PROFILE_ID, owner_id=42, version=1),
        rules=(
            _rule(scope=RuleScope.HARD, text="Не обещать лечение."),
            _rule(scope=RuleScope.FORMAT, text="Короткий абзац."),
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

    context = builder.build(
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
