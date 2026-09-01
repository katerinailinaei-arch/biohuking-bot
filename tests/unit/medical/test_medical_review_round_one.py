from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimReview,
    ClaimType,
    DraftBinding,
    Evidence,
    EvidenceVerdict,
    ReviewBlockingReason,
    RiskLevel,
)
from bodrye_bot.medical.policy import MedicalPolicy, MedicalReviewConfiguration

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
OWNER_ID = 42
WORKFLOW_ID = uuid4()
CLAIM_ID = uuid4()
SOURCE_ID = uuid4()
CLASSIFY_RUN_ID = uuid4()
EVIDENCE_RUN_ID = uuid4()
DRAFT_ID = uuid4()
DRAFT_HASH = content_hash("Точный черновик")


def _review(*, reviewed_at: datetime = NOW) -> ClaimReview:
    claim = AtomicClaim(
        id=CLAIM_ID,
        exact_text="Ходьба может поддерживать здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Может поддерживать",
        modality="Может",
        source_document_ids=(SOURCE_ID,),
    )
    evidence = Evidence(
        id=uuid4(),
        claim_id=CLAIM_ID,
        source_document_id=SOURCE_ID,
        source_url="https://www.who.int/example",
        exact_excerpt="Ходьба связана с показателями здоровья.",
        excerpt_hash=content_hash("Ходьба связана с показателями здоровья."),
        applicability="Взрослые старше 35 лет",
        limitations="Наблюдательные данные; не индивидуальная рекомендация.",
        verdict=EvidenceVerdict.SUPPORTED,
        risk=RiskLevel.GREEN,
        reviewed_at=reviewed_at,
        model_run_id=EVIDENCE_RUN_ID,
        response_id="2" * 32,
    )
    return ClaimReview(
        id=uuid4(),
        owner_id=OWNER_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=7,
        extraction_hash=content_hash("confirmed extraction"),
        draft_version_id=DRAFT_ID,
        draft_hash=DRAFT_HASH,
        policy_version="medical-v2:ttl=86400",
        validity_seconds=86_400,
        classification_run_id=CLASSIFY_RUN_ID,
        classification_response_id="1" * 32,
        reviewed_at=reviewed_at,
        claims=(claim,),
        evidence=(evidence,),
    )


def _policy() -> MedicalPolicy:
    return MedicalPolicy(
        MedicalReviewConfiguration(
            policy_version="medical-v2:ttl=86400",
            provider="fake",
            model="offline-medical-v2",
            claims_prompt_version="claims-medical-v2",
            claims_schema_version="claims-medical-v2",
            evidence_prompt_version="evidence-medical-v2",
            evidence_schema_version="evidence-medical-v2",
            validity_interval=timedelta(hours=24),
        )
    )


@pytest.mark.parametrize(
    ("reviewed_at", "reason"),
    [
        (NOW - timedelta(days=2), ReviewBlockingReason.REVIEW_EXPIRED),
        (NOW + timedelta(seconds=1), ReviewBlockingReason.REVIEW_IN_FUTURE),
    ],
)
def test_policy_uses_trusted_now_for_review_freshness(
    reviewed_at: datetime, reason: ReviewBlockingReason
) -> None:
    decision = _policy().can_draft(_review(reviewed_at=reviewed_at), now=NOW)

    assert decision.allowed is False
    assert reason in decision.reasons


def test_approval_requires_exact_owner_workflow_draft_id_and_hash() -> None:
    review = _review()
    substituted = DraftBinding(
        owner_id=OWNER_ID,
        workflow_id=WORKFLOW_ID,
        draft_version_id=uuid4(),
        content_hash=DRAFT_HASH,
    )

    decision = _policy().can_approve(review, substituted, now=NOW)

    assert decision.allowed is False
    assert ReviewBlockingReason.DRAFT_VERSION_MISMATCH in decision.reasons


def test_domain_rejects_mutable_collection_even_if_annotated_as_tuple() -> None:
    review = _review()

    with pytest.raises(SafeError):
        replace(review, claims=list(review.claims))  # type: ignore[arg-type]


def test_duplicate_claim_document_evidence_pair_is_rejected_before_persistence() -> None:
    review = _review()

    with pytest.raises(SafeError):
        replace(review, evidence=(review.evidence[0], replace(review.evidence[0], id=uuid4())))
