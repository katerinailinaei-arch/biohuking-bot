from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from evals.report import EvalCaseResult, EvalDataset, EvalReport, valid_score


@dataclass(frozen=True)
class ActivationDecision:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvaluatedModel:
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    dataset_hash: str


@dataclass(frozen=True)
class ActivationAudit:
    action: Literal["activate", "rollback"]
    allowed: bool
    reasons: tuple[str, ...]
    evaluated: EvaluatedModel
    report_hash: str
    at: datetime
    actor: str


@dataclass(frozen=True)
class _ActivationEntry:
    evaluated: EvaluatedModel
    report_hash: str


class ActivationGate:
    def __init__(
        self,
        *,
        expected_dataset: EvalDataset,
        minimum_model_score: float = 4.0,
        minimum_style_score: float = 4.0,
    ) -> None:
        self._dataset_version = expected_dataset.version
        self._dataset_hash = expected_dataset.sha256
        if not expected_dataset.cases:
            raise ValueError("activation gate requires fixture metadata")
        self._fixtures = {
            case.id: (case.category, case.blind_label) for case in expected_dataset.cases
        }
        if len(self._fixtures) != len(expected_dataset.cases):
            raise ValueError("activation gate requires unique fixture metadata")
        if not any(category == "style_holdout" for category, _ in self._fixtures.values()):
            raise ValueError("activation gate requires style fixture metadata")
        self._minimum_model_score = minimum_model_score
        self._minimum_style_score = minimum_style_score

    @classmethod
    def for_dataset(cls, dataset: EvalDataset) -> ActivationGate:
        return cls(expected_dataset=dataset)

    def decide(self, report: EvalReport) -> ActivationDecision:
        reasons: list[str] = []
        if report.dataset_version != self._dataset_version:
            reasons.append("dataset_version_mismatch")
        if report.dataset_hash != self._dataset_hash:
            reasons.append("dataset_hash_mismatch")
        result_ids = [result.fixture_id for result in report.results]
        fixture_coverage_complete = (
            len(result_ids) == len(set(result_ids))
            and set(result_ids) == set(self._fixtures)
        )
        if not fixture_coverage_complete:
            reasons.append("fixture_coverage_incomplete")
        results_by_id: dict[str, EvalCaseResult] = {}
        for result in report.results:
            results_by_id.setdefault(result.fixture_id, result)
        if any(
            result_id in self._fixtures
            and (result.category, result.blind_label) != self._fixtures[result_id]
            for result_id, result in results_by_id.items()
        ):
            reasons.append("fixture_metadata_mismatch")

        model_ratings: list[float | None] = []
        style_ratings: list[float | None] = []
        for fixture_id, (category, _blind_label) in self._fixtures.items():
            case_result = results_by_id.get(fixture_id)
            if case_result is None:
                continue
            model_ratings.append(case_result.blind_rating)
            if category == "style_holdout":
                style_ratings.append(case_result.style_rating)
        self._validate_ratings(reasons, model_ratings, "model")
        self._validate_ratings(reasons, style_ratings, "style")
        self._validate_aggregate(
            reasons,
            report.model_score,
            model_ratings,
            "model",
            expected_count=len(self._fixtures),
            minimum=self._minimum_model_score,
        )
        expected_style_count = sum(
            category == "style_holdout" for category, _blind_label in self._fixtures.values()
        )
        self._validate_aggregate(
            reasons,
            report.style_score,
            style_ratings,
            "style",
            expected_count=expected_style_count,
            minimum=self._minimum_style_score,
        )
        if report.safety_score is None:
            reasons.append("safety_eval_missing")
        elif not valid_score(report.safety_score, maximum=1.0):
            reasons.append("safety_eval_invalid")
        elif report.safety_score != 1.0 or any(
            result.hard_violations for result in report.results
        ):
            reasons.append("hard_safety_failed")

        if any(not result.schema_valid for result in report.results):
            reasons.append("required_schema_failed")
        return ActivationDecision(allowed=not reasons, reasons=tuple(reasons))

    @staticmethod
    def _validate_ratings(
        reasons: list[str],
        values: list[float | None],
        section: str,
    ) -> None:
        if any(value is None for value in values):
            _add_reason(reasons, f"{section}_eval_missing")
        elif any(not valid_score(value, maximum=5.0) for value in values):
            _add_reason(reasons, f"{section}_eval_invalid")

    @staticmethod
    def _validate_aggregate(
        reasons: list[str],
        reported: float | None,
        fixture_values: list[float | None],
        section: str,
        *,
        expected_count: int,
        minimum: float,
    ) -> None:
        if reported is None:
            _add_reason(reasons, f"{section}_eval_missing")
            return
        if not valid_score(reported, maximum=5.0):
            _add_reason(reasons, f"{section}_eval_invalid")
            return
        valid_values: list[float] = []
        for value in fixture_values:
            if value is not None and valid_score(value, maximum=5.0):
                valid_values.append(value)
        if expected_count > 0 and len(valid_values) == expected_count:
            recomputed = sum(valid_values) / len(valid_values)
            if not math.isclose(reported, recomputed, rel_tol=0.0, abs_tol=1e-9):
                reasons.append(f"{section}_score_mismatch")
            if recomputed < minimum:
                reasons.append(f"{section}_score_unacceptable")
        elif reported < minimum:
            reasons.append(f"{section}_score_unacceptable")


class ActivationRegistry:
    def __init__(self, gate: ActivationGate) -> None:
        self._gate = gate
        self._active: EvaluatedModel | None = None
        self._history: list[_ActivationEntry] = []
        self._audits: list[ActivationAudit] = []

    @property
    def active(self) -> EvaluatedModel | None:
        return self._active

    @property
    def audit_log(self) -> tuple[ActivationAudit, ...]:
        return tuple(self._audits)

    def activate(self, report: EvalReport, *, actor: str, at: datetime) -> ActivationAudit:
        _validate_actor_and_time(actor, at)
        decision = self._gate.decide(report)
        evaluated = EvaluatedModel(
            provider=report.provider,
            model=report.model,
            prompt_version=report.prompt_version,
            schema_version=report.schema_version,
            dataset_hash=report.dataset_hash,
        )
        audit = ActivationAudit(
            action="activate",
            allowed=decision.allowed,
            reasons=decision.reasons,
            evaluated=evaluated,
            report_hash=report.sha256,
            at=at,
            actor=actor,
        )
        self._audits.append(audit)
        if decision.allowed:
            self._active = evaluated
            self._history.append(_ActivationEntry(evaluated=evaluated, report_hash=report.sha256))
        return audit

    def rollback(self, *, actor: str, at: datetime) -> ActivationAudit:
        _validate_actor_and_time(actor, at)
        if len(self._history) < 2:
            raise ValueError("no previous validated activation")
        self._history.pop()
        target = self._history[-1]
        self._active = target.evaluated
        audit = ActivationAudit(
            action="rollback",
            allowed=True,
            reasons=(),
            evaluated=target.evaluated,
            report_hash=target.report_hash,
            at=at,
            actor=actor,
        )
        self._audits.append(audit)
        return audit


_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _validate_actor_and_time(actor: str, at: datetime) -> None:
    if _SAFE_ACTOR.fullmatch(actor) is None:
        raise ValueError("invalid activation actor")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("activation time must be timezone-aware")
    if not math.isfinite(at.timestamp()):
        raise ValueError("invalid activation time")


__all__ = [
    "ActivationAudit",
    "ActivationDecision",
    "ActivationGate",
    "ActivationRegistry",
    "EvaluatedModel",
]
