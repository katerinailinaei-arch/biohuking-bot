from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import Evidence
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.medical.use_case import MedicalReviewUseCase
from tests.e2e.test_claim_review_round_one import StrictMedicalProvider
from tests.integration.test_medical_repository_round_one import (
    NOW,
    _configuration,
    _real_catalog_extraction,
)


@pytest.mark.asyncio
async def test_medical_repository_uses_owner_scope_before_disclosing_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, repository = await _real_catalog_extraction(session_factory)

    with pytest.raises(SafeError) as caught:
        await repository.load_context(
            owner_id=owner_id + 1,
            workflow_id=extraction.workflow_id,
        )

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_database_rejects_duplicate_review_claim_document_audit_pair(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, extraction, _ = await _real_catalog_extraction(session_factory)
    review = await MedicalReviewUseCase(
        session_factory=session_factory,
        provider=StrictMedicalProvider(),
        configuration=_configuration(),
        clock=iter((NOW, NOW + timedelta(seconds=10))).__next__,
    ).review(owner_id=owner_id, workflow_id=extraction.workflow_id)

    async with session_factory() as session:
        original = await session.scalar(
            select(Evidence).where(Evidence.review_decision_id == review.id)
        )
        assert original is not None
        duplicate = Evidence(
            id=uuid4(),
            owner_id=original.owner_id,
            claim_id=original.claim_id,
            source_document_id=original.source_document_id,
            review_decision_id=original.review_decision_id,
            response_id=uuid4().hex,
            verdict=original.verdict,
            risk=original.risk,
            exact_excerpt=original.exact_excerpt,
            excerpt_hash=original.excerpt_hash,
            applicability=original.applicability,
            limitations=original.limitations,
            reviewed_at=original.reviewed_at,
            review_model_run_id=original.review_model_run_id,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.commit()
