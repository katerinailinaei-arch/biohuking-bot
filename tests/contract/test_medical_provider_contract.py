from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from bodrye_bot.domain.medical import ClaimType, RiskLevel
from bodrye_bot.ports.llm import (
    ClaimClassification,
    ClaimsResponse,
    ClaimVerdict,
    EvidenceResponse,
)


def _classification() -> ClaimClassification:
    return ClaimClassification(
        claim_id=uuid4(),
        exact_text="Ходьба может поддерживать здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Может поддерживать",
        numeric_value=None,
        modality="Может",
        medical_uncertainty=False,
        risk=RiskLevel.GREEN,
        verdict=ClaimVerdict.SUPPORTED,
        rationale="Формулировка ограничена данными источника.",
    )


def test_claims_response_requires_identity_and_complete_medical_semantics() -> None:
    classification = _classification()
    payload = classification.model_dump()
    payload.pop("population")

    with pytest.raises(ValidationError):
        ClaimClassification.model_validate(payload)

    response = ClaimsResponse(response_id="1" * 32, claims=(classification,))
    assert response.claims[0].claim_id == classification.claim_id
    assert response.claims[0].risk is RiskLevel.GREEN


def test_evidence_response_requires_claim_source_identity_and_review_fields() -> None:
    classification = _classification()
    response = EvidenceResponse(
        response_id="2" * 32,
        claim_id=classification.claim_id,
        source_document_id=uuid4(),
        exact_text=classification.exact_text,
        claim_type=classification.claim_type,
        population=classification.population,
        context=classification.context,
        causality=classification.causality,
        numeric_value=classification.numeric_value,
        modality=classification.modality,
        medical_uncertainty=False,
        applicability="Взрослые старше 35 лет",
        limitations="Наблюдательные данные.",
        risk=RiskLevel.GREEN,
        synthesis="Источник поддерживает ограниченную формулировку.",
        verdict=ClaimVerdict.SUPPORTED,
    )

    assert response.source_document_id
    assert response.applicability
    assert response.limitations


def test_medical_provider_collections_are_strictly_immutable() -> None:
    with pytest.raises(ValidationError):
        ClaimsResponse(response_id="1" * 32, claims=[_classification()])  # type: ignore[arg-type]
