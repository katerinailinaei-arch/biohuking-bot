from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from evals.report import EvalDataset, EvalReport, valid_score


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


class ActivationGate:
    def __init__(
        self,
        *,
        expected_dataset_version: str,
        expected_dataset_hash: str,
        required_fixture_ids: frozenset[str],
        minimum_model_score: float = 4.0,
        minimum_style_score: float = 4.0,
    ) -> None:
        self._dataset_version = expected_dataset_version
        self._dataset_hash = expected_dataset_hash
        self._fixture_ids = required_fixture_ids
        self._minimum_model_score = minimum_model_score
        self._minimum_style_score = minimum_style_score

    @classmethod
    def for_dataset(cls, dataset: EvalDataset) -> ActivationGate:
        return cls(
            expected_dataset_version=dataset.version,
            expected_dataset_hash=dataset.sha256,
            required_fixture_ids=frozenset(dataset.fixture_ids),
        )

    def decide(self, report: EvalReport) -> ActivationDecision:
        reasons: list[str] = []
        if report.dataset_version != self._dataset_version:
            reasons.append("dataset_version_mismatch")
        if report.dataset_hash != self._dataset_hash:
            reasons.append("dataset_hash_mismatch")
        result_ids = [result.fixture_id for result in report.results]
        if len(result_ids) != len(set(result_ids)) or set(result_ids) != self._fixture_ids:
            reasons.append("fixture_coverage_incomplete")

        self._score_reason(
            reasons,
            report.model_score,
            "model",
            maximum=5.0,
            minimum=self._minimum_model_score,
        )
        self._score_reason(
            reasons,
            report.style_score,
            "style",
            maximum=5.0,
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
    def _score_reason(
        reasons: list[str],
        value: float | None,
        section: str,
        *,
        maximum: float,
        minimum: float,
    ) -> None:
        if value is None:
            reasons.append(f"{section}_eval_missing")
        elif not valid_score(value, maximum=maximum):
            reasons.append(f"{section}_eval_invalid")
        elif value < minimum:
            reasons.append(f"{section}_score_unacceptable")


class ActivationRegistry:
    def __init__(self, gate: ActivationGate) -> None:
        self._gate = gate
        self._active: EvaluatedModel | None = None
        self._validated: list[EvaluatedModel] = []
        self._report_hashes: dict[EvaluatedModel, str] = {}
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
            self._validated.append(evaluated)
            self._report_hashes[evaluated] = report.sha256
        return audit

    def rollback(self, *, actor: str, at: datetime) -> ActivationAudit:
        _validate_actor_and_time(actor, at)
        if len(self._validated) < 2:
            raise ValueError("no previous validated activation")
        self._validated.pop()
        target = self._validated[-1]
        self._active = target
        audit = ActivationAudit(
            action="rollback",
            allowed=True,
            reasons=(),
            evaluated=target,
            report_hash=self._report_hashes[target],
            at=at,
            actor=actor,
        )
        self._audits.append(audit)
        return audit


_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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
