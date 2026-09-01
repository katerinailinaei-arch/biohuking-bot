from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Never
from uuid import UUID

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ClaimType(StrEnum):
    EFFECT = "effect"
    CAUSAL = "causal"
    ASSOCIATION = "association"
    RISK = "risk"
    NUMERIC = "numeric"
    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"
    DOSAGE = "dosage"
    PREVENTION = "prevention"
    SAFETY = "safety"


class EvidenceVerdict(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INSUFFICIENT = "insufficient"
    MANUAL_REQUIRED = "manual_required"
    REVIEW_INCOMPLETE = "review_incomplete"


class RiskLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class ReviewBlockingReason(StrEnum):
    RED_RISK = "red_risk"
    REFUTED = "refuted"
    INSUFFICIENT = "insufficient"
    MANUAL_REQUIRED = "manual_required"
    REVIEW_INCOMPLETE = "review_incomplete"
    MISSING_PROVENANCE = "missing_provenance"
    MISSING_EXACT_EXCERPT = "missing_exact_excerpt"
    MISSING_SOURCE_URL = "missing_source_url"
    STALE_POLICY = "stale_policy"
    STALE_MODEL_RUN = "stale_model_run"
    UNKNOWN_HIGH_RISK_APPLICABILITY = "unknown_high_risk_applicability"
    WORKFLOW_BINDING_MISMATCH = "workflow_binding_mismatch"
    EXTRACTION_BINDING_MISMATCH = "extraction_binding_mismatch"
    DRAFT_VERSION_MISMATCH = "draft_version_mismatch"
    DRAFT_HASH_MISMATCH = "draft_hash_mismatch"


@dataclass(frozen=True)
class AtomicClaim:
    id: UUID
    exact_text: str = field(repr=False)
    claim_type: ClaimType
    population: str | None = field(default=None, repr=False)
    context: str | None = field(default=None, repr=False)
    causality: str | None = field(default=None, repr=False)
    numeric_value: str | None = field(default=None, repr=False)
    modality: str | None = field(default=None, repr=False)
    source_document_ids: tuple[UUID, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not self.exact_text.strip() or len(self.exact_text) > 3_800:
            _incomplete("claim wording is empty or exceeds 3800 characters")
        for name, value in (
            ("population", self.population),
            ("context", self.context),
            ("causality", self.causality),
            ("numeric_value", self.numeric_value),
            ("modality", self.modality),
        ):
            if value is not None and len(value) > 2_000:
                _incomplete(f"claim {name} exceeds 2000 characters")
        if not self.source_document_ids or len(self.source_document_ids) > 64:
            _incomplete("claim source document binding is empty or exceeds 64 items")
        if len(set(self.source_document_ids)) != len(self.source_document_ids):
            _incomplete("claim source document binding contains duplicates")


@dataclass(frozen=True)
class EvidenceSource:
    id: UUID
    url: str = field(repr=False)
    exact_excerpt: str = field(repr=False)
    excerpt_hash: str = field(repr=False)
    applicability: str = field(repr=False)
    limitations: str = field(repr=False)

    def __post_init__(self) -> None:
        _validate_bounded_evidence(
            exact_excerpt=self.exact_excerpt,
            excerpt_hash=self.excerpt_hash,
            applicability=self.applicability,
            limitations=self.limitations,
        )
        if len(self.url) > 2_048:
            _incomplete("source URL exceeds 2048 characters")


@dataclass(frozen=True)
class Evidence:
    id: UUID
    claim_id: UUID
    source_document_id: UUID
    source_url: str = field(repr=False)
    exact_excerpt: str = field(repr=False)
    excerpt_hash: str = field(repr=False)
    applicability: str = field(repr=False)
    limitations: str = field(repr=False)
    verdict: EvidenceVerdict
    risk: RiskLevel
    reviewed_at: datetime
    model_run_id: UUID

    def __post_init__(self) -> None:
        _validate_bounded_evidence(
            exact_excerpt=self.exact_excerpt,
            excerpt_hash=self.excerpt_hash,
            applicability=self.applicability,
            limitations=self.limitations,
        )
        if len(self.source_url) > 2_048:
            _incomplete("source URL exceeds 2048 characters")
        _require_utc(self.reviewed_at, "evidence reviewed_at")


@dataclass(frozen=True)
class ConfirmedExtraction:
    owner_id: int
    workflow_id: UUID
    workflow_version: int
    extraction_hash: str = field(repr=False)
    confirmed_at: datetime
    claims: tuple[AtomicClaim, ...] = field(repr=False)
    sources: tuple[EvidenceSource, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if self.workflow_version < 1 or _SHA256.fullmatch(self.extraction_hash) is None:
            _incomplete("invalid confirmed extraction binding")
        if not self.claims or len(self.claims) > 256 or len(self.sources) > 64:
            _incomplete("confirmed extraction has invalid collection bounds")
        if len({claim.id for claim in self.claims}) != len(self.claims):
            _incomplete("confirmed extraction contains duplicate claim ids")
        if len({source.id for source in self.sources}) != len(self.sources):
            _incomplete("confirmed extraction contains duplicate source ids")
        _require_utc(self.confirmed_at, "extraction confirmed_at")
        expected_hash = confirmed_extraction_hash(
            owner_id=self.owner_id,
            workflow_id=self.workflow_id,
            workflow_version=self.workflow_version,
            claims=self.claims,
            sources=self.sources,
        )
        if self.extraction_hash != expected_hash:
            _incomplete("confirmed extraction hash does not match exact payload")


@dataclass(frozen=True)
class ClaimReview:
    id: UUID
    owner_id: int
    workflow_id: UUID
    workflow_version: int
    extraction_hash: str = field(repr=False)
    draft_version_id: UUID | None
    draft_hash: str | None = field(repr=False)
    policy_version: str
    model_run_id: UUID
    reviewed_at: datetime
    claims: tuple[AtomicClaim, ...] = field(repr=False)
    evidence: tuple[Evidence, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if self.workflow_version < 1 or _SHA256.fullmatch(self.extraction_hash) is None:
            _incomplete("invalid review workflow or extraction binding")
        if not self.policy_version or len(self.policy_version) > 64:
            _incomplete("invalid medical policy version")
        if self.draft_hash is not None and _SHA256.fullmatch(self.draft_hash) is None:
            _incomplete("invalid draft hash")
        if len(self.claims) > 256 or len(self.evidence) > 16_384:
            _incomplete("review collections exceed bounds")
        _require_utc(self.reviewed_at, "review reviewed_at")


@dataclass(frozen=True)
class MedicalDecision:
    allowed: bool
    reasons: tuple[ReviewBlockingReason, ...]


def confirmed_extraction_hash(
    *,
    owner_id: int,
    workflow_id: UUID,
    workflow_version: int,
    claims: tuple[AtomicClaim, ...],
    sources: tuple[EvidenceSource, ...],
) -> str:
    payload = {
        "owner_id": owner_id,
        "workflow_id": str(workflow_id),
        "workflow_version": workflow_version,
        "claims": [
            {
                "id": str(claim.id),
                "exact_text": claim.exact_text,
                "claim_type": claim.claim_type.value,
                "population": claim.population,
                "context": claim.context,
                "causality": claim.causality,
                "numeric_value": claim.numeric_value,
                "modality": claim.modality,
                "source_document_ids": sorted(
                    str(source_id) for source_id in claim.source_document_ids
                ),
            }
            for claim in sorted(claims, key=lambda item: str(item.id))
        ],
        "sources": [
            {
                "id": str(source.id),
                "url": source.url,
                "exact_excerpt": source.exact_excerpt,
                "excerpt_hash": source.excerpt_hash,
                "applicability": source.applicability,
                "limitations": source.limitations,
            }
            for source in sorted(sources, key=lambda item: str(item.id))
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return content_hash(canonical)


def _validate_bounded_evidence(
    *, exact_excerpt: str, excerpt_hash: str, applicability: str, limitations: str
) -> None:
    if len(exact_excerpt) > 65_536:
        _incomplete("evidence excerpt exceeds 65536 characters")
    if _SHA256.fullmatch(excerpt_hash) is None or content_hash(exact_excerpt) != excerpt_hash:
        _incomplete("evidence excerpt hash mismatch")
    if len(applicability) > 4_000 or len(limitations) > 4_000:
        _incomplete("evidence applicability or limitations exceed bounds")


def _require_utc(value: datetime, name: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        _incomplete(f"{name} must be UTC")


def _incomplete(detail: str) -> Never:
    raise SafeError.for_code(
        SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE,
        developer_detail=detail,
    )


__all__ = [
    "AtomicClaim",
    "ClaimReview",
    "ClaimType",
    "ConfirmedExtraction",
    "Evidence",
    "EvidenceSource",
    "EvidenceVerdict",
    "MedicalDecision",
    "ReviewBlockingReason",
    "RiskLevel",
    "confirmed_extraction_hash",
]
