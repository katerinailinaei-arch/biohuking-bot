from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from bodrye_bot.operations.model_activation import (
    ActivationGate,
    ActivationRegistry,
    EvaluatedModel,
)
from evals.report import EvalCase, EvalCaseResult, EvalDataset, EvalReport


def _report(
    model: str,
    *,
    violations: tuple[str, ...] = (),
    rating: float = 4.5,
) -> EvalReport:
    return EvalReport(
        dataset_version="dataset-v1",
        dataset_hash="a" * 64,
        provider="fake",
        model=model,
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        model_score=rating,
        style_score=rating,
        safety_score=1.0 if not violations else 0.0,
        results=(
            EvalCaseResult(
                fixture_id="case-1",
                category="style_holdout",
                blind_label="blind-a",
                schema_valid=True,
                blind_rating=rating,
                style_rating=rating,
                hard_violations=violations,
                latency_ms=10,
                input_tokens=12,
                output_tokens=8,
            ),
        ),
    )


def _registry() -> ActivationRegistry:
    expected_dataset = EvalDataset(
        version="dataset-v1",
        sha256="a" * 64,
        cases=(
            EvalCase(
                id="case-1",
                category="style_holdout",
                input=MappingProxyType({"topic": "gate", "text": "gate"}),
                expected_schema="draft-v1",
                hard_assertions=(),
                blind_label="blind-a",
            ),
        ),
    )
    return ActivationRegistry(ActivationGate(expected_dataset=expected_dataset))


def test_activation_records_only_exact_evaluated_tuple_in_immutable_audit() -> None:
    registry = _registry()
    at = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)

    audit = registry.activate(_report("candidate-a"), actor="owner:42", at=at)

    assert audit.allowed is True
    assert audit.evaluated == EvaluatedModel(
        provider="fake",
        model="candidate-a",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        dataset_hash="a" * 64,
    )
    assert registry.active == audit.evaluated
    assert audit.report_hash == _report("candidate-a").sha256
    assert audit.at == at
    with pytest.raises(FrozenInstanceError):
        audit.actor = "attacker"  # type: ignore[misc]


def test_failed_candidate_never_replaces_active_model() -> None:
    registry = _registry()
    at = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    registry.activate(_report("safe"), actor="owner:42", at=at)

    failed = registry.activate(
        _report("unsafe", violations=("source_prompt_injection",)),
        actor="owner:42",
        at=at + timedelta(minutes=1),
    )

    assert failed.allowed is False
    assert registry.active is not None
    assert registry.active.model == "safe"
    assert tuple(event.allowed for event in registry.audit_log) == (True, False)


def test_rollback_reselects_previous_validated_tuple_and_appends_audit() -> None:
    registry = _registry()
    at = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    first = registry.activate(_report("candidate-a"), actor="owner:42", at=at)
    registry.activate(
        _report("candidate-b"),
        actor="owner:42",
        at=at + timedelta(minutes=1),
    )

    rollback = registry.rollback(actor="owner:42", at=at + timedelta(minutes=2))

    assert rollback.action == "rollback"
    assert rollback.evaluated == first.evaluated
    assert registry.active == first.evaluated
    assert len(registry.audit_log) == 3
    assert isinstance(registry.audit_log, tuple)


def test_repeated_tuple_rollback_preserves_each_activation_report_hash() -> None:
    registry = _registry()
    at = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    first_a = registry.activate(_report("candidate-a", rating=4.5), actor="owner:42", at=at)
    candidate_b = registry.activate(
        _report("candidate-b", rating=4.6),
        actor="owner:42",
        at=at + timedelta(minutes=1),
    )
    second_a = registry.activate(
        _report("candidate-a", rating=4.7),
        actor="owner:42",
        at=at + timedelta(minutes=2),
    )
    assert first_a.report_hash != second_a.report_hash

    rollback_b = registry.rollback(actor="owner:42", at=at + timedelta(minutes=3))
    rollback_first_a = registry.rollback(actor="owner:42", at=at + timedelta(minutes=4))

    assert rollback_b.evaluated.model == "candidate-b"
    assert rollback_b.report_hash == candidate_b.report_hash
    assert rollback_first_a.evaluated.model == "candidate-a"
    assert rollback_first_a.report_hash == first_a.report_hash
    assert len(registry.audit_log) == 5


def test_activation_audit_contains_metadata_but_no_content_or_secrets() -> None:
    registry = _registry()
    audit = registry.activate(
        _report("candidate-a"),
        actor="owner:42",
        at=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
    )

    serialized = repr(audit)
    assert "prompt-v1" in serialized
    assert "source" not in serialized.casefold()
    assert "secret" not in serialized.casefold()
