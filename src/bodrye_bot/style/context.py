from __future__ import annotations

from typing import Protocol
from uuid import UUID

from bodrye_bot.domain.style import (
    AngleBrief,
    RuleScope,
    RuleStatus,
    StyleContext,
    StyleExample,
    StyleProfile,
    StyleRule,
)


class StyleContextRepository(Protocol):
    def get_profile(self, *, owner_id: int, profile_id: UUID) -> StyleProfile: ...

    def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleRule, ...]: ...

    def approved_examples(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleExample, ...]: ...


class StyleContextBuilder:
    def __init__(self, *, owner_id: int, repository: StyleContextRepository) -> None:
        self._owner_id = owner_id
        self._repository = repository

    def build(
        self,
        profile_id: UUID,
        rubric: str,
        format: str,
        risk: str,
        *,
        tags: tuple[str, ...],
        selected_angle: AngleBrief,
        medical_constraints: tuple[str, ...],
    ) -> StyleContext:
        profile = self._repository.get_profile(
            owner_id=self._owner_id, profile_id=profile_id
        )
        if profile.owner_id != self._owner_id:
            raise ValueError("Profile owner mismatch")
        active_rules = self._repository.active_rules(
            owner_id=self._owner_id, profile_id=profile_id
        )
        examples = self._repository.approved_examples(
            owner_id=self._owner_id, profile_id=profile_id
        )
        matching = tuple(
            example
            for example in examples
            if example.rubric == rubric
            and example.format == format
            and set(tags).intersection(example.tags)
        )
        positive_examples = tuple(
            sorted(
                (
                    example
                    for example in matching
                    if example.rating is not None and example.rating >= 4
                ),
                key=lambda example: (-len(set(tags).intersection(example.tags)), example.text),
            )[:5]
        )
        if len(positive_examples) < 3:
            raise ValueError("Style context requires 3 to 5 approved positive examples")
        negative_examples = tuple(
            sorted(
                (
                    example
                    for example in matching
                    if example.rating is not None and example.rating <= 3
                ),
                key=lambda example: example.text,
            )
        )
        return StyleContext(
            hard_rules=tuple(
                sorted(
                    (
                        rule
                        for rule in active_rules
                        if rule.status is RuleStatus.ACTIVE
                        and rule.scope is RuleScope.HARD
                        and rule.confirmed_at is not None
                    ),
                    key=lambda rule: rule.text,
                )
            ),
            format_rules=tuple(
                sorted(
                    (
                        rule
                        for rule in active_rules
                        if rule.status is RuleStatus.ACTIVE
                        and rule.scope is RuleScope.FORMAT
                        and rule.confirmed_at is not None
                    ),
                    key=lambda rule: rule.text,
                )
            ),
            positive_examples=positive_examples,
            negative_examples=negative_examples,
            selected_angle=selected_angle,
            medical_constraints=medical_constraints,
        )
