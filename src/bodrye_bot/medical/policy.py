from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bodrye_bot.domain.medical import (
    ClaimReview,
    ClaimType,
    DraftBinding,
    EvidenceVerdict,
    MedicalDecision,
    ReviewBlockingReason,
    RiskLevel,
)

_UNKNOWN_APPLICABILITY = {"", "unknown", "неизвестно", "не указано"}


@dataclass(frozen=True)
class MedicalReviewConfiguration:
    policy_version: str
    provider: str
    model: str
    claims_prompt_version: str
    claims_schema_version: str
    evidence_prompt_version: str
    evidence_schema_version: str
    validity_interval: timedelta

    def __post_init__(self) -> None:
        values = (
            self.policy_version,
            self.provider,
            self.model,
            self.claims_prompt_version,
            self.claims_schema_version,
            self.evidence_prompt_version,
            self.evidence_schema_version,
        )
        if any(not value or len(value) > 255 for value in values):
            raise ValueError("medical review configuration is incomplete")
        seconds = self.validity_seconds
        if seconds < 1 or seconds > 604_800:
            raise ValueError("medical review validity must be 1..604800 seconds")
        if f"ttl={seconds}" not in self.policy_version:
            raise ValueError("policy_version must bind the validity interval")

    @property
    def validity_seconds(self) -> int:
        return int(self.validity_interval.total_seconds())


class MedicalPolicy:
    def __init__(self, configuration: MedicalReviewConfiguration) -> None:
        self.configuration = configuration

    @property
    def policy_version(self) -> str:
        return self.configuration.policy_version

    def can_draft(self, review: ClaimReview, *, now: datetime) -> MedicalDecision:
        reasons: list[ReviewBlockingReason] = []
        config = self.configuration
        if review.policy_version != config.policy_version:
            reasons.append(ReviewBlockingReason.STALE_POLICY)
        if review.validity_seconds != config.validity_seconds:
            reasons.append(ReviewBlockingReason.STALE_POLICY)
        if review.reviewed_at > now:
            reasons.append(ReviewBlockingReason.REVIEW_IN_FUTURE)
        elif now - review.reviewed_at > config.validity_interval:
            reasons.append(ReviewBlockingReason.REVIEW_EXPIRED)
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
            if not _claim_shape_complete(claim) or claim.medical_uncertainty:
                reasons.append(ReviewBlockingReason.REVIEW_INCOMPLETE)
            items = evidence_by_claim[claim.id]
            if not items:
                reasons.append(ReviewBlockingReason.MISSING_PROVENANCE)
                continue
            if {item.source_document_id for item in items} != set(
                claim.source_document_ids
            ):
                reasons.append(ReviewBlockingReason.EXTRACTION_BINDING_MISMATCH)
            for item in items:
                if item.reviewed_at != review.reviewed_at:
                    reasons.append(ReviewBlockingReason.REVIEW_INCOMPLETE)
                if item.reviewed_at > now:
                    reasons.append(ReviewBlockingReason.REVIEW_IN_FUTURE)
                elif now - item.reviewed_at > config.validity_interval:
                    reasons.append(ReviewBlockingReason.REVIEW_EXPIRED)
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
                    reasons.append(ReviewBlockingReason.UNKNOWN_HIGH_RISK_APPLICABILITY)
                verdict_reason = _verdict_reason(item.verdict)
                if verdict_reason is not None:
                    reasons.append(verdict_reason)

        unique_reasons = tuple(dict.fromkeys(reasons))
        return MedicalDecision(allowed=not unique_reasons, reasons=unique_reasons)

    def can_approve(
        self,
        review: ClaimReview,
        draft: DraftBinding,
        *,
        now: datetime,
    ) -> MedicalDecision:
        reasons = list(self.can_draft(review, now=now).reasons)
        if (
            draft.owner_id != review.owner_id
            or draft.workflow_id != review.workflow_id
            or review.draft_version_id is None
            or review.draft_version_id != draft.draft_version_id
        ):
            reasons.append(ReviewBlockingReason.DRAFT_VERSION_MISMATCH)
        if review.draft_hash is None or review.draft_hash != draft.content_hash:
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


__all__ = ["MedicalPolicy", "MedicalReviewConfiguration"]
