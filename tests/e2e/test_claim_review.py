from __future__ import annotations

from dataclasses import replace

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import EvidenceVerdict
from bodrye_bot.domain.workflow import WorkflowStatus
from bodrye_bot.ports.llm import ClaimVerdict
from tests.e2e.test_claim_review_round_one import (
    WORKFLOW_ID,
    AttemptRepository,
    StrictMedicalProvider,
    _context,
    _service,
)


@pytest.mark.asyncio
async def test_claim_review_requires_confirmed_extraction_before_provider_calls() -> None:
    context = _context()
    repository = AttemptRepository(
        replace(
            context,
            workflow=replace(context.workflow, status=WorkflowStatus.EXTRACTED),
        )
    )

    with pytest.raises(SafeError) as caught:
        await _service(repository, StrictMedicalProvider()).review(WORKFLOW_ID)

    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE
    assert repository.saved_review is None


@pytest.mark.asyncio
async def test_supported_claim_persists_passed_review() -> None:
    repository = AttemptRepository(_context())

    review = await _service(repository, StrictMedicalProvider()).review(WORKFLOW_ID)

    assert repository.saved_review == review
    assert review.evidence[0].verdict is EvidenceVerdict.SUPPORTED


@pytest.mark.asyncio
async def test_legacy_provider_manual_review_is_normalized_to_domain_manual_required() -> None:
    repository = AttemptRepository(_context())

    review = await _service(
        repository,
        StrictMedicalProvider(verdict=ClaimVerdict.MANUAL_REVIEW),
    ).review(WORKFLOW_ID)

    assert review.evidence[0].verdict is EvidenceVerdict.MANUAL_REQUIRED
