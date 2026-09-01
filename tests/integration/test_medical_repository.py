from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import (
    Claim,
    ClaimReviewDecision,
    ContentWorkflow,
    ProviderRun,
    Source,
    SourceDocument,
)
from bodrye_bot.db.models import (
    Evidence as EvidenceModel,
)
from bodrye_bot.db.repositories.medical import SqlAlchemyMedicalRepository
from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimReview,
    ClaimType,
    ConfirmedExtraction,
    EvidenceSource,
    EvidenceVerdict,
    RiskLevel,
    confirmed_extraction_hash,
)
from bodrye_bot.domain.medical import (
    Evidence as DomainEvidence,
)
from bodrye_bot.domain.workflow import WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy
from bodrye_bot.medical.review import ClaimReviewService
from bodrye_bot.ports.llm import (
    ClaimClassification,
    ClaimsResponse,
    ClaimVerdict,
    EvidenceResponse,
)

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


class SupportedProvider:
    async def classify_claims(self, request):
        return ClaimsResponse(
            response_id="a" * 32,
            claims=tuple(
                ClaimClassification(
                    exact_text=text,
                    verdict=ClaimVerdict.SUPPORTED,
                    rationale="Подтверждено.",
                )
                for text in request.claims
            ),
        )

    async def synthesize_evidence(self, request):
        return EvidenceResponse(
            response_id="b" * 32,
            synthesis="Подтверждено сохранённым источником.",
            verdict=ClaimVerdict.SUPPORTED,
        )


async def _confirmed_case(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[
    int,
    UUID,
    UUID,
    UUID,
    SqlAlchemyMedicalRepository,
    ConfirmedExtraction,
]:
    owner_id = 80_000 + uuid4().int % 10_000
    workflow_id = uuid4()
    source_id = uuid4()
    document_id = uuid4()
    model_run_id = uuid4()
    claim_id = uuid4()
    excerpt = "Регулярная ходьба связана с улучшением показателей здоровья."
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    Source(
                        id=source_id,
                        owner_id=owner_id,
                        name="WHO",
                        canonical_url="https://www.who.int/",
                        source_type="evidence",
                        roles=["evidence"],
                        access_method="http",
                        status="active",
                        failure_count=0,
                        config_json={
                            "applicability": "Взрослые старше 35 лет",
                            "limitations": (
                                "Общая рекомендация, не индивидуальное назначение."
                            ),
                        },
                    ),
                    ContentWorkflow(
                        id=workflow_id,
                        owner_id=owner_id,
                        origin_type="manual_text",
                        status=WorkflowStatus.EXTRACTED,
                        recommended_format="medium",
                        version=3,
                    ),
                ]
            )
            await session.flush()
            session.add(
                SourceDocument(
                    id=document_id,
                    owner_id=owner_id,
                    source_id=source_id,
                    url="https://www.who.int/example",
                    fetched_at=NOW,
                    content_hash=content_hash(excerpt),
                    bounded_excerpt=excerpt,
                    fetch_status="available",
                    http_metadata={},
                )
            )
            session.add(
                ProviderRun(
                    id=model_run_id,
                    owner_id=owner_id,
                    workflow_id=workflow_id,
                    operation="claim_review",
                    provider="fake",
                    model="offline",
                    status="success",
                    prompt_version="claims-v1",
                    schema_version="claims-v1",
                )
            )

    claims = (
        AtomicClaim(
            id=claim_id,
            exact_text="Регулярная ходьба улучшает здоровье взрослых.",
            claim_type=ClaimType.EFFECT,
            population="Взрослые старше 35 лет",
            context="Регулярная ходьба умеренной интенсивности",
            causality="Улучшает",
            numeric_value=None,
            modality="Может",
            source_document_ids=(document_id,),
        ),
    )
    sources = (
        EvidenceSource(
            id=document_id,
            url="https://www.who.int/example",
            exact_excerpt=excerpt,
            excerpt_hash=content_hash(excerpt),
            applicability="Взрослые старше 35 лет",
            limitations="Общая рекомендация, не индивидуальное назначение.",
        ),
    )
    extraction = ConfirmedExtraction(
        owner_id=owner_id,
        workflow_id=workflow_id,
        workflow_version=4,
        extraction_hash=confirmed_extraction_hash(
            owner_id=owner_id,
            workflow_id=workflow_id,
            workflow_version=4,
            claims=claims,
            sources=sources,
        ),
        confirmed_at=NOW,
        claims=claims,
        sources=sources,
    )
    repository = SqlAlchemyMedicalRepository(session_factory)
    await repository.confirm_extraction(extraction, expected_workflow_version=3)
    return owner_id, workflow_id, claim_id, model_run_id, repository, extraction

@pytest.mark.asyncio
async def test_confirmation_and_review_have_atomic_durable_owner_bound_outcome(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, workflow_id, claim_id, model_run_id, repository, extraction = (
        await _confirmed_case(session_factory)
    )
    document_id = extraction.sources[0].id

    context = await repository.load_context(
        owner_id=owner_id,
        workflow_id=workflow_id,
    )
    assert context.workflow.status is WorkflowStatus.EXTRACTION_CONFIRMED
    assert context.workflow.version == 4
    assert context.extraction == extraction

    policy = MedicalPolicy(
        policy_version="medical-v1",
        active_model_run_id=model_run_id,
    )
    review = await ClaimReviewService(
        owner_id=owner_id,
        repository=repository,
        provider=SupportedProvider(),
        policy=policy,
        prompt_version="claims-v1",
        schema_version="claims-v1",
        model_run_id=model_run_id,
        clock=lambda: NOW,
    ).review(workflow_id)

    async with session_factory() as session:
        workflow = await session.get(ContentWorkflow, workflow_id)
        decision = await session.scalar(
            select(ClaimReviewDecision).where(
                ClaimReviewDecision.workflow_id == workflow_id,
                ClaimReviewDecision.owner_id == owner_id,
            )
        )
        evidence_count = await session.scalar(
            select(func.count())
            .select_from(EvidenceModel)
            .where(EvidenceModel.owner_id == owner_id, EvidenceModel.claim_id == claim_id)
        )
        stored_claim = await session.get(Claim, claim_id)

    assert workflow is not None
    assert workflow.status is WorkflowStatus.CLAIMS_REVIEW_PASSED
    assert workflow.version == 6
    assert decision is not None
    assert decision.extraction_hash == extraction.extraction_hash
    assert decision.workflow_version == 4
    assert decision.policy_version == "medical-v1"
    assert decision.model_run_id == model_run_id
    assert decision.reviewed_at == NOW
    assert decision.blocking_reasons == []
    assert evidence_count == 1
    assert stored_claim is not None
    assert stored_claim.status == "supported"
    assert review.evidence[0].source_document_id == document_id


@pytest.mark.asyncio
async def test_database_failure_rolls_back_workflow_evidence_and_decision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, workflow_id, claim_id, model_run_id, repository, extraction = (
        await _confirmed_case(session_factory)
    )
    source = extraction.sources[0]
    duplicate_id = uuid4()
    evidence = DomainEvidence(
        id=duplicate_id,
        claim_id=claim_id,
        source_document_id=source.id,
        source_url=source.url,
        exact_excerpt=source.exact_excerpt,
        excerpt_hash=source.excerpt_hash,
        applicability=source.applicability,
        limitations=source.limitations,
        verdict=EvidenceVerdict.SUPPORTED,
        risk=RiskLevel.GREEN,
        reviewed_at=NOW,
        model_run_id=model_run_id,
    )
    review = ClaimReview(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        workflow_version=4,
        extraction_hash=extraction.extraction_hash,
        draft_version_id=None,
        draft_hash=None,
        policy_version="medical-v1",
        model_run_id=model_run_id,
        reviewed_at=NOW,
        claims=extraction.claims,
        evidence=(evidence, replace(evidence)),
    )
    policy = MedicalPolicy(
        policy_version="medical-v1",
        active_model_run_id=model_run_id,
    )

    with pytest.raises(SafeError) as caught:
        await repository.save_outcome(
            owner_id=owner_id,
            review=review,
            decision=policy.can_draft(review),
            expected_workflow_version=4,
        )

    assert caught.value.code is SafeErrorCode.INTERNAL_ERROR
    async with session_factory() as session:
        workflow = await session.get(ContentWorkflow, workflow_id)
        stored_claim = await session.get(Claim, claim_id)
        evidence_count = await session.scalar(
            select(func.count())
            .select_from(EvidenceModel)
            .where(EvidenceModel.owner_id == owner_id, EvidenceModel.claim_id == claim_id)
        )
        decision_count = await session.scalar(
            select(func.count())
            .select_from(ClaimReviewDecision)
            .where(ClaimReviewDecision.owner_id == owner_id)
        )

    assert workflow is not None
    assert workflow.status is WorkflowStatus.EXTRACTION_CONFIRMED
    assert workflow.version == 4
    assert stored_claim is not None
    assert stored_claim.status == "pending"
    assert evidence_count == 0
    assert decision_count == 0


@pytest.mark.asyncio
async def test_medical_repository_uses_owner_scope_before_disclosing_context(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_workflow: ContentWorkflow,
) -> None:
    repository = SqlAlchemyMedicalRepository(session_factory)

    with pytest.raises(SafeError) as caught:
        await repository.load_context(owner_id=999, workflow_id=seeded_workflow.id)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN
