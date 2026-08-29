from __future__ import annotations

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.operations.usage import UsageRecord
from bodrye_bot.ports.llm import TransportRequest, TransportResponse
from bodrye_bot.providers.groq import GroqProvider
from bodrye_bot.providers.openai import OpenAIProvider
from tests.contract.test_llm_contract import FakeTransport, extract_request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code", "detail"),
    [
        (
            TransportResponse(status_code=200, refusal=True),
            SafeErrorCode.LLM_INVALID_OUTPUT,
            "refusal",
        ),
        (TransportResponse(status_code=400), SafeErrorCode.LLM_UNAVAILABLE, "http_400"),
    ],
)
async def test_provider_failures_have_safe_russian_error_and_redacted_detail(
    response, expected_code, detail
):
    provider = GroqProvider(transport=FakeTransport([response]), model="openai/gpt-oss-120b")

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is expected_code
    assert caught.value.message_ru
    assert caught.value.developer_detail == f"provider_error_class={detail}"
    assert "Проверяемый исходный материал" not in repr(caught.value)


@pytest.mark.asyncio
async def test_timeout_is_safe_and_captures_unknown_usage_as_unknown():
    class TimeoutTransport(FakeTransport):
        async def complete(self, request):
            raise TimeoutError("secret prompt must not leak")

    provider = GroqProvider(
        transport=TimeoutTransport([]),
        model="openai/gpt-oss-120b",
    )

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_TIMEOUT
    usage = await provider.estimate_or_report_usage(caught.value.trace_id)
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.error_class == "timeout"
    assert "secret" not in repr(usage)


def test_usage_repr_and_log_payload_never_expose_sensitive_content():
    usage = UsageRecord(
        owner_id=42,
        workflow_id=None,
        operation="extract",
        provider="groq",
        model="openai/gpt-oss-120b",
        status="failed",
        prompt_version="extract-v1",
        schema_version="extract-schema-v1",
        provider_request_id="req-safe",
        latency_ms=None,
        input_tokens=None,
        output_tokens=None,
        error_class="timeout",
        trace_id="a" * 32,
    )

    log_payload = usage.to_log_dict()
    assert log_payload["prompt_version"] == "extract-v1"
    assert log_payload["schema_version"] == "extract-schema-v1"
    assert "prompt_body" not in log_payload
    assert "source_text" not in log_payload
    assert "api_key" not in repr(usage)
    assert usage.input_tokens is None


def test_request_and_raw_response_repr_hide_content():
    request = extract_request()
    transport_request = TransportRequest(
        operation="extract",
        model="openai/gpt-oss-120b",
        payload=request.model_dump(mode="json"),
    )
    response = TransportResponse(
        status_code=200,
        text_body="raw provider body with secret",
        json_body={"raw": "provider body"},
        headers={"authorization": "Bearer sk-secret-value"},
    )

    assert "Проверяемый исходный материал" not in repr(request)
    assert "Проверяемый исходный материал" not in repr(transport_request)
    assert "raw provider body" not in repr(response)
    assert "sk-secret-value" not in repr(response)


def test_usage_redacts_credential_bearing_labels_before_log_serialization():
    usage = UsageRecord(
        owner_id=42,
        workflow_id=None,
        operation="extract",
        provider="groq",
        model="https://alice:supersecret@example.org/model",
        status="failed",
        prompt_version="extract-v1",
        schema_version="schema-v1",
        provider_request_id="sk-secret-request-id",
        latency_ms=None,
        input_tokens=None,
        output_tokens=None,
        error_class="timeout",
        trace_id="b" * 32,
    )

    payload = usage.to_log_dict()

    assert payload["model"] == "[redacted]"
    assert payload["provider_request_id"] is None
    assert "supersecret" not in repr(usage)


@pytest.mark.parametrize(
    ("selected_provider", "cost_guard_enabled", "eval_activated"),
    [
        ("groq", True, True),
        ("openai", False, True),
        ("openai", True, False),
    ],
)
def test_openai_fails_closed_without_all_activation_conditions(
    selected_provider, cost_guard_enabled, eval_activated
):
    with pytest.raises(SafeError) as caught:
        OpenAIProvider(
            transport=FakeTransport([]),
            model="gpt-5.6-sol",
            selected_provider=selected_provider,
            cost_guard_enabled=cost_guard_enabled,
            eval_activated=eval_activated,
        )

    assert caught.value.code is SafeErrorCode.LLM_UNAVAILABLE


@pytest.mark.asyncio
async def test_groq_model_discovery_filters_status_candidate_and_strict_output():
    class ModelsTransport(FakeTransport):
        async def list_models(self):
            return (
                {
                    "id": "openai/gpt-oss-120b",
                    "active": True,
                    "production": True,
                    "strict_output": True,
                },
                {
                    "id": "openai/gpt-oss-20b",
                    "active": True,
                    "production": True,
                    "strict_output": False,
                },
                {"id": "other/model", "active": True, "production": True, "strict_output": True},
                {
                    "id": "openai/gpt-oss-20b",
                    "active": False,
                    "production": True,
                    "strict_output": True,
                },
            )

    provider = GroqProvider(transport=ModelsTransport([]), model="openai/gpt-oss-120b")

    models = await provider.list_models()

    assert tuple(model.id for model in models) == ("openai/gpt-oss-120b",)
