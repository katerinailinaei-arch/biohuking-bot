from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr

from bodrye_bot.domain.medical import ClaimType, RiskLevel
from bodrye_bot.ports.llm import (
    AnglesRequest,
    ChangeRequest,
    ClaimsRequest,
    DraftRequest,
    EvidenceFragment,
    EvidenceRequest,
    ExtractRequest,
    MedicalClaimInput,
    StyleInferenceRequest,
    TransportRequest,
    TransportResponse,
)
from bodrye_bot.providers.groq import GroqProvider
from bodrye_bot.providers.openai import OpenAIProvider


@dataclass
class FakeTransport:
    responses: list[TransportResponse]
    requests: list[TransportRequest] = field(default_factory=list)

    async def complete(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    async def list_models(self) -> tuple[Mapping[str, Any], ...]:
        return ()


def valid_extract_payload() -> dict[str, object]:
    return {
        "claim_candidates": [
            {"exact_text": "Сон связан с восстановлением.", "medical_uncertainty": False}
        ],
        "provenance": [
            {
                "source_document_id": "source-1",
                "source_url": "https://example.org/study",
            }
        ],
    }


def extract_request() -> ExtractRequest:
    return ExtractRequest(
        owner_id=42,
        workflow_id=UUID("00000000-0000-0000-0000-000000000123"),
        prompt_version="extract-v1",
        schema_version="extract-schema-v1",
        source_document_id="source-1",
        source_text="Проверяемый исходный материал",
    )


def groq_factory(transport: FakeTransport) -> GroqProvider:
    return GroqProvider(transport=transport, model="openai/gpt-oss-120b")


def openai_factory(transport: FakeTransport) -> OpenAIProvider:
    return OpenAIProvider(
        transport=transport,
        model="gpt-5.6-sol",
        selected_provider="openai",
        cost_guard_enabled=True,
        eval_activated=True,
        api_key=SecretStr("test-openai-key"),
    )


MEDICAL_CLAIM = MedicalClaimInput(
    claim_id=UUID("00000000-0000-0000-0000-000000000456"),
    exact_text="Сон важен.",
    claim_type=ClaimType.EFFECT,
    population="Взрослые",
    context="Регулярный сон",
    causality="Может поддерживать",
    numeric_value=None,
    modality="Может",
    medical_uncertainty=False,
)


def medical_claim_payload() -> dict[str, object]:
    return {
        **MEDICAL_CLAIM.model_dump(mode="json"),
        "risk": RiskLevel.GREEN.value,
        "verdict": "supported",
        "rationale": "Есть evidence.",
    }


def medical_evidence_payload() -> dict[str, object]:
    return {
        **MEDICAL_CLAIM.model_dump(mode="json"),
        "source_document_id": "00000000-0000-0000-0000-000000000789",
        "applicability": "Взрослые",
        "limitations": "Общие данные.",
        "risk": RiskLevel.GREEN.value,
        "synthesis": "Подтверждено источником.",
        "verdict": "supported",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_factory", [groq_factory, openai_factory])
@pytest.mark.parametrize(
    ("method_name", "case_request", "payload", "expected"),
    [
        ("extract", extract_request(), valid_extract_payload(), valid_extract_payload()),
        (
            "classify_claims",
            ClaimsRequest(
                owner_id=42,
                workflow_id=None,
                prompt_version="claims-v1",
                schema_version="claims-schema-v1",
                claims=(MEDICAL_CLAIM,),
            ),
            {"claims": [medical_claim_payload()]},
            {"claims": [medical_claim_payload()]},
        ),
        (
            "synthesize_evidence",
            EvidenceRequest(
                owner_id=42,
                workflow_id=None,
                prompt_version="evidence-v1",
                schema_version="evidence-schema-v1",
                claim=MEDICAL_CLAIM,
                evidence_fragment=EvidenceFragment(
                    source_document_id=UUID("00000000-0000-0000-0000-000000000789"),
                    exact_excerpt="Фрагмент.",
                ),
            ),
            medical_evidence_payload(),
            medical_evidence_payload(),
        ),
        (
            "propose_angles",
            AnglesRequest(
                owner_id=42,
                workflow_id=None,
                prompt_version="angles-v1",
                schema_version="angles-schema-v1",
                topic="Сон",
            ),
            {
                "angles": [
                    {
                        "name": "Практика",
                        "hook": "Хук",
                        "promise": "Польза",
                        "tone_note": "Спокойно",
                    }
                ]
            },
            {
                "angles": [
                    {
                        "name": "Практика",
                        "hook": "Хук",
                        "promise": "Польза",
                        "tone_note": "Спокойно",
                    }
                ]
            },
        ),
        (
            "generate_draft",
            DraftRequest(
                owner_id=42,
                workflow_id=None,
                prompt_version="draft-v1",
                schema_version="draft-schema-v1",
                angle="Практика",
                evidence_summary="Подтверждено.",
            ),
            {"body": "Черновик.", "headlines": ["Заголовок"]},
            {"body": "Черновик.", "headlines": ["Заголовок"]},
        ),
        (
            "assess_change",
            ChangeRequest(
                owner_id=42,
                workflow_id=None,
                prompt_version="change-v1",
                schema_version="change-schema-v1",
                previous_text="Было.",
                proposed_text="Стало.",
            ),
            {"assessment": "semantic", "reasons": ["Изменён смысл."]},
            {"assessment": "semantic", "reasons": ["Изменён смысл."]},
        ),
        (
            "infer_style_candidates",
            StyleInferenceRequest(
                owner_id=42,
                workflow_id=None,
                prompt_version="style-v1",
                schema_version="style-schema-v1",
                examples=("Пример.",),
            ),
            {"candidates": [{"rule": "Короткие абзацы", "evidence_count": 1}]},
            {"candidates": [{"rule": "Короткие абзацы", "evidence_count": 1}]},
        ),
    ],
)
async def test_providers_normalize_all_typed_operation_semantics(
    provider_factory, method_name, case_request, payload, expected
):
    transport = FakeTransport(
        [
            TransportResponse(
                status_code=200,
                json_body=payload,
                request_id="req-123",
                latency_ms=17,
                input_tokens=11,
                output_tokens=7,
            )
        ]
    )

    provider = provider_factory(transport)
    result = await getattr(provider, method_name)(case_request)

    normalized = result.model_dump(mode="json")
    response_id = normalized.pop("response_id")
    assert re.fullmatch(r"[0-9a-f]{32}", response_id)
    assert normalized == expected
    usage = await provider.estimate_or_report_usage(response_id)
    assert usage.provider_request_id == "req-123"
    assert usage.operation == method_name
    assert transport.requests[0].connect_timeout_seconds == 5
    assert transport.requests[0].total_timeout_seconds == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_factory", [groq_factory, openai_factory])
async def test_providers_share_healthcheck_semantics(provider_factory):
    provider = provider_factory(FakeTransport([]))

    health = await provider.healthcheck()

    assert health.available is True
    assert health.provider in {"groq", "openai"}
    assert health.model
