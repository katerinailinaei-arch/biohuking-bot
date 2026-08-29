from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.ports.llm import TransportRequest, TransportResponse
from bodrye_bot.providers.groq import GroqProvider
from tests.contract.test_llm_contract import extract_request, valid_extract_payload


@dataclass
class FakeTransport:
    responses: list[TransportResponse | Exception]
    calls: int = 0

    async def complete(self, request: TransportRequest) -> TransportResponse:
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def list_models(self) -> tuple[Mapping[str, Any], ...]:
        return ()


@pytest.mark.asyncio
async def test_safe_5xx_retries_twice_then_succeeds():
    transport = FakeTransport(
        [
            TransportResponse(status_code=503),
            TransportResponse(status_code=500),
            TransportResponse(status_code=200, json_body=valid_extract_payload(), request_id="ok"),
        ]
    )
    sleeps: list[float] = []
    provider = GroqProvider(
        transport=transport,
        model="openai/gpt-oss-120b",
        sleep=lambda delay: _record_sleep(sleeps, delay),
        jitter=lambda attempt: float(attempt),
    )

    result = await provider.extract(extract_request())

    assert len(result.response_id) == 32
    assert transport.calls == 3
    assert sleeps == [1.0, 2.0]
    usage = await provider.estimate_or_report_usage(result.response_id)
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.latency_ms is None


async def _record_sleep(sleeps: list[float], delay: float) -> None:
    sleeps.append(delay)


@pytest.mark.asyncio
async def test_exhausted_quota_does_not_retry_and_opens_new_call_circuit():
    transport = FakeTransport(
        [TransportResponse(status_code=429, headers={"X-RateLimit-Remaining-Tokens": "0"})]
    )
    provider = GroqProvider(transport=transport, model="openai/gpt-oss-120b")

    with pytest.raises(SafeError) as first:
        await provider.extract(extract_request())
    with pytest.raises(SafeError) as second:
        await provider.extract(extract_request())

    assert first.value.code is SafeErrorCode.LLM_QUOTA_EXHAUSTED
    assert second.value.code is SafeErrorCode.LLM_QUOTA_EXHAUSTED
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_positive_remaining_rate_limit_retries_then_has_safe_code():
    transport = FakeTransport(
        [
            TransportResponse(
                status_code=429,
                headers={"x-ratelimit-remaining-tokens": "12"},
            )
            for _ in range(3)
        ]
    )
    provider = GroqProvider(
        transport=transport,
        model="openai/gpt-oss-120b",
        sleep=_discard_sleep,
        jitter=lambda _: 0.0,
    )

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_RATE_LIMIT
    assert transport.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"x-ratelimit-remaining-tokens": "unknown"}])
async def test_unknown_remaining_rate_limit_never_retries(headers):
    transport = FakeTransport([TransportResponse(status_code=429, headers=headers)])
    provider = GroqProvider(
        transport=transport,
        model="openai/gpt-oss-120b",
        sleep=_discard_sleep,
    )

    with pytest.raises(SafeError) as caught:
        await provider.extract(extract_request())

    assert caught.value.code is SafeErrorCode.LLM_RATE_LIMIT
    assert transport.calls == 1


async def _discard_sleep(_: float) -> None:
    return None
