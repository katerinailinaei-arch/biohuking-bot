from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from bodrye_bot.ports.llm import ExtractRequest, TransportRequest, TransportResponse
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
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_factory", [groq_factory, openai_factory])
async def test_providers_normalize_the_same_valid_extract_semantics(provider_factory):
    transport = FakeTransport(
        [
            TransportResponse(
                status_code=200,
                json_body=valid_extract_payload(),
                request_id="req-123",
                latency_ms=17,
                input_tokens=11,
                output_tokens=7,
            )
        ]
    )

    result = await provider_factory(transport).extract(extract_request())

    assert result.model_dump(mode="json") == {
        "response_id": "req-123",
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
    assert transport.requests[0].connect_timeout_seconds == 5
    assert transport.requests[0].total_timeout_seconds == 60
