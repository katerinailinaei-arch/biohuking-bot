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
    with pytest.raises(ValidationError):
        ExtractRequest(**{**request.model_dump(), "owner_id": "42"})


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

    assert len(result.response_id) == 32
    assert [request.schema_repair for request in transport.requests] == [False, True]


@pytest.mark.asyncio
async def test_string_false_cannot_become_medical_certainty_or_trigger_repair():
    payload = valid_extract_payload()
    payload["claim_candidates"] = [
        {"exact_text": "Неопределённое утверждение.", "medical_uncertainty": "false"}
    ]
    transport = FakeTransport([TransportResponse(status_code=200, json_body=payload)])
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_INVALID_OUTPUT
    assert len(transport.requests) == 1


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
async def test_refusal_during_schema_repair_stays_blocked():
    transport = FakeTransport(
        [
            TransportResponse(status_code=200, text_body="not json"),
            TransportResponse(status_code=200, refusal=True),
        ]
    )
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_INVALID_OUTPUT
    assert caught.value.developer_detail == "provider_error_class=refusal"
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_schema_repair_usage_aggregates_every_known_transport_response():
    transport = FakeTransport(
        [
            TransportResponse(
                status_code=200,
                text_body="not json",
                request_id="initial",
                latency_ms=10,
                input_tokens=3,
                output_tokens=1,
            ),
            TransportResponse(
                status_code=200,
                json_body=valid_extract_payload(),
                request_id="repaired",
                latency_ms=15,
                input_tokens=5,
                output_tokens=2,
            ),
        ]
    )
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    result = await provider.extract(extract_request())
    usage = await provider.estimate_or_report_usage(result.response_id)

    assert usage.latency_ms == 25
    assert usage.input_tokens == 8
    assert usage.output_tokens == 3
    assert usage.provider_request_id == "repaired"


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
