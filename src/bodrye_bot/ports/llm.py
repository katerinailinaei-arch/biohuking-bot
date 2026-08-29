from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequestContext(StrictModel):
    owner_id: int
    workflow_id: UUID | None
    prompt_version: str = Field(min_length=1, max_length=64)
    schema_version: str = Field(min_length=1, max_length=64)


class ExtractRequest(RequestContext):
    source_document_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1, repr=False)


class ClaimsRequest(RequestContext):
    claims: tuple[str, ...] = Field(repr=False)


class EvidenceRequest(RequestContext):
    claim: str = Field(repr=False)
    evidence_fragments: tuple[str, ...] = Field(repr=False)


class AnglesRequest(RequestContext):
    topic: str = Field(repr=False)
    constraints: tuple[str, ...] = Field(default=(), repr=False)


class DraftRequest(RequestContext):
    angle: str = Field(repr=False)
    evidence_summary: str = Field(repr=False)
    style_context: tuple[str, ...] = Field(default=(), repr=False)


class ChangeRequest(RequestContext):
    previous_text: str = Field(repr=False)
    proposed_text: str = Field(repr=False)


class StyleInferenceRequest(RequestContext):
    examples: tuple[str, ...] = Field(repr=False)


class ClaimCandidate(StrictModel):
    exact_text: str = Field(min_length=1)
    medical_uncertainty: bool


class Provenance(StrictModel):
    source_document_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class ExtractResponse(StrictModel):
    response_id: str
    claim_candidates: tuple[ClaimCandidate, ...]
    provenance: tuple[Provenance, ...]


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INSUFFICIENT = "insufficient"
    MANUAL_REVIEW = "manual_review"


class ClaimClassification(StrictModel):
    exact_text: str
    verdict: ClaimVerdict
    rationale: str


class ClaimsResponse(StrictModel):
    response_id: str
    claims: tuple[ClaimClassification, ...]


class EvidenceResponse(StrictModel):
    response_id: str
    synthesis: str
    verdict: ClaimVerdict


class AngleProposal(StrictModel):
    name: str
    hook: str
    promise: str
    tone_note: str


class AnglesResponse(StrictModel):
    response_id: str
    angles: tuple[AngleProposal, ...]


class DraftResponse(StrictModel):
    response_id: str
    body: str
    headlines: tuple[str, ...]


class ChangeAssessment(StrEnum):
    COSMETIC = "cosmetic"
    SEMANTIC = "semantic"
    MEDICAL = "medical"


class ChangeResponse(StrictModel):
    response_id: str
    assessment: ChangeAssessment
    reasons: tuple[str, ...]


class StyleCandidate(StrictModel):
    rule: str
    evidence_count: int = Field(ge=1)


class StyleInferenceResponse(StrictModel):
    response_id: str
    candidates: tuple[StyleCandidate, ...]


class ProviderHealth(StrictModel):
    available: bool
    provider: str
    model: str


class AvailableModel(StrictModel):
    id: str
    provider: str


class UsageReport(StrictModel):
    owner_id: int
    workflow_id: UUID | None
    operation: str
    provider: str
    model: str
    status: str
    prompt_version: str
    schema_version: str
    provider_request_id: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    error_class: str | None
    trace_id: str


class TransportRequest(StrictModel):
    operation: str
    model: str = Field(repr=False)
    payload: Mapping[str, Any] = Field(repr=False)
    schema_repair: bool = False
    connect_timeout_seconds: int = 5
    total_timeout_seconds: int = 60


class TransportResponse(StrictModel):
    status_code: int
    json_body: Any | None = Field(default=None, repr=False)
    text_body: str | None = Field(default=None, repr=False)
    headers: Mapping[str, str] = Field(default_factory=dict, repr=False)
    request_id: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    refusal: bool = False


class LLMTransport(Protocol):
    async def complete(self, request: TransportRequest) -> TransportResponse: ...

    async def list_models(self) -> tuple[Mapping[str, Any], ...]: ...


Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[int], float]


class LLMProvider(Protocol):
    async def extract(self, request: ExtractRequest) -> ExtractResponse: ...

    async def classify_claims(self, request: ClaimsRequest) -> ClaimsResponse: ...

    async def synthesize_evidence(self, request: EvidenceRequest) -> EvidenceResponse: ...

    async def propose_angles(self, request: AnglesRequest) -> AnglesResponse: ...

    async def generate_draft(self, request: DraftRequest) -> DraftResponse: ...

    async def assess_change(self, request: ChangeRequest) -> ChangeResponse: ...

    async def infer_style_candidates(
        self, request: StyleInferenceRequest
    ) -> StyleInferenceResponse: ...

    async def healthcheck(self) -> ProviderHealth: ...

    async def estimate_or_report_usage(self, response_id: str) -> UsageReport: ...


__all__ = [
    "AngleProposal",
    "AnglesRequest",
    "AnglesResponse",
    "AvailableModel",
    "ChangeAssessment",
    "ChangeRequest",
    "ChangeResponse",
    "ClaimCandidate",
    "ClaimClassification",
    "ClaimsRequest",
    "ClaimsResponse",
    "ClaimVerdict",
    "DraftRequest",
    "DraftResponse",
    "EvidenceRequest",
    "EvidenceResponse",
    "ExtractRequest",
    "ExtractResponse",
    "Jitter",
    "LLMProvider",
    "LLMTransport",
    "ProviderHealth",
    "Provenance",
    "Sleep",
    "StrictModel",
    "StyleCandidate",
    "StyleInferenceRequest",
    "StyleInferenceResponse",
    "TransportRequest",
    "TransportResponse",
    "UsageReport",
]
