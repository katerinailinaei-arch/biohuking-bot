from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimType,
    ConfirmedExtraction,
    EvidenceSource,
    RiskLevel,
    confirmed_extraction_hash,
)
from bodrye_bot.domain.workflow import WorkflowState, WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy, MedicalReviewConfiguration
from bodrye_bot.medical.review import ClaimReviewContext, ClaimReviewService, ReviewAttempt
from bodrye_bot.ports.llm import (
    ClaimClassification,
    ClaimsResponse,
    ClaimVerdict,
    EvidenceResponse,
    UsageReport,
)

START = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
COMPLETED = START + timedelta(seconds=12)
OWNER_ID = 42
WORKFLOW_ID = uuid4()
CLAIM_ID = uuid4()
SOURCE_ID = uuid4()
ATTEMPT_ID = uuid4()


def _context() -> ClaimReviewContext:
    claim = AtomicClaim(
        id=CLAIM_ID,
        exact_text="Ходьба может поддерживать здоровье взрослых.",
        claim_type=ClaimType.EFFECT,
        population="Взрослые старше 35 лет",
        context="Регулярная ходьба умеренной интенсивности",
        causality="Может поддерживать",
        numeric_value=None,
        modality="Может",
        medical_uncertainty=False,
        source_document_ids=(SOURCE_ID,),
    )
    source = EvidenceSource(
        id=SOURCE_ID,
        url="https://www.who.int/example",
        exact_excerpt="Ходьба связана с показателями здоровья.",
        excerpt_hash=content_hash("Ходьба связана с показателями здоровья."),
        catalog_version="source-registry-v1",
    )
    extraction = ConfirmedExtraction(
        owner_id=OWNER_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=4,
        extraction_hash=confirmed_extraction_hash(
            owner_id=OWNER_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=4,
            claims=(claim,),
            sources=(source,),
        ),
        confirmed_at=START,
        claims=(claim,),
        sources=(source,),
    )
    return ClaimReviewContext(
        workflow=WorkflowState(
            id=WORKFLOW_ID,
            owner_id=OWNER_ID,
            status=WorkflowStatus.EXTRACTION_CONFIRMED,
            version=4,
        ),
        extraction=extraction,
    )


@dataclass
class AttemptRepository:
    context: ClaimReviewContext
    saved_review: object | None = None
    calls: list[tuple[UUID, str, str]] | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        self.calls = []

    async def start_attempt(self, **kwargs) -> ReviewAttempt:
        self.attempts += 1
        return ReviewAttempt(
            id=ATTEMPT_ID,
            context=self.context,
            pending_workflow_version=5,
        )

    async def record_provider_call(self, *, attempt_id, response_id, usage):
        assert self.calls is not None
        self.calls.append((attempt_id, response_id, usage.operation))
        return UUID(hex=response_id)

    async def save_outcome(self, **kwargs) -> None:
        self.saved_review = kwargs["review"]

    async def fail_attempt(self, **kwargs) -> None:
        return None


class StrictMedicalProvider:
    provider_name = "fake"
    model = "offline-medical-v2"

    def __init__(
        self,
        *,
        duplicate: bool = False,
        verdict: ClaimVerdict = ClaimVerdict.SUPPORTED,
    ) -> None:
        self.duplicate = duplicate
        self.verdict = verdict
        self._operations: dict[str, tuple[str, int, UUID]] = {}
        self.classification_response_id = uuid4().hex
        self.evidence_response_id = uuid4().hex

    async def classify_claims(self, request):
        claim = request.claims[0]
        item = ClaimClassification(
            **claim.model_dump(),
            risk=RiskLevel.YELLOW,
            verdict=self.verdict,
            rationale="Формулировка ограничена источником.",
        )
        response = ClaimsResponse(
            response_id=self.classification_response_id,
            claims=(item, item) if self.duplicate else (item,),
        )
        assert request.workflow_id is not None
        self._operations[response.response_id] = (
            "classify_claims",
            request.owner_id,
            request.workflow_id,
        )
        return response

    async def synthesize_evidence(self, request):
        claim = request.claim
        response = EvidenceResponse(
            response_id=self.evidence_response_id,
            **claim.model_dump(),
            source_document_id=request.evidence_fragment.source_document_id,
            applicability="Взрослые старше 35 лет",
            limitations="Наблюдательные данные.",
            risk=RiskLevel.YELLOW,
            synthesis="Источник поддерживает ограниченную формулировку.",
            verdict=self.verdict,
        )
        assert request.workflow_id is not None
        self._operations[response.response_id] = (
            "synthesize_evidence",
            request.owner_id,
            request.workflow_id,
        )
        return response

    async def estimate_or_report_usage(self, response_id: str) -> UsageReport:
        operation, owner_id, workflow_id = self._operations[response_id]
        prompt = "claims-medical-v2" if operation == "classify_claims" else "evidence-medical-v2"
        return UsageReport(
            owner_id=owner_id,
            workflow_id=workflow_id,
            operation=operation,
            provider=self.provider_name,
            model=self.model,
            status="succeeded",
            prompt_version=prompt,
            schema_version=prompt,
            provider_request_id=f"req-{operation}",
            latency_ms=5,
            input_tokens=10,
            output_tokens=5,
            error_class=None,
            trace_id=response_id,
        )


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


def _service(repository: AttemptRepository, provider: StrictMedicalProvider):
    moments = iter((START, COMPLETED))
    config = _configuration()
    return ClaimReviewService(
        owner_id=OWNER_ID,
        repository=repository,
        provider=provider,
        policy=MedicalPolicy(config),
        configuration=config,
        clock=lambda: next(moments),
        attempt_lease=timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_review_claims_attempt_before_calls_and_binds_actual_runs_at_completion() -> None:
    repository = AttemptRepository(_context())
    provider = StrictMedicalProvider()

    review = await _service(repository, provider).review(WORKFLOW_ID)

    assert repository.attempts == 1
    assert repository.saved_review == review
    assert review.reviewed_at == COMPLETED
    assert review.classification_run_id == UUID(hex=provider.classification_response_id)
    assert review.evidence[0].model_run_id == UUID(hex=provider.evidence_response_id)
    assert review.evidence[0].risk is RiskLevel.YELLOW
    assert review.evidence[0].applicability == "Взрослые старше 35 лет"
    assert [item[2] for item in repository.calls or []] == [
        "classify_claims",
        "synthesize_evidence",
    ]


@pytest.mark.asyncio
async def test_duplicate_provider_claim_identity_fails_closed_after_durable_attempt() -> None:
    repository = AttemptRepository(_context())

    with pytest.raises(SafeError) as caught:
        await _service(repository, StrictMedicalProvider(duplicate=True)).review(WORKFLOW_ID)

    assert caught.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE
    assert repository.attempts == 1
    assert repository.saved_review is None
