from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from statistics import median
from uuid import UUID

from bodrye_bot.domain.errors import SafeError, SafeErrorCode


class RuleScope(StrEnum):
    HARD = "hard"
    FORMAT = "format"


class RuleStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class StyleProfileStatus(StrEnum):
    CALIBRATING = "calibrating"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


_PATTERN_PART = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_PATTERN_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def normalize_pattern_key(value: str) -> str:
    """Normalize the explicit category:slug key used for edit similarity."""
    parts = value.strip().lower().split(":")
    if len(parts) != 2:
        raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
    category, slug = (
        re.sub(r"-+", "-", re.sub(r"[_\s]+", "-", part)).strip("-")
        for part in parts
    )
    if not _PATTERN_PART.fullmatch(category) or not _PATTERN_SLUG.fullmatch(slug):
        raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
    return f"{category}:{slug}"


@dataclass(frozen=True)
class StyleProfile:
    id: UUID
    owner_id: int
    version: int
    status: StyleProfileStatus = StyleProfileStatus.CALIBRATING
    activated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", StyleProfileStatus(self.status))


@dataclass(frozen=True)
class StyleRule:
    id: UUID
    owner_id: int
    profile_id: UUID
    scope: RuleScope
    text: str
    origin: str
    status: RuleStatus
    pattern_key: str = ""
    confirmed_at: datetime | None = None
    format: str | None = None
    risks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", RuleScope(self.scope))
        object.__setattr__(self, "status", RuleStatus(self.status))
        if self.pattern_key:
            object.__setattr__(self, "pattern_key", normalize_pattern_key(self.pattern_key))


@dataclass(frozen=True)
class StyleExample:
    id: UUID
    owner_id: int
    profile_id: UUID
    text: str
    rubric: str
    format: str
    tags: tuple[str, ...]
    rating: int | None
    risks: tuple[str, ...] = ()


@dataclass(frozen=True)
class AngleBrief:
    id: str
    name: str


@dataclass(frozen=True)
class StyleContext:
    hard_rules: tuple[StyleRule, ...]
    format_rules: tuple[StyleRule, ...]
    positive_examples: tuple[StyleExample, ...]
    negative_examples: tuple[StyleExample, ...]
    selected_angle: AngleBrief
    medical_constraints: tuple[str, ...]


@dataclass(frozen=True)
class CalibrationTopic:
    id: str
    risk: str
    short_variants: tuple[str, ...]


@dataclass(frozen=True)
class CalibrationFeedback:
    topic_id: str
    selected_variant: int | None
    rejected_variants: tuple[int, ...]
    edit: str | None
    creates_active_rule: bool = False


@dataclass(frozen=True)
class CalibrationSession:
    topics: tuple[CalibrationTopic, ...]


@dataclass(frozen=True)
class HoldoutPost:
    id: UUID
    topic_id: str
    body: str


@dataclass(frozen=True)
class HoldoutResult:
    topic_id: str
    rating: int
    accepted_without_rewrite: bool
    hard_rule_violations: int = 0

    def __post_init__(self) -> None:
        if type(self.rating) is not int or type(self.hard_rule_violations) is not int:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        if not 1 <= self.rating <= 5:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        if self.hard_rule_violations < 0:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)


@dataclass(frozen=True)
class StyleGateDecision:
    passed: bool
    median_rating: int | float
    accepted_without_rewrite: int
    hard_rule_violations: int
    reason: str | None


class StyleGate:
    def evaluate(
        self, results: tuple[HoldoutResult, ...]
    ) -> StyleGateDecision:
        if len(results) != 3 or len({result.topic_id for result in results}) != 3:
            return StyleGateDecision(False, 0, 0, 0, "three_unique_holdouts_required")
        violations = sum(result.hard_rule_violations for result in results)
        accepted = sum(result.accepted_without_rewrite for result in results)
        median_rating = median(result.rating for result in results)
        if violations:
            return StyleGateDecision(
                False, median_rating, accepted, violations, "hard_rule_violations"
            )
        if accepted < 2:
            return StyleGateDecision(
                False, median_rating, accepted, violations, "holdouts_not_accepted"
            )
        if median_rating < 4:
            return StyleGateDecision(
                False, median_rating, accepted, violations, "median_rating_too_low"
            )
        return StyleGateDecision(True, median_rating, accepted, violations, None)


@dataclass(frozen=True)
class EditObservation:
    profile_id: UUID
    source_edit_id: UUID
    rule_text: str
    pattern_key: str
    confirmed: bool
    explicit_remember: bool = False
    scope: RuleScope = RuleScope.FORMAT
    format: str | None = "post"
    risks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern_key", normalize_pattern_key(self.pattern_key))
        object.__setattr__(self, "scope", RuleScope(self.scope))
        if self.scope is RuleScope.FORMAT and not self.format:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
