from __future__ import annotations

from collections.abc import Mapping
from typing import Never
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import (
    Claim as ClaimModel,
)
from bodrye_bot.db.models import (
    ClaimReviewDecision as ClaimReviewDecisionModel,
)
from bodrye_bot.db.models import (
    ClaimSourceDocument as ClaimSourceDocumentModel,
)
from bodrye_bot.db.models import (
    ContentWorkflow,
    ProviderRun,
    Source,
    SourceDocument,
)
from bodrye_bot.db.models import (
    Evidence as EvidenceModel,
)
from bodrye_bot.db.models import (
    ExtractionConfirmation as ExtractionConfirmationModel,
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
from bodrye_bot.domain.workflow import Actor, WorkflowState, WorkflowStatus
from bodrye_bot.medical.policy import MedicalPolicy
from bodrye_bot.medical.review import ClaimReviewContext
from bodrye_bot.operations.audit import (
    AuditEntry,
    AuditEventType,
    AuditObjectType,
    SqlAlchemyAuditWriter,
)
from bodrye_bot.ports.repositories import ConcurrentUpdate


class SqlAlchemyMedicalRepository:
    """Short transaction boundaries around persisted extraction and review outcomes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
                    workflow.status is not WorkflowStatus.EXTRACTED
                    or workflow.version != expected_workflow_version
                ):
                    raise ConcurrentUpdate

                source_lookup = await _source_lookup(
                    session,
                    owner_id=extraction.owner_id,
                    source_document_ids={source.id for source in extraction.sources},
                )
                _validate_confirmation_sources(extraction, source_lookup)

                confirmation_id = uuid4()
                session.add(
                    ExtractionConfirmationModel(
                        id=confirmation_id,
                        owner_id=extraction.owner_id,
                        workflow_id=extraction.workflow_id,
                        workflow_version=extraction.workflow_version,
                        extraction_hash=extraction.extraction_hash,
                        confirmed_at=extraction.confirmed_at,
                    )
                )
                await session.flush()
                for claim in extraction.claims:
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
                            is_medical=True,
                            status="pending",
                        )
                    )
                    session.add_all(
                        ClaimSourceDocumentModel(
                            owner_id=extraction.owner_id,
                            claim_id=claim.id,
                            source_document_id=source_document_id,
                        )
                        for source_document_id in claim.source_document_ids
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

    async def load_context(
        self, *, owner_id: int, workflow_id: UUID
    ) -> ClaimReviewContext:
        async with self._session_factory() as session:
            return await _load_context(
                session,
                owner_id=owner_id,
                workflow_id=workflow_id,
                lock=False,
            )

    async def save_outcome(
        self,
        *,
        owner_id: int,
        review: ClaimReview,
        decision: MedicalDecision,
        expected_workflow_version: int,
    ) -> None:
        try:
            await self._save_outcome(
                owner_id=owner_id,
                review=review,
                decision=decision,
                expected_workflow_version=expected_workflow_version,
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
        review: ClaimReview,
        decision: MedicalDecision,
        expected_workflow_version: int,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                context = await _load_context(
                    session,
                    owner_id=owner_id,
                    workflow_id=review.workflow_id,
                    lock=True,
                )
                _validate_review_binding(
                    context=context,
                    review=review,
                    owner_id=owner_id,
                    expected_workflow_version=expected_workflow_version,
                )
                recomputed = MedicalPolicy(
                    policy_version=review.policy_version,
                    active_model_run_id=review.model_run_id,
                ).can_draft(review)
                if decision != recomputed:
                    _incomplete("medical decision does not match bound review values")

                provider_run = await session.scalar(
                    select(ProviderRun).where(
                        ProviderRun.id == review.model_run_id,
                        ProviderRun.owner_id == owner_id,
                        ProviderRun.workflow_id == review.workflow_id,
                    )
                )
                if provider_run is None or provider_run.status != "success":
                    _incomplete("review model run is absent, foreign, or unsuccessful")

                confirmation = await session.scalar(
                    select(ExtractionConfirmationModel).where(
                        ExtractionConfirmationModel.owner_id == owner_id,
                        ExtractionConfirmationModel.workflow_id == review.workflow_id,
                    )
                )
                if confirmation is None:
                    _incomplete("confirmed extraction is absent")
                _validate_review_evidence(context, review)

                pending_version = expected_workflow_version + 1
                final_version = expected_workflow_version + 2
                pending_result = await session.execute(
                    update(ContentWorkflow)
                    .where(
                        ContentWorkflow.id == review.workflow_id,
                        ContentWorkflow.owner_id == owner_id,
                        ContentWorkflow.status == WorkflowStatus.EXTRACTION_CONFIRMED,
                        ContentWorkflow.version == expected_workflow_version,
                    )
                    .values(
                        status=WorkflowStatus.CLAIMS_REVIEW_PENDING,
                        version=pending_version,
                    )
                )
                if pending_result.rowcount != 1:  # type: ignore[attr-defined]
                    raise ConcurrentUpdate
                await _record_transition(
                    session,
                    owner_id=owner_id,
                    workflow_id=review.workflow_id,
                    actor=Actor.SYSTEM,
                    previous_version=expected_workflow_version,
                    new_version=pending_version,
                    status=WorkflowStatus.CLAIMS_REVIEW_PENDING,
                )

                final_status = (
                    WorkflowStatus.CLAIMS_REVIEW_PASSED
                    if decision.allowed
                    else WorkflowStatus.CLAIMS_REVIEW_BLOCKED
                )
                final_result = await session.execute(
                    update(ContentWorkflow)
                    .where(
                        ContentWorkflow.id == review.workflow_id,
                        ContentWorkflow.owner_id == owner_id,
                        ContentWorkflow.status == WorkflowStatus.CLAIMS_REVIEW_PENDING,
                        ContentWorkflow.version == pending_version,
                    )
                    .values(status=final_status, version=final_version)
                )
                if final_result.rowcount != 1:  # type: ignore[attr-defined]
                    raise ConcurrentUpdate

                for evidence in review.evidence:
                    session.add(
                        EvidenceModel(
                            id=evidence.id,
                            owner_id=owner_id,
                            claim_id=evidence.claim_id,
                            source_document_id=evidence.source_document_id,
                            verdict=evidence.verdict.value,
                            risk=evidence.risk.value,
                            exact_excerpt=evidence.exact_excerpt,
                            excerpt_hash=evidence.excerpt_hash,
                            applicability=evidence.applicability,
                            limitations=evidence.limitations,
                            reviewed_at=evidence.reviewed_at,
                            review_model_run_id=evidence.model_run_id,
                        )
                    )
                await _update_claim_statuses(session, review)
                session.add(
                    ClaimReviewDecisionModel(
                        id=review.id,
                        owner_id=owner_id,
                        workflow_id=review.workflow_id,
                        extraction_confirmation_id=confirmation.id,
                        workflow_version=review.workflow_version,
                        extraction_hash=review.extraction_hash,
                        status="passed" if decision.allowed else "blocked",
                        blocking_reasons=[reason.value for reason in decision.reasons],
                        reviewed_at=review.reviewed_at,
                        policy_version=review.policy_version,
                        model_run_id=review.model_run_id,
                    )
                )
                await _record_transition(
                    session,
                    owner_id=owner_id,
                    workflow_id=review.workflow_id,
                    actor=Actor.SYSTEM,
                    previous_version=pending_version,
                    new_version=final_version,
                    status=final_status,
                )


async def _load_context(
    session: AsyncSession,
    *,
    owner_id: int,
    workflow_id: UUID,
    lock: bool,
) -> ClaimReviewContext:
    workflow = await _owned_workflow(
        session,
        owner_id=owner_id,
        workflow_id=workflow_id,
        lock=lock,
    )
    confirmation_query = select(ExtractionConfirmationModel).where(
        ExtractionConfirmationModel.owner_id == owner_id,
        ExtractionConfirmationModel.workflow_id == workflow_id,
    )
    if lock:
        confirmation_query = confirmation_query.with_for_update()
    confirmation = await session.scalar(confirmation_query)
    if confirmation is None:
        _incomplete("confirmed extraction is absent")

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
            select(ClaimSourceDocumentModel).where(
                ClaimSourceDocumentModel.owner_id == owner_id,
                ClaimSourceDocumentModel.claim_id.in_([claim.id for claim in claim_rows]),
            )
        )
    ).all()
    source_ids_by_claim: dict[UUID, list[UUID]] = {claim.id: [] for claim in claim_rows}
    for binding in bindings:
        source_ids_by_claim[binding.claim_id].append(binding.source_document_id)
    source_ids = {binding.source_document_id for binding in bindings}
    source_lookup = await _source_lookup(
        session,
        owner_id=owner_id,
        source_document_ids=source_ids,
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
            status=workflow.status,
            version=workflow.version,
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
    session: AsyncSession,
    *,
    owner_id: int,
    workflow_id: UUID,
    lock: bool,
) -> ContentWorkflow:
    query = select(ContentWorkflow).where(
        ContentWorkflow.id == workflow_id,
        ContentWorkflow.owner_id == owner_id,
    )
    if lock:
        query = query.with_for_update()
    workflow = await session.scalar(query)
    if workflow is None:
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    return workflow


async def _source_lookup(
    session: AsyncSession,
    *,
    owner_id: int,
    source_document_ids: set[UUID],
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
    return lookup


def _validate_confirmation_sources(
    extraction: ConfirmedExtraction,
    source_lookup: Mapping[UUID, tuple[SourceDocument, Source]],
) -> None:
    all_bound_ids = {
        source_id
        for claim in extraction.claims
        for source_id in claim.source_document_ids
    }
    declared = {source.id: source for source in extraction.sources}
    if set(declared) != all_bound_ids or set(source_lookup) != all_bound_ids:
        _incomplete("confirmed claims and source documents are not exactly bound")
    for source_id, supplied in declared.items():
        stored = _domain_source(*source_lookup[source_id])
        if supplied != stored:
            _incomplete("confirmed source provenance does not match stored document")


def _domain_source(document: SourceDocument, source: Source) -> EvidenceSource:
    excerpt = document.bounded_excerpt or ""
    applicability = _config_text(source.config_json, "applicability")
    limitations = _config_text(source.config_json, "limitations")
    return EvidenceSource(
        id=document.id,
        url=document.url,
        exact_excerpt=excerpt,
        excerpt_hash=content_hash(excerpt),
        applicability=applicability,
        limitations=limitations,
    )


def _config_text(config: Mapping[str, object], key: str) -> str:
    value = config.get(key, "")
    return value if isinstance(value, str) else ""


def _validate_review_binding(
    *,
    context: ClaimReviewContext,
    review: ClaimReview,
    owner_id: int,
    expected_workflow_version: int,
) -> None:
    workflow = context.workflow
    extraction = context.extraction
    if workflow.owner_id != owner_id or review.owner_id != owner_id:
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    if (
        workflow.status is not WorkflowStatus.EXTRACTION_CONFIRMED
        or workflow.version != expected_workflow_version
        or review.workflow_version != expected_workflow_version
        or extraction.workflow_version != expected_workflow_version
    ):
        raise ConcurrentUpdate
    if (
        review.workflow_id != workflow.id
        or review.extraction_hash != extraction.extraction_hash
        or review.claims != extraction.claims
    ):
        _incomplete("review does not match confirmed extraction")


def _validate_review_evidence(
    context: ClaimReviewContext,
    review: ClaimReview,
) -> None:
    source_lookup = {source.id: source for source in context.extraction.sources}
    expected_pairs = {
        (claim.id, source_id)
        for claim in context.extraction.claims
        for source_id in claim.source_document_ids
    }
    actual_pairs = {
        (evidence.claim_id, evidence.source_document_id) for evidence in review.evidence
    }
    if actual_pairs != expected_pairs:
        _incomplete("review evidence does not cover exact confirmed provenance")
    for evidence in review.evidence:
        source = source_lookup.get(evidence.source_document_id)
        if source is None or (
            evidence.source_url != source.url
            or evidence.exact_excerpt != source.exact_excerpt
            or evidence.excerpt_hash != source.excerpt_hash
            or evidence.applicability != source.applicability
            or evidence.limitations != source.limitations
            or evidence.model_run_id != review.model_run_id
            or evidence.reviewed_at != review.reviewed_at
        ):
            _incomplete("review evidence differs from persisted provenance")
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
            else EvidenceVerdict.REVIEW_INCOMPLETE.value
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
    writer = SqlAlchemyAuditWriter(session, ensure_active=lambda: None)
    await writer.record(
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
