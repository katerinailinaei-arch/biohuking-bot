from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import (
    AngleBrief,
    RuleScope,
    RuleStatus,
    StyleContext,
    StyleExample,
    StyleProfile,
    StyleProfileStatus,
    StyleRule,
)

_SHA256_LOWERHEX = re.compile(r"^[0-9a-f]{64}$")


class StyleContextRepository(Protocol):
    async def get_profile(self, *, owner_id: int, profile_id: UUID) -> StyleProfile: ...

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleRule, ...]: ...

    async def approved_examples(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleExample, ...]: ...


class StyleContextBuilder:
    def __init__(self, *, owner_id: int, repository: StyleContextRepository) -> None:
        self._owner_id = owner_id
        self._repository = repository

    async def build(
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
        profile = await self._repository.get_profile(
            owner_id=self._owner_id, profile_id=profile_id
        )
        if profile.owner_id != self._owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        if profile.id != profile_id:
            raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
        if (
            profile.status is not StyleProfileStatus.ACTIVE
            or profile.activated_at is None
            or profile.calibration_report_id is None
            or profile.calibration_report_hash is None
            or _SHA256_LOWERHEX.fullmatch(profile.calibration_report_hash) is None
        ):
            raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
        active_rules = await self._repository.active_rules(
            owner_id=self._owner_id, profile_id=profile_id
        )
        examples = await self._repository.approved_examples(
            owner_id=self._owner_id, profile_id=profile_id
        )
        _validate_records(
            active_rules,
            examples,
            owner_id=self._owner_id,
            profile_id=profile_id,
        )
        matching = tuple(
            example
            for example in examples
            if example.rubric == rubric
            and example.format == format
            and set(tags).intersection(example.tags)
            and risk in example.risks
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
            raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
        negative_examples = tuple(
            sorted(
                (
                    example
                    for example in matching
                    if example.rating is not None and example.rating <= 3
                ),
                key=lambda example: example.text,
            )[:5]
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
                        and (not rule.risks or risk in rule.risks)
                        and (not rule.tags or bool(set(tags).intersection(rule.tags)))
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
                        and rule.format == format
                        and (not rule.risks or risk in rule.risks)
                        and (not rule.tags or bool(set(tags).intersection(rule.tags)))
                    ),
                    key=lambda rule: rule.text,
                )
            ),
            positive_examples=positive_examples,
            negative_examples=negative_examples,
            selected_angle=selected_angle,
            medical_constraints=medical_constraints,
        )


def _validate_records(
    rules: tuple[StyleRule, ...],
    examples: tuple[StyleExample, ...],
    *,
    owner_id: int,
    profile_id: UUID,
) -> None:
    for record in rules:
        if record.owner_id != owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        if record.profile_id != profile_id:
            raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
    for example in examples:
        if example.owner_id != owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        if example.profile_id != profile_id:
            raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
