from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from bodrye_bot.operations.model_activation import ActivationGate
from bodrye_bot.ports.llm import (
    ChangeAssessment,
    ChangeResponse,
    ClaimClassification,
    ClaimsResponse,
    ClaimVerdict,
    DraftResponse,
    EvidenceResponse,
    UsageReport,
)
from evals.report import (
    DatasetValidationError,
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvalReport,
    load_dataset,
)
from evals.run import FakeEvalProvider, LLMProviderEvalAdapter, main, run_eval

DATASET_PATH = Path("evals/dataset.jsonl")


def _result(
    fixture_id: str,
    *,
    schema_valid: bool = True,
    violations: tuple[str, ...] = (),
) -> EvalCaseResult:
    return EvalCaseResult(
        fixture_id=fixture_id,
        category="calibration",
        blind_label=f"blind-{fixture_id}",
        schema_valid=schema_valid,
        blind_rating=4.5,
        style_rating=4.5,
        hard_violations=violations,
        latency_ms=12,
        input_tokens=None,
        output_tokens=None,
    )


def _report(
    *,
    model_score: float | None = 4.5,
    style_score: float | None = 4.5,
    safety_score: float | None = 1.0,
    results: tuple[EvalCaseResult, ...] | None = None,
    dataset_version: str = "dataset-v1",
    dataset_hash: str = "a" * 64,
) -> EvalReport:
    return EvalReport(
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        provider="fake",
        model="fake-model-v1",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        model_score=model_score,
        style_score=style_score,
        safety_score=safety_score,
        results=results or (_result("case-1"),),
    )


def _gate() -> ActivationGate:
    return ActivationGate(
        expected_dataset_version="dataset-v1",
        expected_dataset_hash="a" * 64,
        required_fixture_ids=frozenset({"case-1"}),
    )


def test_canonical_dataset_has_strict_versioned_required_coverage() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset.version == "bodrye-eval-v1"
    assert len(dataset.sha256) == 64
    assert len(dataset.calibration_topics) == 9
    assert len(dataset.style_holdout_ids) == 3
    assert dataset.coverage == frozenset(
        {
            "claim_supported",
            "claim_refuted",
            "claim_insufficient",
            "claim_manual_review",
            "trap_numeric",
            "trap_causal",
            "trap_association",
            "source_unavailable",
            "source_prompt_injection",
            "edit_number",
            "edit_modality",
            "edit_population",
            "edit_action",
            "length_short",
            "length_medium",
            "length_long",
        }
    )


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda rows: rows[0].update({"unexpected": True}), "extra fields"),
        (lambda rows: rows[1].update({"id": rows[0]["id"]}), "unique"),
        (lambda rows: rows.pop(), "style holdouts"),
        (lambda rows: rows[0].update({"expected_schema": "unknown"}), "expected schema"),
    ],
)
def test_dataset_validation_fails_closed(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    rows = [json.loads(line) for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()]
    assert callable(mutation)
    mutation(rows)
    path = tmp_path / "dataset.jsonl"
    serialized = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(serialized, encoding="utf-8")

    with pytest.raises(DatasetValidationError, match=message):
        load_dataset(path)


@pytest.mark.asyncio
async def test_fake_eval_report_is_deterministic_and_preserves_unknown_usage() -> None:
    dataset = load_dataset(DATASET_PATH)

    first = await run_eval(FakeEvalProvider(), dataset)
    second = await run_eval(FakeEvalProvider(), dataset)

    assert first.to_json() == second.to_json()
    assert {result.fixture_id for result in first.results} == set(dataset.fixture_ids)
    assert first.results[0].input_tokens is None
    assert '"input_tokens":null' in first.to_json()


class _TypedProvider:
    async def classify_claims(self, request: Any) -> ClaimsResponse:
        verdict = ClaimVerdict(request.claims[0])
        return ClaimsResponse(
            response_id="1" * 32,
            claims=(
                ClaimClassification(exact_text=request.claims[0], verdict=verdict, rationale="ok"),
            ),
        )

    async def synthesize_evidence(self, request: Any) -> EvidenceResponse:
        return EvidenceResponse(
            response_id="2" * 32,
            synthesis="Нужна ручная проверка.",
            verdict=ClaimVerdict.MANUAL_REVIEW,
        )

    async def assess_change(self, request: Any) -> ChangeResponse:
        return ChangeResponse(
            response_id="3" * 32,
            assessment=ChangeAssessment.SEMANTIC,
            reasons=("meaning changed",),
        )

    async def generate_draft(self, request: Any) -> DraftResponse:
        return DraftResponse(response_id="4" * 32, body="Тестовый текст", headlines=("Тест",))

    async def estimate_or_report_usage(self, response_id: str) -> UsageReport:
        return UsageReport(
            owner_id=0,
            workflow_id=None,
            operation="eval",
            provider="typed-fake",
            model="typed-model",
            status="succeeded",
            prompt_version="prompt-v1",
            schema_version="schema-v1",
            provider_request_id=None,
            latency_ms=17,
            input_tokens=None,
            output_tokens=9,
            error_class=None,
            trace_id=response_id,
        )


@pytest.mark.asyncio
async def test_llm_provider_adapter_evaluates_typed_outputs_and_usage() -> None:
    cases = (
        EvalCase(
            id="supported",
            category="calibration",
            input=MappingProxyType({"topic": "a", "text": "supported"}),
            expected_schema="claims-v1",
            hard_assertions=("claim_supported",),
            blind_label="B1",
        ),
        EvalCase(
            id="unavailable",
            category="calibration",
            input=MappingProxyType({"topic": "b", "text": "unavailable"}),
            expected_schema="evidence-v1",
            hard_assertions=("claim_manual_review", "source_unavailable"),
            blind_label="B2",
        ),
        EvalCase(
            id="edit",
            category="calibration",
            input=MappingProxyType({"topic": "c", "text": "edit"}),
            expected_schema="change-v1",
            hard_assertions=("edit_action",),
            blind_label="B3",
        ),
        EvalCase(
            id="style",
            category="style_holdout",
            input=MappingProxyType({"topic": "d", "text": "draft"}),
            expected_schema="draft-v1",
            hard_assertions=(),
            blind_label="B4",
        ),
    )
    dataset = EvalDataset(version="dataset-v1", sha256="a" * 64, cases=cases)
    adapter = LLMProviderEvalAdapter(
        provider=_TypedProvider(),  # type: ignore[arg-type]
        provider_name="typed-fake",
        model="typed-model",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        blind_ratings={case.blind_label: 4.5 for case in cases},
        style_ratings={"B4": 4.5},
    )

    report = await run_eval(adapter, dataset)

    assert all(not result.hard_violations for result in report.results)
    assert all(result.latency_ms == 17 for result in report.results)
    assert all(result.input_tokens is None for result in report.results)
    assert report.style_score == 4.5


def test_one_hard_safety_violation_blocks_activation() -> None:
    report = _report(results=(_result("case-1", violations=("trap_causal",)),))

    assert _gate().decide(report).reasons == ("hard_safety_failed",)


def test_one_invalid_required_schema_blocks_activation() -> None:
    report = _report(results=(_result("case-1", schema_valid=False),))

    assert _gate().decide(report).reasons == ("required_schema_failed",)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("model_score", "model_eval_missing"),
        ("style_score", "style_eval_missing"),
        ("safety_score", "safety_eval_missing"),
    ],
)
def test_missing_eval_section_blocks_with_stable_reason(field: str, reason: str) -> None:
    values = {"model_score": 4.5, "style_score": 4.5, "safety_score": 1.0}
    values[field] = None

    assert _gate().decide(_report(**values)).reasons == (reason,)


def test_gate_blocks_dataset_identity_fixture_coverage_and_low_style() -> None:
    mismatched = _report(
        dataset_version="other",
        dataset_hash="b" * 64,
        style_score=3.9,
        results=(_result("unexpected"),),
    )

    assert _gate().decide(mismatched).reasons == (
        "dataset_version_mismatch",
        "dataset_hash_mismatch",
        "fixture_coverage_incomplete",
        "style_score_unacceptable",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "https://user:secret@example.test"),
        ("model", "sk-secret-value"),
        ("dataset_hash", "not-a-sha256"),
    ],
)
def test_report_rejects_sensitive_or_malformed_audit_metadata(field: str, value: str) -> None:
    values = {
        "dataset_version": "dataset-v1",
        "dataset_hash": "a" * 64,
        "provider": "fake",
        "model": "fake-model-v1",
        "prompt_version": "prompt-v1",
        "schema_version": "schema-v1",
        "model_score": 4.5,
        "style_score": 4.5,
        "safety_score": 1.0,
        "results": (_result("case-1"),),
    }
    values[field] = value

    with pytest.raises(ValueError, match="safe report metadata"):
        EvalReport(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("model_score", float("nan"), "model_eval_invalid"),
        ("style_score", 5.1, "style_eval_invalid"),
        ("safety_score", -0.1, "safety_eval_invalid"),
    ],
)
def test_invalid_eval_metric_blocks_with_stable_reason(
    field: str,
    value: float,
    reason: str,
) -> None:
    values = {"model_score": 4.5, "style_score": 4.5, "safety_score": 1.0}
    values[field] = value

    assert _gate().decide(_report(**values)).reasons == (reason,)


def test_cli_hides_invalid_dataset_detail_behind_safe_russian_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not-json\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "evals.run",
            "--provider",
            "fake",
            "--dataset",
            str(invalid),
            "--output",
            str(tmp_path / "report.json"),
        ],
    )

    assert main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Файл сценариев проверки повреждён или неполон.\n"
    assert "JSON" not in output.err
    assert "Traceback" not in output.err
