from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Never
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import (
    Claim as ClaimModel,
)
from bodrye_bot.db.models import (
    ClaimReviewDecision as DecisionModel,
)
from bodrye_bot.db.models import (
    ClaimSourceDocument as ClaimSourceModel,
)
from bodrye_bot.db.models import (
    ContentWorkflow,
    DraftVersion,
    ProviderRun,
    Source,
    SourceDocument,
)
from bodrye_bot.db.models import (
    Evidence as EvidenceModel,
)
from bodrye_bot.db.models import (
    ExtractionConfirmation as ConfirmationModel,
)
from bodrye_bot.db.models import (
    MedicalReviewAttempt as AttemptModel,
)
from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.medical import (
    AtomicClaim,
    ClaimReview,
    ClaimType,
    ConfirmedExtraction,
    EvidenceSource,
    EvidenceVerdict,
    MedicalDecision,
)
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.domain.workflow import Actor, WorkflowState, WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy, MedicalReviewConfiguration
from bodrye_bot.medical.review import ClaimReviewContext, ReviewAttempt
from bodrye_bot.operations.audit import (
    AuditEntry,
    AuditEventType,
    AuditObjectType,
    SqlAlchemyAuditWriter,
)
from bodrye_bot.ports.llm import UsageReport
from bodrye_bot.ports.repositories import ConcurrentUpdate
from bodrye_bot.sources.catalog import SourceKind, SourceStatus


class SqlAlchemyMedicalRepository:
    """Owner-first durable medical review boundary with attempt fencing."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        configuration: MedicalReviewConfiguration | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._configuration = configuration

    async def confirm_extraction(
        self,
        extraction: ConfirmedExtraction,
        *,
        expected_workflow_version: int,
    ) -> None:
        if extraction.workflow_version != expected_workflow_version + 1:
            _incomplete("confirmed extraction workflow version is not the next version")
        async with self._session_factory() as session:
            async with session.begin():
                workflow = await _owned_workflow(
                    session,
                    owner_id=extraction.owner_id,
                    workflow_id=extraction.workflow_id,
                    lock=True,
                )
                if (
                    workflow.status
                    not in {WorkflowStatus.EXTRACTED, WorkflowStatus.CLAIMS_REVIEW_BLOCKED}
                    or workflow.version != expected_workflow_version
                ):
                    raise ConcurrentUpdate
                source_lookup = await _source_lookup(
                    session,
                    owner_id=extraction.owner_id,
                    source_document_ids={source.id for source in extraction.sources},
                )
                _validate_confirmation_sources(extraction, source_lookup)
                current = await session.scalar(
                    select(ConfirmationModel)
                    .where(
                        ConfirmationModel.owner_id == extraction.owner_id,
                        ConfirmationModel.workflow_id == extraction.workflow_id,
                        ConfirmationModel.is_current.is_(True),
                    )
                    .with_for_update()
                )
                number = 1
                if current is not None:
                    if workflow.status is not WorkflowStatus.CLAIMS_REVIEW_BLOCKED:
                        _incomplete("current extraction confirmation already exists")
                    current.is_current = False
                    current.invalidated_at = extraction.confirmed_at
                    number = current.confirmation_number + 1

                confirmation_id = uuid4()
                session.add(
                    ConfirmationModel(
                        id=confirmation_id,
                        owner_id=extraction.owner_id,
                        workflow_id=extraction.workflow_id,
                        workflow_version=extraction.workflow_version,
                        confirmation_number=number,
                        is_current=True,
                        invalidated_at=None,
                        extraction_hash=extraction.extraction_hash,
                        confirmed_at=extraction.confirmed_at,
                    )
                )
                await session.flush()
                for claim in tuple(extraction.claims):
                    session.add(
                        ClaimModel(
                            id=claim.id,
                            owner_id=extraction.owner_id,
                            workflow_id=extraction.workflow_id,
                            draft_version_id=None,
                            extraction_confirmation_id=confirmation_id,
                            exact_text=claim.exact_text,
                            claim_type=claim.claim_type.value,
                            population=claim.population,
                            context=claim.context,
                            causality=claim.causality,
                            numeric_value=claim.numeric_value,
                            modality=claim.modality,
                            medical_uncertainty=claim.medical_uncertainty,
                            is_medical=True,
                            status="pending",
                        )
                    )
                    session.add_all(
                        ClaimSourceModel(
                            owner_id=extraction.owner_id,
                            claim_id=claim.id,
                            source_document_id=source_document_id,
                        )
                        for source_document_id in tuple(claim.source_document_ids)
                    )
                workflow.status = WorkflowStatus.EXTRACTION_CONFIRMED
                workflow.version = extraction.workflow_version
                await _record_transition(
                    session,
                    owner_id=extraction.owner_id,
                    workflow_id=extraction.workflow_id,
                    actor=Actor.OWNER,
                    previous_version=expected_workflow_version,
                    new_version=extraction.workflow_version,
                    status=WorkflowStatus.EXTRACTION_CONFIRMED,
                )

    async def load_context(self, *, owner_id: int, workflow_id: UUID) -> ClaimReviewContext:
        async with self._session_factory() as session:
            return await _load_context(session, owner_id=owner_id, workflow_id=workflow_id)

    async def start_attempt(
        self,
        *,
        owner_id: int,
        workflow_id: UUID,
        started_at: datetime,
        lease_until: datetime,
    ) -> ReviewAttempt:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    workflow = await _owned_workflow(
                        session, owner_id=owner_id, workflow_id=workflow_id, lock=True
                    )
                    context = await _load_context(
                        session, owner_id=owner_id, workflow_id=workflow_id
                    )
                    active = await session.scalar(
                        select(AttemptModel)
                        .where(
                            AttemptModel.owner_id == owner_id,
                            AttemptModel.workflow_id == workflow_id,
                            AttemptModel.status == "processing",
                        )
                        .with_for_update()
                    )
                    if active is not None and active.lease_until > started_at:
                        _incomplete("medical review attempt is already active")
                    if active is not None:
                        active.status = "failed"
                        active.completed_at = started_at
                        active.failure_class = "lease_expired"

                    if workflow.status is WorkflowStatus.EXTRACTION_CONFIRMED:
                        if workflow.version != context.extraction.workflow_version:
                            raise ConcurrentUpdate
                        base_version = workflow.version
                        pending_version = base_version + 1
                        workflow.status = WorkflowStatus.CLAIMS_REVIEW_PENDING
                        workflow.version = pending_version
                        await _record_transition(
                            session,
                            owner_id=owner_id,
                            workflow_id=workflow_id,
                            actor=Actor.SYSTEM,
                            previous_version=base_version,
                            new_version=pending_version,
                            status=WorkflowStatus.CLAIMS_REVIEW_PENDING,
                        )
                    elif workflow.status is WorkflowStatus.CLAIMS_REVIEW_PENDING and active:
                        base_version = active.base_workflow_version
                        pending_version = active.pending_workflow_version
                        if workflow.version != pending_version:
                            raise ConcurrentUpdate
                    else:
                        _incomplete("medical review cannot start from current workflow state")

                    attempt_id = uuid4()
                    confirmation = await _current_confirmation(
                        session, owner_id=owner_id, workflow_id=workflow_id
                    )
                    session.add(
                        AttemptModel(
                            id=attempt_id,
                            owner_id=owner_id,
                            workflow_id=workflow_id,
                            extraction_confirmation_id=confirmation.id,
                            base_workflow_version=base_version,
                            pending_workflow_version=pending_version,
                            status="processing",
                            started_at=started_at,
                            lease_until=lease_until,
                        )
                    )
                    return ReviewAttempt(
                        id=attempt_id,
                        context=context,
                        pending_workflow_version=pending_version,
                    )
        except (SafeError, ConcurrentUpdate):
            raise
        except IntegrityError:
            _incomplete("medical review attempt lost concurrency race")

    async def record_provider_call(
        self,
        *,
        attempt_id: UUID,
        response_id: str,
        usage: UsageReport,
    ) -> UUID:
        try:
            run_id = UUID(hex=response_id)
            async with self._session_factory() as session:
                async with session.begin():
                    attempt = await _owned_attempt(
                        session, owner_id=usage.owner_id, attempt_id=attempt_id, lock=True
                    )
                    if attempt.status != "processing":
                        raise ConcurrentUpdate
                    if (
                        usage.workflow_id != attempt.workflow_id
                        or usage.trace_id != response_id
                        or usage.status != "succeeded"
                    ):
                        _incomplete("provider usage does not match medical attempt")
                    session.add(
                        ProviderRun(
                            id=run_id,
                            owner_id=usage.owner_id,
                            workflow_id=attempt.workflow_id,
                            medical_attempt_id=attempt.id,
                            response_id=response_id,
                            operation=usage.operation,
                            provider=usage.provider,
                            model=usage.model,
                            status="success",
                            prompt_version=usage.prompt_version,
                            schema_version=usage.schema_version,
                            provider_request_id=usage.provider_request_id,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            duration_ms=usage.latency_ms,
                            error_class=usage.error_class,
                        )
                    )
            return run_id
        except (SafeError, ConcurrentUpdate):
            raise
        except (IntegrityError, ValueError):
            _incomplete("provider response identity is invalid or reused")

    async def save_outcome(
        self,
        *,
        owner_id: int,
        attempt_id: UUID,
        review: ClaimReview,
        decision: MedicalDecision,
        completed_at: datetime,
    ) -> None:
        try:
            await self._save_outcome(
                owner_id=owner_id,
                attempt_id=attempt_id,
                review=review,
                decision=decision,
                completed_at=completed_at,
            )
        except (SafeError, ConcurrentUpdate):
            raise
        except Exception as exc:
            raise SafeError.for_code(
                SafeErrorCode.INTERNAL_ERROR,
                developer_detail=f"medical review persistence failed: {type(exc).__name__}",
            ) from None

    async def _save_outcome(
        self,
        *,
        owner_id: int,
        attempt_id: UUID,
        review: ClaimReview,
        decision: MedicalDecision,
        completed_at: datetime,
    ) -> None:
        config = self._configuration
        if config is None:
            _incomplete("active medical configuration is unavailable")
        async with self._session_factory() as session:
            async with session.begin():
                attempt = await _owned_attempt(
                    session, owner_id=owner_id, attempt_id=attempt_id, lock=True
                )
                if attempt.status != "processing" or completed_at > attempt.lease_until:
                    raise ConcurrentUpdate
                workflow = await _owned_workflow(
                    session,
                    owner_id=owner_id,
                    workflow_id=attempt.workflow_id,
                    lock=True,
                )
                context = await _load_context(
                    session, owner_id=owner_id, workflow_id=attempt.workflow_id
                )
                _validate_review_binding(context, review, owner_id, attempt)
                _validate_config_binding(review, config)
                recomputed = MedicalPolicy(config).can_draft(review, now=completed_at)
                if decision != recomputed:
                    _incomplete("medical decision does not match trusted active policy")
                await _validate_provider_runs(session, owner_id, attempt, review, config)
                await _validate_review_evidence(session, context, review)
                if (
                    workflow.status is not WorkflowStatus.CLAIMS_REVIEW_PENDING
                    or workflow.version != attempt.pending_workflow_version
                ):
                    raise ConcurrentUpdate

                confirmation = await _current_confirmation(
                    session, owner_id=owner_id, workflow_id=review.workflow_id
                )
                decision_row = DecisionModel(
                    id=review.id,
                    owner_id=owner_id,
                    workflow_id=review.workflow_id,
                    extraction_confirmation_id=confirmation.id,
                    attempt_id=attempt.id,
                    workflow_version=review.workflow_version,
                    extraction_hash=review.extraction_hash,
                    status="passed" if decision.allowed else "blocked",
                    blocking_reasons=[reason.value for reason in decision.reasons],
                    reviewed_at=review.reviewed_at,
                    policy_version=review.policy_version,
                    validity_seconds=review.validity_seconds,
                    model_run_id=review.classification_run_id,
                    classification_response_id=review.classification_response_id,
                    draft_version_id=review.draft_version_id,
                    draft_hash=review.draft_hash,
                )
                session.add(decision_row)
                await session.flush()
                for item in tuple(review.evidence):
                    session.add(
                        EvidenceModel(
                            id=item.id,
                            owner_id=owner_id,
                            claim_id=item.claim_id,
                            source_document_id=item.source_document_id,
                            review_decision_id=review.id,
                            response_id=item.response_id,
                            verdict=item.verdict.value,
                            risk=item.risk.value,
                            exact_excerpt=item.exact_excerpt,
                            excerpt_hash=item.excerpt_hash,
                            applicability=item.applicability,
                            limitations=item.limitations,
                            reviewed_at=item.reviewed_at,
                            review_model_run_id=item.model_run_id,
                        )
                    )
                await _update_claim_statuses(session, review)
                final_status = (
                    WorkflowStatus.CLAIMS_REVIEW_PASSED
                    if decision.allowed
                    else WorkflowStatus.CLAIMS_REVIEW_BLOCKED
                )
                final_version = attempt.pending_workflow_version + 1
                workflow.status = final_status
                workflow.version = final_version
                attempt.status = "completed"
                attempt.completed_at = completed_at
                await _record_transition(
                    session,
                    owner_id=owner_id,
                    workflow_id=review.workflow_id,
                    actor=Actor.SYSTEM,
                    previous_version=attempt.pending_workflow_version,
                    new_version=final_version,
                    status=final_status,
                )

    async def fail_attempt(self, *, owner_id: int, attempt_id: UUID) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                attempt = await _owned_attempt(
                    session, owner_id=owner_id, attempt_id=attempt_id, lock=True
                )
                if attempt.status != "processing":
                    return
                workflow = await _owned_workflow(
                    session,
                    owner_id=owner_id,
                    workflow_id=attempt.workflow_id,
                    lock=True,
                )
                attempt.status = "failed"
                attempt.failure_class = "review_failed"
                if (
                    workflow.status is WorkflowStatus.CLAIMS_REVIEW_PENDING
                    and workflow.version == attempt.pending_workflow_version
                ):
                    previous_version = workflow.version
                    workflow.status = WorkflowStatus.CLAIMS_REVIEW_BLOCKED
                    workflow.version += 1
                    await _record_transition(
                        session,
                        owner_id=owner_id,
                        workflow_id=attempt.workflow_id,
                        actor=Actor.SYSTEM,
                        previous_version=previous_version,
                        new_version=workflow.version,
                        status=WorkflowStatus.CLAIMS_REVIEW_BLOCKED,
                    )

    async def bind_current_draft(
        self,
        *,
        owner_id: int,
        workflow_id: UUID,
        review_id: UUID,
        draft_version_id: UUID,
        draft_hash: str,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                workflow = await _owned_workflow(
                    session, owner_id=owner_id, workflow_id=workflow_id, lock=True
                )
                draft = await session.scalar(
                    select(DraftVersion).where(
                        DraftVersion.id == draft_version_id,
                        DraftVersion.owner_id == owner_id,
                        DraftVersion.workflow_id == workflow_id,
                        DraftVersion.body_hash == draft_hash,
                    )
                )
                decision = await session.scalar(
                    select(DecisionModel).where(
                        DecisionModel.id == review_id,
                        DecisionModel.owner_id == owner_id,
                        DecisionModel.workflow_id == workflow_id,
                        DecisionModel.status == "passed",
                    )
                )
                if (
                    draft is None
                    or decision is None
                    or workflow.current_version_id != draft_version_id
                ):
                    _incomplete("draft is not the exact current owner workflow version")
                if decision.draft_version_id is not None and (
                    decision.draft_version_id != draft_version_id
                    or decision.draft_hash != draft_hash
                ):
                    _incomplete("medical review is already bound to another draft version")
                decision.draft_version_id = draft_version_id
                decision.draft_hash = draft_hash


async def _load_context(
    session: AsyncSession, *, owner_id: int, workflow_id: UUID
) -> ClaimReviewContext:
    workflow = await _owned_workflow(
        session, owner_id=owner_id, workflow_id=workflow_id, lock=False
    )
    confirmation = await _current_confirmation(
        session, owner_id=owner_id, workflow_id=workflow_id
    )
    claim_rows = (
        await session.scalars(
            select(ClaimModel)
            .where(
                ClaimModel.owner_id == owner_id,
                ClaimModel.workflow_id == workflow_id,
                ClaimModel.extraction_confirmation_id == confirmation.id,
            )
            .order_by(ClaimModel.created_at, ClaimModel.id)
        )
    ).all()
    bindings = (
        await session.scalars(
            select(ClaimSourceModel).where(
                ClaimSourceModel.owner_id == owner_id,
                ClaimSourceModel.claim_id.in_([claim.id for claim in claim_rows]),
            )
        )
    ).all()
    source_ids_by_claim: dict[UUID, list[UUID]] = {row.id: [] for row in claim_rows}
    for binding in bindings:
        source_ids_by_claim[binding.claim_id].append(binding.source_document_id)
    source_lookup = await _source_lookup(
        session,
        owner_id=owner_id,
        source_document_ids={binding.source_document_id for binding in bindings},
    )
    claims = tuple(
        AtomicClaim(
            id=row.id,
            exact_text=row.exact_text,
            claim_type=ClaimType(row.claim_type),
            population=row.population,
            context=row.context,
            causality=row.causality,
            numeric_value=row.numeric_value,
            modality=row.modality,
            medical_uncertainty=row.medical_uncertainty,
            source_document_ids=tuple(sorted(source_ids_by_claim[row.id], key=str)),
        )
        for row in claim_rows
    )
    sources = tuple(
        _domain_source(document, source)
        for _, (document, source) in sorted(source_lookup.items(), key=lambda item: str(item[0]))
    )
    return ClaimReviewContext(
        workflow=WorkflowState(
            id=workflow.id,
            owner_id=workflow.owner_id,
            status=WorkflowStatus.EXTRACTION_CONFIRMED,
            version=confirmation.workflow_version,
        ),
        extraction=ConfirmedExtraction(
            owner_id=owner_id,
            workflow_id=workflow_id,
            workflow_version=confirmation.workflow_version,
            extraction_hash=confirmation.extraction_hash,
            confirmed_at=confirmation.confirmed_at,
            claims=claims,
            sources=sources,
        ),
    )


async def _owned_workflow(
    session: AsyncSession, *, owner_id: int, workflow_id: UUID, lock: bool
) -> ContentWorkflow:
    query = select(ContentWorkflow).where(
        ContentWorkflow.id == workflow_id, ContentWorkflow.owner_id == owner_id
    )
    if lock:
        query = query.with_for_update()
    workflow = await session.scalar(query)
    if workflow is None:
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    return workflow


async def _owned_attempt(
    session: AsyncSession, *, owner_id: int, attempt_id: UUID, lock: bool
) -> AttemptModel:
    query = select(AttemptModel).where(
        AttemptModel.id == attempt_id, AttemptModel.owner_id == owner_id
    )
    if lock:
        query = query.with_for_update()
    attempt = await session.scalar(query)
    if attempt is None:
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    return attempt


async def _current_confirmation(
    session: AsyncSession, *, owner_id: int, workflow_id: UUID
) -> ConfirmationModel:
    confirmation = await session.scalar(
        select(ConfirmationModel).where(
            ConfirmationModel.owner_id == owner_id,
            ConfirmationModel.workflow_id == workflow_id,
            ConfirmationModel.is_current.is_(True),
        )
    )
    if confirmation is None:
        _incomplete("current confirmed extraction is absent")
    return confirmation


async def _source_lookup(
    session: AsyncSession, *, owner_id: int, source_document_ids: set[UUID]
) -> dict[UUID, tuple[SourceDocument, Source]]:
    if not source_document_ids:
        return {}
    rows = (
        await session.execute(
            select(SourceDocument, Source)
            .join(
                Source,
                (Source.id == SourceDocument.source_id)
                & (Source.owner_id == SourceDocument.owner_id),
            )
            .where(
                SourceDocument.owner_id == owner_id,
                SourceDocument.id.in_(source_document_ids),
            )
        )
    ).all()
    lookup = {document.id: (document, source) for document, source in rows}
    if set(lookup) != source_document_ids:
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    for document, source in lookup.values():
        _validate_source_authority(document, source)
    return lookup


def _validate_source_authority(document: SourceDocument, source: Source) -> None:
    roles = set(source.roles)
    config = source.config_json
    allowed_hosts = config.get("allowed_hosts")
    host = (urlsplit(document.url).hostname or "").casefold()
    host_values = (
        {str(item).casefold() for item in allowed_hosts}
        if isinstance(allowed_hosts, list)
        else set()
    )
    host_allowed = any(host == item or host.endswith(f".{item}") for item in host_values)
    if (
        SourceRole.EVIDENCE.value not in roles
        or source.source_type == SourceKind.TELEGRAM_MANUAL.value
        or source.status != SourceStatus.ACTIVE.value
        or config.get("catalog_current") is not True
        or not isinstance(config.get("registry_version"), str)
        or not config.get("registry_version")
        or not host_allowed
        or document.fetch_status != "available"
        or not (document.bounded_excerpt or "").strip()
    ):
        _incomplete("source document is not current authorized evidence")


def _validate_confirmation_sources(
    extraction: ConfirmedExtraction,
    source_lookup: Mapping[UUID, tuple[SourceDocument, Source]],
) -> None:
    all_bound_ids = {
        source_id for claim in extraction.claims for source_id in claim.source_document_ids
    }
    declared = {source.id: source for source in extraction.sources}
    if set(declared) != all_bound_ids or set(source_lookup) != all_bound_ids:
        _incomplete("confirmed claims and source documents are not exactly bound")
    for source_id, supplied in declared.items():
        if supplied != _domain_source(*source_lookup[source_id]):
            _incomplete("confirmed source provenance differs from active catalog document")


def _domain_source(document: SourceDocument, source: Source) -> EvidenceSource:
    _validate_source_authority(document, source)
    excerpt = document.bounded_excerpt or ""
    return EvidenceSource(
        id=document.id,
        url=document.url,
        exact_excerpt=excerpt,
        excerpt_hash=content_hash(excerpt),
        catalog_version=str(source.config_json["registry_version"]),
    )


def _validate_review_binding(
    context: ClaimReviewContext,
    review: ClaimReview,
    owner_id: int,
    attempt: AttemptModel,
) -> None:
    if review.owner_id != owner_id or context.workflow.owner_id != owner_id:
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    if (
        review.workflow_id != attempt.workflow_id
        or review.workflow_version != attempt.base_workflow_version
        or review.workflow_version != context.extraction.workflow_version
        or review.extraction_hash != context.extraction.extraction_hash
        or review.claims != context.extraction.claims
    ):
        _incomplete("review differs from fenced confirmed extraction")


def _validate_config_binding(
    review: ClaimReview, config: MedicalReviewConfiguration
) -> None:
    if (
        review.policy_version != config.policy_version
        or review.validity_seconds != config.validity_seconds
    ):
        _incomplete("review policy or validity differs from active configuration")


async def _validate_provider_runs(
    session: AsyncSession,
    owner_id: int,
    attempt: AttemptModel,
    review: ClaimReview,
    config: MedicalReviewConfiguration,
) -> None:
    await _validate_run(
        session,
        owner_id,
        attempt,
        review.classification_run_id,
        review.classification_response_id,
        "classify_claims",
        config.provider,
        config.model,
        config.claims_prompt_version,
        config.claims_schema_version,
    )
    for item in review.evidence:
        await _validate_run(
            session,
            owner_id,
            attempt,
            item.model_run_id,
            item.response_id,
            "synthesize_evidence",
            config.provider,
            config.model,
            config.evidence_prompt_version,
            config.evidence_schema_version,
        )


async def _validate_run(
    session: AsyncSession,
    owner_id: int,
    attempt: AttemptModel,
    run_id: UUID,
    response_id: str,
    operation: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> None:
    run = await session.scalar(
        select(ProviderRun).where(
            ProviderRun.id == run_id,
            ProviderRun.owner_id == owner_id,
            ProviderRun.workflow_id == attempt.workflow_id,
            ProviderRun.medical_attempt_id == attempt.id,
        )
    )
    if run is None or (
        run.response_id != response_id
        or run.operation != operation
        or run.provider != provider
        or run.model != model
        or run.prompt_version != prompt_version
        or run.schema_version != schema_version
        or run.status != "success"
    ):
        _incomplete("provider run does not match actual active review call")


async def _validate_review_evidence(
    session: AsyncSession, context: ClaimReviewContext, review: ClaimReview
) -> None:
    fresh = await _source_lookup(
        session,
        owner_id=review.owner_id,
        source_document_ids={source.id for source in context.extraction.sources},
    )
    source_lookup = {source.id: source for source in context.extraction.sources}
    expected_pairs = {
        (claim.id, source_id)
        for claim in context.extraction.claims
        for source_id in claim.source_document_ids
    }
    actual_pairs = [
        (item.claim_id, item.source_document_id) for item in tuple(review.evidence)
    ]
    if len(actual_pairs) != len(set(actual_pairs)) or set(actual_pairs) != expected_pairs:
        _incomplete("review evidence is not a unique exact provenance cover")
    for item in review.evidence:
        source = source_lookup.get(item.source_document_id)
        if source is None or source != _domain_source(*fresh[item.source_document_id]) or (
            item.source_url != source.url
            or item.exact_excerpt != source.exact_excerpt
            or item.excerpt_hash != source.excerpt_hash
            or item.reviewed_at != review.reviewed_at
        ):
            _incomplete("review evidence differs from current authorized provenance")


async def _update_claim_statuses(session: AsyncSession, review: ClaimReview) -> None:
    precedence = {
        EvidenceVerdict.SUPPORTED: 0,
        EvidenceVerdict.INSUFFICIENT: 1,
        EvidenceVerdict.REFUTED: 2,
        EvidenceVerdict.MANUAL_REQUIRED: 3,
        EvidenceVerdict.REVIEW_INCOMPLETE: 4,
    }
    for claim in review.claims:
        verdicts = [item.verdict for item in review.evidence if item.claim_id == claim.id]
        status = (
            max(verdicts, key=precedence.__getitem__).value
            if verdicts
            else "review_incomplete"
        )
        await session.execute(
            update(ClaimModel)
            .where(ClaimModel.id == claim.id, ClaimModel.owner_id == review.owner_id)
            .values(status=status)
        )


async def _record_transition(
    session: AsyncSession,
    *,
    owner_id: int,
    workflow_id: UUID,
    actor: Actor,
    previous_version: int,
    new_version: int,
    status: WorkflowStatus,
) -> None:
    await SqlAlchemyAuditWriter(session, ensure_active=lambda: None).record(
        AuditEntry(
            owner_id=owner_id,
            workflow_id=workflow_id,
            event_type=AuditEventType.WORKFLOW_STATE_CHANGED,
            actor=actor,
            object_type=AuditObjectType.WORKFLOW,
            object_id=workflow_id,
            metadata={
                "previous_version": previous_version,
                "new_version": new_version,
                "status": status.value,
            },
        )
    )


def _incomplete(detail: str) -> Never:
    raise SafeError.for_code(
        SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE,
        developer_detail=detail,
    )


__all__ = ["SqlAlchemyMedicalRepository"]
