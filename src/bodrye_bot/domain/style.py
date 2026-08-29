from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from statistics import median
from uuid import UUID


class RuleScope(StrEnum):
    HARD = "hard"
    FORMAT = "format"


class RuleStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class StyleProfile:
    id: UUID
    owner_id: int
    version: int


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", RuleScope(self.scope))
        object.__setattr__(self, "status", RuleStatus(self.status))


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
    rule_text: str
    pattern_key: str
    confirmed: bool
    explicit_remember: bool = False
