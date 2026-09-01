from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimReview,
    ClaimType,
    ConfirmedExtraction,
    DraftBinding,
    Evidence,
    EvidenceSource,
    EvidenceVerdict,
    ReviewBlockingReason,
    RiskLevel,
)
from bodrye_bot.medical.policy import MedicalPolicy, MedicalReviewConfiguration

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
OWNER_ID = 42
WORKFLOW_ID = uuid4()
MODEL_RUN_ID = uuid4()
DRAFT_VERSION_ID = uuid4()
DRAFT_HASH = content_hash("Текущая версия черновика")


def claim_review(
    *,
    verdict: EvidenceVerdict = EvidenceVerdict.SUPPORTED,
    risk: RiskLevel = RiskLevel.GREEN,
    has_provenance: bool = True,
    exact_excerpt: str = "Регулярная ходьба связана с улучшением показателей здоровья.",
    source_url: str = "https://www.who.int/example",
    applicability: str = "Взрослые старше 35 лет",
    limitations: str = "Общая рекомендация, не индивидуальное назначение.",
) -> ClaimReview:
    claim_id = uuid4()
    source_document_id = uuid4()
    claim = AtomicClaim(
        id=claim_id,
        exact_text="Регулярная ходьба улучшает здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Улучшает",
        numeric_value=None,
        modality="Может",
        medical_uncertainty=False,
        source_document_ids=(source_document_id,),
    )
    evidence = ()
    if has_provenance:
        evidence = (
            Evidence(
                id=uuid4(),
                claim_id=claim_id,
                source_document_id=source_document_id,
                source_url=source_url,
                exact_excerpt=exact_excerpt,
                excerpt_hash=content_hash(exact_excerpt),
                applicability=applicability,
                limitations=limitations,
                verdict=verdict,
                risk=risk,
                reviewed_at=NOW,
                model_run_id=MODEL_RUN_ID,
                response_id="2" * 32,
            ),
        )
    return ClaimReview(
        id=uuid4(),
        owner_id=OWNER_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=7,
        extraction_hash=content_hash("confirmed extraction v7"),
        draft_version_id=DRAFT_VERSION_ID,
        draft_hash=DRAFT_HASH,
        policy_version="medical-v1:ttl=86400",
        validity_seconds=86_400,
        classification_run_id=MODEL_RUN_ID,
        classification_response_id="1" * 32,
        reviewed_at=NOW,
        claims=(claim,),
        evidence=evidence,
    )


def policy() -> MedicalPolicy:
    return MedicalPolicy(
        MedicalReviewConfiguration(
            policy_version="medical-v1:ttl=86400",
            provider="fake",
            model="offline",
            claims_prompt_version="claims-v2",
            claims_schema_version="claims-v2",
            evidence_prompt_version="evidence-v2",
            evidence_schema_version="evidence-v2",
            validity_interval=timedelta(hours=24),
        )
    )


def draft_binding(*, content: str = DRAFT_HASH) -> DraftBinding:
    return DraftBinding(
        owner_id=OWNER_ID,
        workflow_id=WORKFLOW_ID,
        draft_version_id=DRAFT_VERSION_ID,
        content_hash=content,
    )


def test_exact_domain_values_match_approved_contract() -> None:
    assert {item.value for item in ClaimType} == {
        "effect",
        "causal",
        "association",
        "risk",
        "numeric",
        "diagnosis",
        "treatment",
        "dosage",
        "prevention",
        "safety",
    }
    assert {item.value for item in EvidenceVerdict} == {
        "supported",
        "refuted",
        "insufficient",
        "manual_required",
        "review_incomplete",
    }
    assert {item.value for item in RiskLevel} == {"green", "yellow", "red"}


def test_confirmed_extraction_rejects_hash_not_derived_from_exact_payload() -> None:
    source_id = uuid4()
    claim = AtomicClaim(
        id=uuid4(),
        exact_text="Регулярная ходьба улучшает здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Улучшает",
        modality="Может",
        source_document_ids=(source_id,),
    )
    excerpt = "Регулярная ходьба связана с улучшением показателей здоровья."
    source = EvidenceSource(
        id=source_id,
        url="https://www.who.int/example",
        exact_excerpt=excerpt,
        excerpt_hash=content_hash(excerpt),
        catalog_version="source-registry-v1",
    )

    with pytest.raises(SafeError) as caught:
        ConfirmedExtraction(
            owner_id=OWNER_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=7,
            extraction_hash=content_hash("unrelated payload"),
            confirmed_at=NOW,
            claims=(claim,),
            sources=(source,),
        )

    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE


@pytest.mark.parametrize(
    ("verdict", "risk", "has_provenance", "reason"),
    [
        (
            EvidenceVerdict.INSUFFICIENT,
            RiskLevel.RED,
            True,
            ReviewBlockingReason.RED_RISK,
        ),
        (
            EvidenceVerdict.SUPPORTED,
            RiskLevel.GREEN,
            False,
            ReviewBlockingReason.MISSING_PROVENANCE,
        ),
        (
            EvidenceVerdict.MANUAL_REQUIRED,
            RiskLevel.YELLOW,
            True,
            ReviewBlockingReason.MANUAL_REQUIRED,
        ),
        (
            EvidenceVerdict.REFUTED,
            RiskLevel.GREEN,
            True,
            ReviewBlockingReason.REFUTED,
        ),
        (
            EvidenceVerdict.REVIEW_INCOMPLETE,
            RiskLevel.GREEN,
            True,
            ReviewBlockingReason.REVIEW_INCOMPLETE,
        ),
    ],
)
def test_unsafe_or_incomplete_claim_blocks_approval(
    verdict: EvidenceVerdict,
    risk: RiskLevel,
    has_provenance: bool,
    reason: ReviewBlockingReason,
) -> None:
    review = claim_review(
        verdict=verdict,
        risk=risk,
        has_provenance=has_provenance,
    )

    decision = policy().can_approve(review, draft_binding(), now=NOW)

    assert decision.allowed is False
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("review", "reason"),
    [
        (
            replace(claim_review(), policy_version="medical-v0"),
            ReviewBlockingReason.STALE_POLICY,
        ),
        (
            replace(claim_review(), validity_seconds=3_600),
            ReviewBlockingReason.STALE_POLICY,
        ),
        (
            claim_review(exact_excerpt=""),
            ReviewBlockingReason.MISSING_EXACT_EXCERPT,
        ),
        (
            claim_review(source_url=""),
            ReviewBlockingReason.MISSING_SOURCE_URL,
        ),
    ],
)
def test_provenance_and_freshness_are_derived_from_bound_values(
    review: ClaimReview, reason: ReviewBlockingReason
) -> None:
    decision = policy().can_draft(review, now=NOW)

    assert decision.allowed is False
    assert reason in decision.reasons


def test_unknown_high_risk_applicability_blocks_downstream() -> None:
    review = claim_review(risk=RiskLevel.YELLOW, applicability="unknown")

    decision = policy().can_draft(review, now=NOW)

    assert decision.allowed is False
    assert ReviewBlockingReason.UNKNOWN_HIGH_RISK_APPLICABILITY in decision.reasons


@pytest.mark.parametrize(
    "review",
    [
        replace(
            claim_review(),
            claims=(replace(claim_review().claims[0], population=None),),
        ),
        replace(
            claim_review(),
            claims=(replace(claim_review().claims[0], context=None),),
        ),
        replace(
            claim_review(),
            claims=(replace(claim_review().claims[0], modality=None),),
        ),
        replace(
            claim_review(),
            claims=(
                replace(
                    claim_review().claims[0],
                    claim_type=ClaimType.CAUSAL,
                    causality=None,
                ),
            ),
        ),
        replace(
            claim_review(),
            claims=(
                replace(
                    claim_review().claims[0],
                    claim_type=ClaimType.NUMERIC,
                    numeric_value=None,
                ),
            ),
        ),
        claim_review(applicability=""),
        claim_review(limitations=""),
    ],
)
def test_missing_required_claim_or_evidence_field_blocks_review(
    review: ClaimReview,
) -> None:
    decision = policy().can_draft(review, now=NOW)

    assert decision.allowed is False
    assert ReviewBlockingReason.REVIEW_INCOMPLETE in decision.reasons


def test_approval_requires_exact_draft_hash_binding() -> None:
    review = claim_review()

    decision = policy().can_approve(
        review,
        draft_binding(content=content_hash("Подменённый черновик")),
        now=NOW,
    )

    assert decision.allowed is False
    assert ReviewBlockingReason.DRAFT_HASH_MISMATCH in decision.reasons


def test_evidence_from_unconfirmed_claim_source_blocks_downstream() -> None:
    review = claim_review()
    contaminated = replace(
        review,
        evidence=(
            replace(review.evidence[0], source_document_id=uuid4()),
        ),
    )

    decision = policy().can_draft(contaminated, now=NOW)

    assert decision.allowed is False
    assert ReviewBlockingReason.EXTRACTION_BINDING_MISMATCH in decision.reasons


def test_supported_current_review_can_draft_and_approve_exact_version() -> None:
    review = claim_review()

    assert policy().can_draft(review, now=NOW).allowed is True
    assert policy().can_approve(review, draft_binding(), now=NOW).allowed is True


def test_medical_repr_does_not_disclose_claim_or_evidence_text() -> None:
    rendered = repr(claim_review())

    assert "Регулярная ходьба" not in rendered
    assert "Общая рекомендация" not in rendered
    assert "www.who.int" not in rendered
