from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimReview,
    ConfirmedExtraction,
    Evidence,
    EvidenceVerdict,
    MedicalDecision,
    RiskLevel,
)
from bodrye_bot.domain.workflow import WorkflowState, WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy
from bodrye_bot.ports.llm import (
    ClaimsRequest,
    ClaimsResponse,
    ClaimVerdict,
    EvidenceRequest,
    EvidenceResponse,
)


class ClaimsEvidenceProvider(Protocol):
    async def classify_claims(self, request: ClaimsRequest) -> ClaimsResponse: ...

    async def synthesize_evidence(self, request: EvidenceRequest) -> EvidenceResponse: ...


@dataclass(frozen=True)
class ClaimReviewContext:
    workflow: WorkflowState
    extraction: ConfirmedExtraction


class MedicalRepository(Protocol):
    async def load_context(
        self, *, owner_id: int, workflow_id: UUID
    ) -> ClaimReviewContext: ...

    async def save_outcome(
        self,
        *,
        owner_id: int,
        review: ClaimReview,
        decision: MedicalDecision,
        expected_workflow_version: int,
    ) -> None: ...


class ClaimReviewService:
    def __init__(
        self,
        *,
        owner_id: int,
        repository: MedicalRepository,
        provider: ClaimsEvidenceProvider,
        policy: MedicalPolicy,
        prompt_version: str,
        schema_version: str,
        model_run_id: UUID,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._owner_id = owner_id
        self._repository = repository
        self._provider = provider
        self._policy = policy
        self._prompt_version = prompt_version
        self._schema_version = schema_version
        self._model_run_id = model_run_id
        self._clock = clock or (lambda: datetime.now(UTC))

    async def review(self, workflow_id: UUID) -> ClaimReview:
        context = await self._repository.load_context(
            owner_id=self._owner_id,
            workflow_id=workflow_id,
        )
        self._validate_context(context, workflow_id)
        assert context.workflow.version is not None

        classifications = await self._provider.classify_claims(
            ClaimsRequest(
                owner_id=self._owner_id,
                workflow_id=workflow_id,
                prompt_version=self._prompt_version,
                schema_version=self._schema_version,
                claims=tuple(claim.exact_text for claim in context.extraction.claims),
            )
        )
        classified = {item.exact_text: item.verdict for item in classifications.claims}
        now = self._clock()
        evidence: list[Evidence] = []
        for claim in context.extraction.claims:
            bound_sources = tuple(
                source
                for source in context.extraction.sources
                if source.id in claim.source_document_ids
            )
            evidence_verdict = await self._evidence_verdict(
                workflow_id=workflow_id,
                claim=claim,
                fragments=tuple(source.exact_excerpt for source in bound_sources),
            )
            verdict = _combine_verdicts(
                _normalize_provider_verdict(classified.get(claim.exact_text)),
                evidence_verdict,
            )
            risk = _risk_for(claim)
            evidence.extend(
                Evidence(
                    id=uuid4(),
                    claim_id=claim.id,
                    source_document_id=source.id,
                    source_url=source.url,
                    exact_excerpt=source.exact_excerpt,
                    excerpt_hash=source.excerpt_hash,
                    applicability=source.applicability,
                    limitations=source.limitations,
                    verdict=verdict,
                    risk=risk,
                    reviewed_at=now,
                    model_run_id=self._model_run_id,
                )
                for source in bound_sources
            )

        review = ClaimReview(
            id=uuid4(),
            owner_id=self._owner_id,
            workflow_id=workflow_id,
            workflow_version=context.workflow.version,
            extraction_hash=context.extraction.extraction_hash,
            draft_version_id=None,
            draft_hash=None,
            policy_version=self._policy.policy_version,
            model_run_id=self._model_run_id,
            reviewed_at=now,
            claims=context.extraction.claims,
            evidence=tuple(evidence),
        )
        decision = self._policy.can_draft(review)
        await self._repository.save_outcome(
            owner_id=self._owner_id,
            review=review,
            decision=decision,
            expected_workflow_version=context.workflow.version,
        )
        return review

    def _validate_context(
        self, context: ClaimReviewContext, workflow_id: UUID
    ) -> None:
        workflow = context.workflow
        extraction = context.extraction
        if workflow.owner_id != self._owner_id or extraction.owner_id != self._owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        if workflow.id != workflow_id or extraction.workflow_id != workflow_id:
            raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)
        if workflow.status is not WorkflowStatus.EXTRACTION_CONFIRMED:
            raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)
        if workflow.version is None or workflow.version != extraction.workflow_version:
            raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)
        if self._model_run_id != self._policy.active_model_run_id:
            raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)

    async def _evidence_verdict(
        self,
        *,
        workflow_id: UUID,
        claim: AtomicClaim,
        fragments: tuple[str, ...],
    ) -> EvidenceVerdict:
        if not fragments:
            return EvidenceVerdict.REVIEW_INCOMPLETE
        response = await self._provider.synthesize_evidence(
            EvidenceRequest(
                owner_id=self._owner_id,
                workflow_id=workflow_id,
                prompt_version=self._prompt_version,
                schema_version=self._schema_version,
                claim=claim.exact_text,
                evidence_fragments=fragments,
            )
        )
        return _normalize_provider_verdict(response.verdict)


def _normalize_provider_verdict(verdict: ClaimVerdict | None) -> EvidenceVerdict:
    if verdict is None:
        return EvidenceVerdict.REVIEW_INCOMPLETE
    return {
        ClaimVerdict.SUPPORTED: EvidenceVerdict.SUPPORTED,
        ClaimVerdict.REFUTED: EvidenceVerdict.REFUTED,
        ClaimVerdict.INSUFFICIENT: EvidenceVerdict.INSUFFICIENT,
        ClaimVerdict.MANUAL_REVIEW: EvidenceVerdict.MANUAL_REQUIRED,
    }[verdict]


def _combine_verdicts(
    classification: EvidenceVerdict, evidence: EvidenceVerdict
) -> EvidenceVerdict:
    precedence = (
        EvidenceVerdict.REVIEW_INCOMPLETE,
        EvidenceVerdict.MANUAL_REQUIRED,
        EvidenceVerdict.REFUTED,
        EvidenceVerdict.INSUFFICIENT,
        EvidenceVerdict.SUPPORTED,
    )
    return next(item for item in precedence if item in {classification, evidence})


def _risk_for(claim: AtomicClaim) -> RiskLevel:
    from bodrye_bot.domain.medical import ClaimType

    if claim.claim_type in {ClaimType.DIAGNOSIS, ClaimType.TREATMENT, ClaimType.DOSAGE}:
        return RiskLevel.RED
    if claim.claim_type in {ClaimType.RISK, ClaimType.PREVENTION, ClaimType.SAFETY}:
        return RiskLevel.YELLOW
    return RiskLevel.GREEN


__all__ = [
    "ClaimReviewContext",
    "ClaimReviewService",
    "ClaimsEvidenceProvider",
    "MedicalRepository",
]
