from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimType,
    ConfirmedExtraction,
    EvidenceSource,
    EvidenceVerdict,
    confirmed_extraction_hash,
)
from bodrye_bot.domain.workflow import WorkflowState, WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy
from bodrye_bot.medical.review import ClaimReviewContext, ClaimReviewService
from bodrye_bot.ports.llm import (
    ClaimClassification,
    ClaimsResponse,
    EvidenceResponse,
)

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
OWNER_ID = 42
WORKFLOW_ID = uuid4()
MODEL_RUN_ID = uuid4()
SOURCE_DOCUMENT_ID = uuid4()


class FakeProvider:
    def __init__(self, *, verdict: str = "supported") -> None:
        self.verdict = verdict
        self.invocations = 0

    async def classify_claims(self, request):
        self.invocations += 1
        from bodrye_bot.ports.llm import ClaimVerdict

        return ClaimsResponse(
            response_id="1" * 32,
            claims=tuple(
                ClaimClassification(
                    exact_text=claim,
                    verdict=ClaimVerdict(self.verdict),
                    rationale="Проверено по сохранённому источнику.",
                )
                for claim in request.claims
            ),
        )

    async def synthesize_evidence(self, request):
        self.invocations += 1
        from bodrye_bot.ports.llm import ClaimVerdict

        return EvidenceResponse(
            response_id="2" * 32,
            synthesis="Формулировка соответствует источнику.",
            verdict=ClaimVerdict(self.verdict),
        )


@dataclass
class InMemoryMedicalRepository:
    context: ClaimReviewContext
    saved_review: object | None = None
    saved_status: WorkflowStatus | None = None
    saved_reasons: tuple[object, ...] | None = None
    loads: list[tuple[int, UUID]] | None = None

    def __post_init__(self) -> None:
        self.loads = []

    async def load_context(self, *, owner_id: int, workflow_id: UUID) -> ClaimReviewContext:
        assert self.loads is not None
        self.loads.append((owner_id, workflow_id))
        return self.context

    async def save_outcome(
        self,
        *,
        owner_id: int,
        review,
        decision,
        expected_workflow_version: int,
    ) -> None:
        if owner_id != self.context.workflow.owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        if expected_workflow_version != self.context.workflow.version:
            raise RuntimeError("stale")
        self.saved_review = review
        self.saved_reasons = decision.reasons
        self.saved_status = (
            WorkflowStatus.CLAIMS_REVIEW_PASSED
            if decision.allowed
            else WorkflowStatus.CLAIMS_REVIEW_BLOCKED
        )
        self.context = replace(
            self.context,
            workflow=replace(
                self.context.workflow,
                status=self.saved_status,
                version=expected_workflow_version + 2,
            ),
        )


def review_context(
    *, status: WorkflowStatus = WorkflowStatus.EXTRACTION_CONFIRMED
) -> ClaimReviewContext:
    claim = AtomicClaim(
        id=uuid4(),
        exact_text="Регулярная ходьба улучшает здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Улучшает",
        numeric_value=None,
        modality="Может",
        source_document_ids=(SOURCE_DOCUMENT_ID,),
    )
    sources = (
        EvidenceSource(
            id=SOURCE_DOCUMENT_ID,
            url="https://www.who.int/example",
            exact_excerpt=(
                "Регулярная ходьба связана с улучшением показателей здоровья."
            ),
            excerpt_hash=content_hash(
                "Регулярная ходьба связана с улучшением показателей здоровья."
            ),
            applicability="Взрослые старше 35 лет",
            limitations="Общая рекомендация, не индивидуальное назначение.",
        ),
    )
    claims = (claim,)
    extraction = ConfirmedExtraction(
        owner_id=OWNER_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=4,
        extraction_hash=confirmed_extraction_hash(
            owner_id=OWNER_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=4,
            claims=claims,
            sources=sources,
        ),
        confirmed_at=NOW,
        claims=claims,
        sources=sources,
    )
    return ClaimReviewContext(
        workflow=WorkflowState(
            id=WORKFLOW_ID,
            owner_id=OWNER_ID,
            status=status,
            version=4,
        ),
        extraction=extraction,
    )


def service(
    repository: InMemoryMedicalRepository,
    provider: FakeProvider,
) -> ClaimReviewService:
    return ClaimReviewService(
        owner_id=OWNER_ID,
        repository=repository,
        provider=provider,
        policy=MedicalPolicy(
            policy_version="medical-v1",
            active_model_run_id=MODEL_RUN_ID,
        ),
        prompt_version="claims-v1",
        schema_version="claims-v1",
        model_run_id=MODEL_RUN_ID,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_claim_review_requires_confirmed_extraction() -> None:
    repository = InMemoryMedicalRepository(
        review_context(status=WorkflowStatus.EXTRACTED)
    )
    provider = FakeProvider()

    with pytest.raises(SafeError) as caught:
        await service(repository, provider).review(WORKFLOW_ID)

    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE
    assert provider.invocations == 0
    assert repository.saved_review is None


@pytest.mark.asyncio
async def test_supported_claim_persists_passed_review_and_workflow_outcome_atomically() -> None:
    repository = InMemoryMedicalRepository(review_context())
    provider = FakeProvider()

    review = await service(repository, provider).review(WORKFLOW_ID)

    assert repository.saved_review == review
    assert repository.saved_status is WorkflowStatus.CLAIMS_REVIEW_PASSED
    assert repository.saved_reasons == ()
    assert repository.context.workflow.status is WorkflowStatus.CLAIMS_REVIEW_PASSED
    assert review.owner_id == OWNER_ID
    assert review.workflow_id == WORKFLOW_ID
    assert review.workflow_version == 4
    assert review.extraction_hash == repository.context.extraction.extraction_hash
    assert review.policy_version == "medical-v1"
    assert review.model_run_id == MODEL_RUN_ID
    assert review.reviewed_at == NOW
    assert review.evidence[0].source_document_id == SOURCE_DOCUMENT_ID
    assert review.evidence[0].exact_excerpt.startswith("Регулярная ходьба")


@pytest.mark.asyncio
async def test_provider_manual_review_is_normalized_and_blocks_angles() -> None:
    repository = InMemoryMedicalRepository(review_context())
    provider = FakeProvider(verdict="manual_review")

    review = await service(repository, provider).review(WORKFLOW_ID)

    assert review.evidence[0].verdict is EvidenceVerdict.MANUAL_REQUIRED
    assert repository.saved_status is WorkflowStatus.CLAIMS_REVIEW_BLOCKED
    assert tuple(reason.value for reason in repository.saved_reasons or ()) == (
        "manual_required",
    )


@pytest.mark.asyncio
async def test_contaminated_owner_context_fails_before_provider_invocation_or_write() -> None:
    repository = InMemoryMedicalRepository(
        replace(
            review_context(),
            workflow=replace(review_context().workflow, owner_id=999),
        )
    )
    provider = FakeProvider()

    with pytest.raises(SafeError) as caught:
        await service(repository, provider).review(WORKFLOW_ID)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN
    assert provider.invocations == 0
    assert repository.saved_review is None
