from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from bodrye_bot.operations.model_activation import (
    ActivationGate,
    ActivationRegistry,
    EvaluatedModel,
)
from evals.report import EvalCaseResult, EvalReport


def _report(
    model: str,
    *,
    violations: tuple[str, ...] = (),
) -> EvalReport:
    return EvalReport(
        dataset_version="dataset-v1",
        dataset_hash="a" * 64,
        provider="fake",
        model=model,
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        model_score=4.5,
        style_score=4.5,
        safety_score=1.0 if not violations else 0.0,
        results=(
            EvalCaseResult(
                fixture_id="case-1",
                category="calibration",
                blind_label="blind-a",
                schema_valid=True,
                blind_rating=4.5,
                style_rating=4.5,
                hard_violations=violations,
                latency_ms=10,
                input_tokens=12,
                output_tokens=8,
            ),
        ),
    )


def _registry() -> ActivationRegistry:
    return ActivationRegistry(
        ActivationGate(
            expected_dataset_version="dataset-v1",
            expected_dataset_hash="a" * 64,
            required_fixture_ids=frozenset({"case-1"}),
        )
    )


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
