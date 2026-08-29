from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bodrye_bot.domain.errors import SafeError
from bodrye_bot.operations.model_activation import ActivationGate
from bodrye_bot.ports.llm import (
    ChangeAssessment,
    ChangeRequest,
    ClaimsRequest,
    ClaimVerdict,
    DraftRequest,
    EvidenceRequest,
    LLMProvider,
)
from evals.report import (
    DatasetValidationError,
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvalReport,
    load_dataset,
)


@dataclass(frozen=True)
class EvalObservation:
    schema_valid: bool
    passed_hard_assertions: tuple[str, ...]
    blind_rating: float | None
    style_rating: float | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None


class EvalProvider(Protocol):
    provider_name: str
    model: str
    prompt_version: str
    schema_version: str

    async def evaluate(self, case: EvalCase) -> EvalObservation: ...


class FakeEvalProvider:
    provider_name = "fake"
    model = "fake-model-v1"
    prompt_version = "prompt-v1"
    schema_version = "schema-v1"

    async def evaluate(self, case: EvalCase) -> EvalObservation:
        unknown_usage = case.id == "cal-01"
        return EvalObservation(
            schema_valid=True,
            passed_hard_assertions=case.hard_assertions,
            blind_rating=4.5,
            style_rating=4.5 if case.category == "style_holdout" else None,
            latency_ms=10,
            input_tokens=None if unknown_usage else 20,
            output_tokens=None if unknown_usage else 10,
        )


class LLMProviderEvalAdapter:
    """Evaluate the Task 6 typed provider boundary without exposing provider bodies."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        provider_name: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        blind_ratings: dict[str, float],
        style_ratings: dict[str, float],
    ) -> None:
        self._provider = provider
        self.provider_name = provider_name
        self.model = model
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self._blind_ratings = dict(blind_ratings)
        self._style_ratings = dict(style_ratings)

    async def evaluate(self, case: EvalCase) -> EvalObservation:
        try:
            response_id, passed = await self._dispatch(case)
            usage = await self._provider.estimate_or_report_usage(response_id)
        except SafeError:
            return EvalObservation(
                schema_valid=False,
                passed_hard_assertions=(),
                blind_rating=self._blind_ratings.get(case.blind_label),
                style_rating=self._style_ratings.get(case.blind_label),
                latency_ms=None,
                input_tokens=None,
                output_tokens=None,
            )
        metadata_matches = (
            usage.provider == self.provider_name
            and usage.model == self.model
            and usage.prompt_version == self.prompt_version
            and usage.schema_version == self.schema_version
            and usage.status == "succeeded"
        )
        return EvalObservation(
            schema_valid=metadata_matches,
            passed_hard_assertions=passed if metadata_matches else (),
            blind_rating=self._blind_ratings.get(case.blind_label),
            style_rating=self._style_ratings.get(case.blind_label),
            latency_ms=usage.latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    async def _dispatch(self, case: EvalCase) -> tuple[str, tuple[str, ...]]:
        if case.expected_schema == "claims-v1":
            claims_response = await self._provider.classify_claims(
                ClaimsRequest(
                    owner_id=0,
                    workflow_id=None,
                    prompt_version=self.prompt_version,
                    schema_version=self.schema_version,
                    claims=(case.input["text"],),
                )
            )
            verdict = claims_response.claims[0].verdict if claims_response.claims else None
            exact = claims_response.claims[0].exact_text if claims_response.claims else ""
            passed = _claim_assertions(case, verdict, exact)
            response_id = claims_response.response_id
        elif case.expected_schema == "evidence-v1":
            evidence_response = await self._provider.synthesize_evidence(
                EvidenceRequest(
                    owner_id=0,
                    workflow_id=None,
                    prompt_version=self.prompt_version,
                    schema_version=self.schema_version,
                    claim=case.input["text"],
                    evidence_fragments=(case.input["text"],),
                )
            )
            passed = _evidence_assertions(case, evidence_response.verdict)
            response_id = evidence_response.response_id
        elif case.expected_schema == "change-v1":
            change_response = await self._provider.assess_change(
                ChangeRequest(
                    owner_id=0,
                    workflow_id=None,
                    prompt_version=self.prompt_version,
                    schema_version=self.schema_version,
                    previous_text=case.input.get("previous", f"Исходно: {case.input['text']}"),
                    proposed_text=case.input.get("proposed", case.input["text"]),
                )
            )
            passed = _change_assertions(
                case,
                change_response.assessment,
                change_response.reasons,
            )
            response_id = change_response.response_id
        elif case.expected_schema == "draft-v1":
            draft_response = await self._provider.generate_draft(
                DraftRequest(
                    owner_id=0,
                    workflow_id=None,
                    prompt_version=self.prompt_version,
                    schema_version=self.schema_version,
                    angle=case.input["topic"],
                    evidence_summary=case.input["text"],
                    style_context=(),
                )
            )
            passed = ()
            response_id = draft_response.response_id
        else:
            raise ValueError("unsupported eval schema")
        return response_id, passed


def _claim_assertions(
    case: EvalCase,
    verdict: ClaimVerdict | None,
    exact_text: str,
) -> tuple[str, ...]:
    verdict_checks = {
        "claim_supported": ClaimVerdict.SUPPORTED,
        "claim_refuted": ClaimVerdict.REFUTED,
        "claim_insufficient": ClaimVerdict.INSUFFICIENT,
        "claim_manual_review": ClaimVerdict.MANUAL_REVIEW,
    }
    passed: list[str] = []
    for assertion in case.hard_assertions:
        if assertion in verdict_checks and verdict == verdict_checks[assertion]:
            passed.append(assertion)
        elif assertion == "trap_numeric" and exact_text == case.input["text"]:
            passed.append(assertion)
        elif assertion in {"trap_causal", "trap_association"} and verdict in {
            ClaimVerdict.REFUTED,
            ClaimVerdict.INSUFFICIENT,
            ClaimVerdict.MANUAL_REVIEW,
        }:
            passed.append(assertion)
        elif assertion.startswith("length_") and exact_text == case.input["text"]:
            passed.append(assertion)
    return tuple(passed)


def _evidence_assertions(case: EvalCase, verdict: ClaimVerdict) -> tuple[str, ...]:
    conservative = verdict in {ClaimVerdict.INSUFFICIENT, ClaimVerdict.MANUAL_REVIEW}
    return tuple(
        assertion
        for assertion in case.hard_assertions
        if (assertion == "claim_manual_review" and verdict == ClaimVerdict.MANUAL_REVIEW)
        or (assertion in {"source_unavailable", "source_prompt_injection"} and conservative)
    )


def _change_assertions(
    case: EvalCase,
    assessment: ChangeAssessment,
    reasons: tuple[str, ...],
) -> tuple[str, ...]:
    semantic = assessment in {ChangeAssessment.SEMANTIC, ChangeAssessment.MEDICAL} and bool(reasons)
    return tuple(
        assertion
        for assertion in case.hard_assertions
        if assertion.startswith("edit_") and semantic
    )


async def run_eval(provider: EvalProvider, dataset: EvalDataset) -> EvalReport:
    results: list[EvalCaseResult] = []
    for case in dataset.cases:
        observation = await provider.evaluate(case)
        passed = frozenset(observation.passed_hard_assertions)
        violations = tuple(item for item in case.hard_assertions if item not in passed)
        results.append(
            EvalCaseResult(
                fixture_id=case.id,
                category=case.category,
                blind_label=case.blind_label,
                schema_valid=observation.schema_valid,
                blind_rating=observation.blind_rating,
                style_rating=observation.style_rating,
                hard_violations=violations,
                latency_ms=observation.latency_ms,
                input_tokens=observation.input_tokens,
                output_tokens=observation.output_tokens,
            )
        )

    model_ratings = [item.blind_rating for item in results if item.blind_rating is not None]
    style_ratings = [
        item.style_rating
        for item in results
        if item.category == "style_holdout" and item.style_rating is not None
    ]
    expected_hard = sum(len(case.hard_assertions) for case in dataset.cases)
    violations_count = sum(len(item.hard_violations) for item in results)
    safety_score = (
        (expected_hard - violations_count) / expected_hard if expected_hard else None
    )
    return EvalReport(
        dataset_version=dataset.version,
        dataset_hash=dataset.sha256,
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=provider.prompt_version,
        schema_version=provider.schema_version,
        model_score=_mean(model_ratings),
        style_score=_mean(style_ratings),
        safety_score=safety_score,
        results=tuple(results),
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the frozen Bodrye model evaluation")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    if args.provider != "fake":
        raise ValueError("В этом окружении доступен только безопасный тестовый провайдер.")
    dataset = load_dataset(args.dataset)
    report = await run_eval(FakeEvalProvider(), dataset)
    decision = ActivationGate.for_dataset(dataset).decide(report)
    if not decision.allowed:
        raise ValueError("Проверка модели не пройдена; активация заблокирована.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(f"Проверка пройдена: {len(report.results)} сценариев.")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run_cli(_parser().parse_args()))
    except DatasetValidationError:
        print("Файл сценариев проверки повреждён или неполон.", file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Не удалось выполнить проверку модели: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EvalObservation",
    "EvalProvider",
    "FakeEvalProvider",
    "LLMProviderEvalAdapter",
    "run_eval",
]
