from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import (
    AuditEvent,
    ClaimReviewDecision,
    ContentWorkflow,
    DraftVersion,
    Evidence,
    ExtractionConfirmation,
    MedicalReviewAttempt,
    Source,
    SourceDocument,
)
from bodrye_bot.db.repositories.medical import SqlAlchemyMedicalRepository
from bodrye_bot.db.uow import SqlAlchemyUnitOfWork
from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimType,
    ConfirmedExtraction,
    EvidenceSource,
    confirmed_extraction_hash,
)
from bodrye_bot.domain.workflow import WorkflowStatus
from bodrye_bot.medical.policy import MedicalReviewConfiguration
from bodrye_bot.medical.use_case import MedicalReviewUseCase
from bodrye_bot.sources.catalog import SourceCatalog
from tests.e2e.test_claim_review_round_one import StrictMedicalProvider

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


async def _real_catalog_extraction(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, ConfirmedExtraction, SqlAlchemyMedicalRepository]:
    owner_id = 1_000_000 + uuid4().int % 1_000_000
    workflow_id = uuid4()
    claim_id = uuid4()
    document_id = uuid4()
    catalog = SourceCatalog.initial()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.catalogs.save(owner_id, catalog)
        await uow.commit()
    async with session_factory() as session:
        async with session.begin():
            source = await session.scalar(
                select(Source).where(
                    Source.owner_id == owner_id,
                    Source.canonical_url == "https://www.who.int/news-room/fact-sheets",
                )
            )
            assert source is not None
            excerpt = "Ходьба связана с показателями здоровья взрослых."
            session.add(
                ContentWorkflow(
                    id=workflow_id,
                    owner_id=owner_id,
                    origin_type="manual_text",
                    status=WorkflowStatus.EXTRACTED,
                    recommended_format="medium",
                    version=3,
                )
            )
            session.add(
                SourceDocument(
                    id=document_id,
                    owner_id=owner_id,
                    source_id=source.id,
                    url="https://www.who.int/news-room/fact-sheets/detail/example",
                    fetched_at=NOW,
                    content_hash=content_hash(excerpt),
                    bounded_excerpt=excerpt,
                    fetch_status="available",
                    http_metadata={"status": 200},
                )
            )
    claim = AtomicClaim(
        id=claim_id,
        exact_text="Ходьба может поддерживать здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Может поддерживать",
        numeric_value=None,
        modality="Может",
        medical_uncertainty=False,
        source_document_ids=(document_id,),
    )
    evidence_source = EvidenceSource(
        id=document_id,
        url="https://www.who.int/news-room/fact-sheets/detail/example",
        exact_excerpt=excerpt,
        excerpt_hash=content_hash(excerpt),
        catalog_version=catalog.version,
    )
    extraction = ConfirmedExtraction(
        owner_id=owner_id,
        workflow_id=workflow_id,
        workflow_version=4,
        extraction_hash=confirmed_extraction_hash(
            owner_id=owner_id,
            workflow_id=workflow_id,
            workflow_version=4,
            claims=(claim,),
            sources=(evidence_source,),
        ),
        confirmed_at=NOW,
        claims=(claim,),
        sources=(evidence_source,),
    )
    repository = SqlAlchemyMedicalRepository(session_factory)
    await repository.confirm_extraction(extraction, expected_workflow_version=3)
    return owner_id, extraction, repository


def _configuration() -> MedicalReviewConfiguration:
    return MedicalReviewConfiguration(
        policy_version="medical-v2:ttl=86400",
        provider="fake",
        model="offline-medical-v2",
        claims_prompt_version="claims-medical-v2",
        claims_schema_version="claims-medical-v2",
        evidence_prompt_version="evidence-medical-v2",
        evidence_schema_version="evidence-medical-v2",
        validity_interval=timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_production_use_case_uses_real_catalog_and_persists_provider_evidence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, _ = await _real_catalog_extraction(session_factory)
    use_case = MedicalReviewUseCase(
        session_factory=session_factory,
        provider=StrictMedicalProvider(),
        configuration=_configuration(),
        clock=iter((NOW, NOW + timedelta(seconds=10))).__next__,
    )

    review = await use_case.review(owner_id=owner_id, workflow_id=extraction.workflow_id)

    async with session_factory() as session:
        workflow = await session.get(ContentWorkflow, extraction.workflow_id)
        decision = await session.scalar(
            select(ClaimReviewDecision).where(ClaimReviewDecision.id == review.id)
        )
        stored_evidence = await session.scalar(
            select(Evidence).where(Evidence.review_decision_id == review.id)
        )
        runs = await session.scalar(
            select(func.count()).select_from(MedicalReviewAttempt)
        )
    assert workflow is not None and workflow.status is WorkflowStatus.CLAIMS_REVIEW_PASSED
    assert decision is not None and decision.attempt_id is not None
    assert stored_evidence is not None
    assert stored_evidence.applicability == "Взрослые старше 35 лет"
    assert stored_evidence.limitations == "Наблюдательные данные."
    assert runs is not None and runs >= 1


@pytest.mark.asyncio
async def test_attempt_fence_rejects_concurrent_call_and_recovers_expired_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, repository = await _real_catalog_extraction(session_factory)
    first = await repository.start_attempt(
        owner_id=owner_id,
        workflow_id=extraction.workflow_id,
        started_at=NOW,
        lease_until=NOW + timedelta(minutes=5),
    )

    with pytest.raises(SafeError) as caught:
        await repository.start_attempt(
            owner_id=owner_id,
            workflow_id=extraction.workflow_id,
            started_at=NOW + timedelta(minutes=1),
            lease_until=NOW + timedelta(minutes=6),
        )
    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE

    recovered = await repository.start_attempt(
        owner_id=owner_id,
        workflow_id=extraction.workflow_id,
        started_at=NOW + timedelta(minutes=6),
        lease_until=NOW + timedelta(minutes=11),
    )
    assert recovered.id != first.id
    assert recovered.pending_workflow_version == first.pending_workflow_version

    async with session_factory() as session:
        audit_count_before = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.workflow_id == extraction.workflow_id
            )
        )
    await repository.fail_attempt(owner_id=owner_id, attempt_id=recovered.id)
    async with session_factory() as session:
        workflow = await session.get(ContentWorkflow, extraction.workflow_id)
        audit_count_after = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.workflow_id == extraction.workflow_id
            )
        )
    assert workflow is not None
    assert workflow.status is WorkflowStatus.CLAIMS_REVIEW_BLOCKED
    assert audit_count_before is not None and audit_count_after == audit_count_before + 1


@pytest.mark.asyncio
async def test_evidence_source_authority_rechecked_at_finalization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, repository = await _real_catalog_extraction(session_factory)
    attempt = await repository.start_attempt(
        owner_id=owner_id,
        workflow_id=extraction.workflow_id,
        started_at=NOW,
        lease_until=NOW + timedelta(minutes=5),
    )
    async with session_factory() as session:
        async with session.begin():
            source = await session.scalar(
                select(Source).join(SourceDocument).where(
                    SourceDocument.id == extraction.sources[0].id
                )
            )
            assert source is not None
            source.roles = ["topic"]

    with pytest.raises(SafeError) as caught:
        await repository.start_attempt(
            owner_id=owner_id,
            workflow_id=extraction.workflow_id,
            started_at=NOW + timedelta(minutes=6),
            lease_until=NOW + timedelta(minutes=11),
        )
    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE
    assert attempt.id


@pytest.mark.asyncio
async def test_blocked_workflow_accepts_new_versioned_confirmation_without_deleting_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, repository = await _real_catalog_extraction(session_factory)
    async with session_factory() as session:
        async with session.begin():
            workflow = await session.get(ContentWorkflow, extraction.workflow_id)
            assert workflow is not None
            workflow.status = WorkflowStatus.CLAIMS_REVIEW_BLOCKED
            workflow.version = 6
    revised_claim = replace(
        extraction.claims[0],
        id=uuid4(),
        exact_text="Ходьба связана со здоровьем.",
    )
    revised = ConfirmedExtraction(
        owner_id=owner_id,
        workflow_id=extraction.workflow_id,
        workflow_version=7,
        extraction_hash=confirmed_extraction_hash(
            owner_id=owner_id,
            workflow_id=extraction.workflow_id,
            workflow_version=7,
            claims=(revised_claim,),
            sources=extraction.sources,
        ),
        confirmed_at=NOW + timedelta(minutes=10),
        claims=(revised_claim,),
        sources=extraction.sources,
    )

    await repository.confirm_extraction(revised, expected_workflow_version=6)

    async with session_factory() as session:
        confirmations = list(
            (
                await session.scalars(
                    select(ExtractionConfirmation).where(
                        ExtractionConfirmation.workflow_id == extraction.workflow_id
                    )
                )
            ).all()
        )
    assert len(confirmations) == 2
    assert sum(item.is_current for item in confirmations) == 1
    current = next(item for item in confirmations if item.is_current)
    assert current.extraction_hash == revised.extraction_hash


@pytest.mark.asyncio
async def test_persisted_review_binds_only_exact_current_draft_id_and_hash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, _ = await _real_catalog_extraction(session_factory)
    config = _configuration()
    review = await MedicalReviewUseCase(
        session_factory=session_factory,
        provider=StrictMedicalProvider(),
        configuration=config,
        clock=iter((NOW, NOW + timedelta(seconds=10))).__next__,
    ).review(owner_id=owner_id, workflow_id=extraction.workflow_id)
    draft_id = uuid4()
    draft_hash = content_hash("Точный текущий черновик")
    async with session_factory() as session:
        async with session.begin():
            session.add(
                DraftVersion(
                    id=draft_id,
                    owner_id=owner_id,
                    workflow_id=extraction.workflow_id,
                    version_number=1,
                    body="Точный текущий черновик",
                    body_hash=draft_hash,
                    format="medium",
                    headlines=["Заголовок"],
                    public_sources=[],
                    style_profile_version=1,
                )
            )
            workflow = await session.get(ContentWorkflow, extraction.workflow_id)
            assert workflow is not None
            workflow.current_version_id = draft_id

    repository = SqlAlchemyMedicalRepository(session_factory, configuration=config)
    with pytest.raises(SafeError):
        await repository.bind_current_draft(
            owner_id=owner_id,
            workflow_id=extraction.workflow_id,
            review_id=review.id,
            draft_version_id=uuid4(),
            draft_hash=draft_hash,
        )

    await repository.bind_current_draft(
        owner_id=owner_id,
        workflow_id=extraction.workflow_id,
        review_id=review.id,
        draft_version_id=draft_id,
        draft_hash=draft_hash,
    )
    async with session_factory() as session:
        decision = await session.get(ClaimReviewDecision, review.id)
    assert decision is not None
    assert decision.draft_version_id == draft_id
    assert decision.draft_hash == draft_hash

    replacement_id = uuid4()
    replacement_hash = content_hash("Изменённый черновик требует нового review")
    async with session_factory() as session:
        async with session.begin():
            session.add(
                DraftVersion(
                    id=replacement_id,
                    owner_id=owner_id,
                    workflow_id=extraction.workflow_id,
                    version_number=2,
                    body="Изменённый черновик требует нового review",
                    body_hash=replacement_hash,
                    format="medium",
                    headlines=["Новый заголовок"],
                    public_sources=[],
                    style_profile_version=1,
                    supersedes_id=draft_id,
                )
            )
            workflow = await session.get(ContentWorkflow, extraction.workflow_id)
            assert workflow is not None
            workflow.current_version_id = replacement_id

    with pytest.raises(SafeError):
        await repository.bind_current_draft(
            owner_id=owner_id,
            workflow_id=extraction.workflow_id,
            review_id=review.id,
            draft_version_id=replacement_id,
            draft_hash=replacement_hash,
        )
