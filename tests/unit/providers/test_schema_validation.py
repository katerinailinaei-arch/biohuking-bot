from __future__ import annotations

import pytest
from pydantic import ValidationError

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.ports.llm import ExtractRequest, TransportResponse
from bodrye_bot.providers.groq import GroqProvider
from tests.contract.test_llm_contract import FakeTransport, extract_request, valid_extract_payload


def test_request_models_forbid_extra_fields_and_are_immutable():
    request = extract_request()

    with pytest.raises(ValidationError):
        ExtractRequest(**request.model_dump(), surprise="forbidden")
    with pytest.raises(ValidationError):
        request.source_text = "changed"


@pytest.mark.asyncio
async def test_malformed_json_gets_exactly_one_schema_repair():
    transport = FakeTransport(
        [
            TransportResponse(status_code=200, text_body="not json", request_id="bad"),
            TransportResponse(
                status_code=200, json_body=valid_extract_payload(), request_id="repaired"
            ),
        ]
    )
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    result = await provider.extract(extract_request())

    assert result.response_id == "repaired"
    assert [request.schema_repair for request in transport.requests] == [False, True]


@pytest.mark.asyncio
async def test_second_invalid_response_stays_blocked():
    transport = FakeTransport(
        [
            TransportResponse(status_code=200, text_body="not json"),
            TransportResponse(status_code=200, json_body={"still": "wrong"}),
        ]
    )
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_INVALID_OUTPUT
    assert len(transport.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {**valid_extract_payload(), "unexpected": "field"},
        {
            "claim_candidates": [{"exact_text": "Текст", "medical_uncertainty": "unknown-enum"}],
            "provenance": [],
        },
    ],
)
async def test_forbidden_extra_or_unknown_enum_is_not_repaired(payload):
    transport = FakeTransport([TransportResponse(status_code=200, json_body=payload)])
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_INVALID_OUTPUT
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_medical_uncertainty_remains_explicitly_blocked_data():
    payload = valid_extract_payload()
    payload["claim_candidates"] = [
        {"exact_text": "Причинное утверждение не доказано.", "medical_uncertainty": True}
    ]
    transport = FakeTransport([TransportResponse(status_code=200, json_body=payload)])
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    result = await provider.extract(extract_request())

    assert result.claim_candidates[0].medical_uncertainty is True
    assert len(transport.requests) == 1
