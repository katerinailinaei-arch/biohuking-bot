from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Never, Protocol
from uuid import UUID, uuid4

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimReview,
    ConfirmedExtraction,
    Evidence,
    EvidenceSource,
    EvidenceVerdict,
    MedicalDecision,
)
from bodrye_bot.domain.workflow import WorkflowState, WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy, MedicalReviewConfiguration
from bodrye_bot.ports.llm import (
    ClaimClassification,
    ClaimsRequest,
    ClaimsResponse,
    ClaimVerdict,
    EvidenceFragment,
    EvidenceRequest,
    EvidenceResponse,
    MedicalClaimInput,
    UsageReport,
)


class ClaimsEvidenceProvider(Protocol):
    provider_name: str
    model: str

    async def classify_claims(self, request: ClaimsRequest) -> ClaimsResponse: ...

    async def synthesize_evidence(self, request: EvidenceRequest) -> EvidenceResponse: ...

    async def estimate_or_report_usage(self, response_id: str) -> UsageReport: ...


@dataclass(frozen=True)
class ClaimReviewContext:
    workflow: WorkflowState
    extraction: ConfirmedExtraction


@dataclass(frozen=True)
class ReviewAttempt:
    id: UUID
    context: ClaimReviewContext
    pending_workflow_version: int


class MedicalRepository(Protocol):
    async def start_attempt(
        self,
        *,
        owner_id: int,
        workflow_id: UUID,
        started_at: datetime,
        lease_until: datetime,
    ) -> ReviewAttempt: ...

    async def record_provider_call(
        self,
        *,
        attempt_id: UUID,
        response_id: str,
        usage: UsageReport,
    ) -> UUID: ...

    async def save_outcome(
        self,
        *,
        owner_id: int,
        attempt_id: UUID,
        review: ClaimReview,
        decision: MedicalDecision,
        completed_at: datetime,
    ) -> None: ...

    async def fail_attempt(self, *, owner_id: int, attempt_id: UUID) -> None: ...


@dataclass(frozen=True)
class _EvidenceResult:
    claim: AtomicClaim
    source_id: UUID
    response: EvidenceResponse
    run_id: UUID
    verdict: EvidenceVerdict


class ClaimReviewService:
    def __init__(
        self,
        *,
        owner_id: int,
        repository: MedicalRepository,
        provider: ClaimsEvidenceProvider,
        policy: MedicalPolicy,
        configuration: MedicalReviewConfiguration,
        clock: Callable[[], datetime] | None = None,
        attempt_lease: timedelta = timedelta(minutes=5),
    ) -> None:
        if policy.configuration != configuration:
            raise ValueError("medical policy and service configuration differ")
        if (
            provider.provider_name != configuration.provider
            or provider.model != configuration.model
        ):
            raise ValueError("medical provider does not match active configuration")
        if attempt_lease <= timedelta(0) or attempt_lease > timedelta(hours=1):
            raise ValueError("medical attempt lease must be positive and at most one hour")
        self._owner_id = owner_id
        self._repository = repository
        self._provider = provider
        self._policy = policy
        self._configuration = configuration
        self._clock = clock or (lambda: datetime.now(UTC))
        self._attempt_lease = attempt_lease

    async def review(self, workflow_id: UUID) -> ClaimReview:
        started_at = self._trusted_now()
        attempt = await self._repository.start_attempt(
            owner_id=self._owner_id,
            workflow_id=workflow_id,
            started_at=started_at,
            lease_until=started_at + self._attempt_lease,
        )
        try:
            context = attempt.context
            self._validate_context(context, workflow_id)
            claim_inputs = tuple(_claim_input(claim) for claim in context.extraction.claims)
            classifications = await self._provider.classify_claims(
                ClaimsRequest(
                    owner_id=self._owner_id,
                    workflow_id=workflow_id,
                    prompt_version=self._configuration.claims_prompt_version,
                    schema_version=self._configuration.claims_schema_version,
                    claims=claim_inputs,
                )
            )
            classified = _validated_classifications(
                classifications,
                context.extraction.claims,
            )
            classification_run_id = await self._record_call(
                attempt.id,
                classifications.response_id,
                operation="classify_claims",
                prompt_version=self._configuration.claims_prompt_version,
                schema_version=self._configuration.claims_schema_version,
            )

            evidence_results: list[_EvidenceResult] = []
            source_lookup = {source.id: source for source in context.extraction.sources}
            for claim in context.extraction.claims:
                classification = classified[claim.id]
                for source_id in claim.source_document_ids:
                    source = source_lookup.get(source_id)
                    if source is None:
                        _incomplete("claim references absent confirmed source")
                    response = await self._provider.synthesize_evidence(
                        EvidenceRequest(
                            owner_id=self._owner_id,
                            workflow_id=workflow_id,
                            prompt_version=self._configuration.evidence_prompt_version,
                            schema_version=self._configuration.evidence_schema_version,
                            claim=_claim_input(claim),
                            evidence_fragment=EvidenceFragment(
                                source_document_id=source.id,
                                exact_excerpt=source.exact_excerpt,
                            ),
                        )
                    )
                    _validate_evidence_response(response, claim, source.id, classification)
                    run_id = await self._record_call(
                        attempt.id,
                        response.response_id,
                        operation="synthesize_evidence",
                        prompt_version=self._configuration.evidence_prompt_version,
                        schema_version=self._configuration.evidence_schema_version,
                    )
                    evidence_results.append(
                        _EvidenceResult(
                            claim=claim,
                            source_id=source.id,
                            response=response,
                            run_id=run_id,
                            verdict=_combine_verdicts(
                                _normalize_provider_verdict(classification.verdict),
                                _normalize_provider_verdict(response.verdict),
                            ),
                        )
                    )

            completed_at = self._trusted_now()
            evidence = tuple(
                _domain_evidence(result, source_lookup[result.source_id], completed_at)
                for result in evidence_results
            )
            extraction = context.extraction
            review = ClaimReview(
                id=uuid4(),
                owner_id=self._owner_id,
                workflow_id=workflow_id,
                workflow_version=extraction.workflow_version,
                extraction_hash=extraction.extraction_hash,
                draft_version_id=None,
                draft_hash=None,
                policy_version=self._configuration.policy_version,
                validity_seconds=self._configuration.validity_seconds,
                classification_run_id=classification_run_id,
                classification_response_id=classifications.response_id,
                reviewed_at=completed_at,
                claims=tuple(extraction.claims),
                evidence=evidence,
            )
            decision = self._policy.can_draft(review, now=completed_at)
            await self._repository.save_outcome(
                owner_id=self._owner_id,
                attempt_id=attempt.id,
                review=review,
                decision=decision,
                completed_at=completed_at,
            )
            return review
        except Exception:
            await self._repository.fail_attempt(
                owner_id=self._owner_id,
                attempt_id=attempt.id,
            )
            raise

    async def _record_call(
        self,
        attempt_id: UUID,
        response_id: str,
        *,
        operation: str,
        prompt_version: str,
        schema_version: str,
    ) -> UUID:
        usage = await self._provider.estimate_or_report_usage(response_id)
        if (
            usage.owner_id != self._owner_id
            or usage.operation != operation
            or usage.provider != self._configuration.provider
            or usage.model != self._configuration.model
            or usage.prompt_version != prompt_version
            or usage.schema_version != schema_version
            or usage.status != "succeeded"
            or usage.trace_id != response_id
        ):
            _incomplete("provider call metadata does not match active medical configuration")
        return await self._repository.record_provider_call(
            attempt_id=attempt_id,
            response_id=response_id,
            usage=usage,
        )

    def _trusted_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            _incomplete("trusted medical clock must return UTC")
        return value

    def _validate_context(self, context: ClaimReviewContext, workflow_id: UUID) -> None:
        workflow = context.workflow
        extraction = context.extraction
        if workflow.owner_id != self._owner_id or extraction.owner_id != self._owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        if workflow.id != workflow_id or extraction.workflow_id != workflow_id:
            _incomplete("medical workflow binding mismatch")
        if workflow.status is not WorkflowStatus.EXTRACTION_CONFIRMED:
            _incomplete("medical review requires confirmed extraction")
        if workflow.version is None or workflow.version != extraction.workflow_version:
            _incomplete("medical workflow version mismatch")


def _claim_input(claim: AtomicClaim) -> MedicalClaimInput:
    return MedicalClaimInput(
        claim_id=claim.id,
        exact_text=claim.exact_text,
        claim_type=claim.claim_type,
        population=claim.population,
        context=claim.context,
        causality=claim.causality,
        numeric_value=claim.numeric_value,
        modality=claim.modality,
        medical_uncertainty=claim.medical_uncertainty,
    )


def _validated_classifications(
    response: ClaimsResponse,
    claims: tuple[AtomicClaim, ...],
) -> dict[UUID, ClaimClassification]:
    expected = {claim.id: claim for claim in claims}
    actual_ids = [item.claim_id for item in response.claims]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
        _incomplete("provider claim identities are duplicate, missing, or extra")
    result = {item.claim_id: item for item in response.claims}
    for claim_id, item in result.items():
        if not _classification_matches_claim(item, expected[claim_id]):
            _incomplete("provider classification differs from confirmed claim semantics")
    return result


def _classification_matches_claim(item: ClaimClassification, claim: AtomicClaim) -> bool:
    return (
        item.claim_id == claim.id
        and item.exact_text == claim.exact_text
        and item.claim_type is claim.claim_type
        and item.population == claim.population
        and item.context == claim.context
        and item.causality == claim.causality
        and item.numeric_value == claim.numeric_value
        and item.modality == claim.modality
        and item.medical_uncertainty is claim.medical_uncertainty
    )


def _validate_evidence_response(
    response: EvidenceResponse,
    claim: AtomicClaim,
    source_id: UUID,
    classification: ClaimClassification,
) -> None:
    semantic_match = (
        response.claim_id == claim.id
        and response.source_document_id == source_id
        and response.exact_text == claim.exact_text
        and response.claim_type is claim.claim_type
        and response.population == claim.population
        and response.context == claim.context
        and response.causality == claim.causality
        and response.numeric_value == claim.numeric_value
        and response.modality == claim.modality
        and response.medical_uncertainty is claim.medical_uncertainty
        and response.risk is classification.risk
    )
    if not semantic_match:
        _incomplete("provider evidence differs from confirmed claim semantics")


def _domain_evidence(
    result: _EvidenceResult,
    source: EvidenceSource,
    completed_at: datetime,
) -> Evidence:
    response = result.response
    return Evidence(
        id=uuid4(),
        claim_id=result.claim.id,
        source_document_id=result.source_id,
        source_url=source.url,
        exact_excerpt=source.exact_excerpt,
        excerpt_hash=source.excerpt_hash,
        applicability=response.applicability,
        limitations=response.limitations,
        verdict=result.verdict,
        risk=response.risk,
        reviewed_at=completed_at,
        model_run_id=result.run_id,
        response_id=response.response_id,
    )


def _normalize_provider_verdict(verdict: ClaimVerdict) -> EvidenceVerdict:
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


def _incomplete(detail: str) -> Never:
    raise SafeError.for_code(
        SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE,
        developer_detail=detail,
    )


__all__ = [
    "ClaimReviewContext",
    "ClaimReviewService",
    "ClaimsEvidenceProvider",
    "MedicalRepository",
    "ReviewAttempt",
]
