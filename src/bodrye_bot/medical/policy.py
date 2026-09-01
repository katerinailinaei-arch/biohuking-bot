from __future__ import annotations

from uuid import UUID

from bodrye_bot.domain.medical import (
    ClaimReview,
    ClaimType,
    EvidenceVerdict,
    MedicalDecision,
    ReviewBlockingReason,
    RiskLevel,
)

_UNKNOWN_APPLICABILITY = {"", "unknown", "неизвестно", "не указано"}


class MedicalPolicy:
    def __init__(self, *, policy_version: str, active_model_run_id: UUID) -> None:
        if not policy_version or len(policy_version) > 64:
            raise ValueError("policy_version must be 1..64 characters")
        self.policy_version = policy_version
        self.active_model_run_id = active_model_run_id

    def can_draft(self, review: ClaimReview) -> MedicalDecision:
        reasons: list[ReviewBlockingReason] = []
        if review.policy_version != self.policy_version:
            reasons.append(ReviewBlockingReason.STALE_POLICY)
        if review.model_run_id != self.active_model_run_id:
            reasons.append(ReviewBlockingReason.STALE_MODEL_RUN)
        if not review.claims:
            reasons.append(ReviewBlockingReason.REVIEW_INCOMPLETE)

        claim_ids = {claim.id for claim in review.claims}
        evidence_by_claim = {
            claim_id: tuple(item for item in review.evidence if item.claim_id == claim_id)
            for claim_id in claim_ids
        }
        if any(item.claim_id not in claim_ids for item in review.evidence):
            reasons.append(ReviewBlockingReason.EXTRACTION_BINDING_MISMATCH)

        for claim in review.claims:
            if not _claim_shape_complete(claim):
                reasons.append(ReviewBlockingReason.REVIEW_INCOMPLETE)
            items = evidence_by_claim[claim.id]
            if not items:
                reasons.append(ReviewBlockingReason.MISSING_PROVENANCE)
                continue
            for item in items:
                if item.source_document_id not in claim.source_document_ids:
                    reasons.append(ReviewBlockingReason.EXTRACTION_BINDING_MISMATCH)
                if item.model_run_id != review.model_run_id:
                    reasons.append(ReviewBlockingReason.STALE_MODEL_RUN)
                if item.reviewed_at != review.reviewed_at:
                    reasons.append(ReviewBlockingReason.REVIEW_INCOMPLETE)
                if not item.exact_excerpt.strip():
                    reasons.append(ReviewBlockingReason.MISSING_EXACT_EXCERPT)
                if not _valid_source_url(item.source_url):
                    reasons.append(ReviewBlockingReason.MISSING_SOURCE_URL)
                if not item.applicability.strip() or not item.limitations.strip():
                    reasons.append(ReviewBlockingReason.REVIEW_INCOMPLETE)
                if item.risk is RiskLevel.RED:
                    reasons.append(ReviewBlockingReason.RED_RISK)
                if (
                    item.risk in {RiskLevel.YELLOW, RiskLevel.RED}
                    and item.applicability.strip().casefold() in _UNKNOWN_APPLICABILITY
                ):
                    reasons.append(
                        ReviewBlockingReason.UNKNOWN_HIGH_RISK_APPLICABILITY
                    )
                verdict_reason = _verdict_reason(item.verdict)
                if verdict_reason is not None:
                    reasons.append(verdict_reason)

        unique_reasons = tuple(dict.fromkeys(reasons))
        return MedicalDecision(allowed=not unique_reasons, reasons=unique_reasons)

    def can_approve(
        self, review: ClaimReview, draft_hash: str | None
    ) -> MedicalDecision:
        reasons = list(self.can_draft(review).reasons)
        if review.draft_version_id is None:
            reasons.append(ReviewBlockingReason.DRAFT_VERSION_MISMATCH)
        if review.draft_hash is None or review.draft_hash != draft_hash:
            reasons.append(ReviewBlockingReason.DRAFT_HASH_MISMATCH)
        unique_reasons = tuple(dict.fromkeys(reasons))
        return MedicalDecision(allowed=not unique_reasons, reasons=unique_reasons)


def _claim_shape_complete(claim: object) -> bool:
    from bodrye_bot.domain.medical import AtomicClaim

    if not isinstance(claim, AtomicClaim):
        return False
    if not (claim.exact_text.strip() and claim.population and claim.context and claim.modality):
        return False
    if claim.claim_type in {ClaimType.EFFECT, ClaimType.CAUSAL} and not claim.causality:
        return False
    if claim.claim_type is ClaimType.NUMERIC and not claim.numeric_value:
        return False
    return True


def _valid_source_url(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized.startswith("https://") or normalized.startswith("http://")


def _verdict_reason(verdict: EvidenceVerdict) -> ReviewBlockingReason | None:
    return {
        EvidenceVerdict.SUPPORTED: None,
        EvidenceVerdict.REFUTED: ReviewBlockingReason.REFUTED,
        EvidenceVerdict.INSUFFICIENT: ReviewBlockingReason.INSUFFICIENT,
        EvidenceVerdict.MANUAL_REQUIRED: ReviewBlockingReason.MANUAL_REQUIRED,
        EvidenceVerdict.REVIEW_INCOMPLETE: ReviewBlockingReason.REVIEW_INCOMPLETE,
    }[verdict]


__all__ = ["MedicalPolicy"]
