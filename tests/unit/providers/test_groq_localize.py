from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from bodrye_bot.operations.token_budget import InMemoryUsageLedger
from bodrye_bot.providers.groq_localize import GroqTopicLocalizer


def _handler(request: httpx.Request) -> httpx.Response:
    assert "gsk-secret" not in str(request.url)
    assert request.headers["Authorization"] == "Bearer gsk-secret"
    body = json.loads(request.content.decode())
    assert body["response_format"] == {"type": "json_object"}
    assert "Hepatic" in body["messages"][1]["content"]
    payload = {
        "items": [
            {
                "headline": "Нехватка селена и печень у мышей",
                "summary": "Исследование на мышах. Это идея для канала, не совет людям.",
            }
        ]
    }
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 111, "completion_tokens": 22},
        },
    )


@pytest.mark.asyncio
async def test_groq_localizer_returns_russian_items_and_keeps_key_out_of_url() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        copies = await GroqTopicLocalizer(
            SecretStr("gsk-secret"), model="openai/gpt-oss-120b", client=client
        ).localize_batch(
            (
                (
                    "Hepatic cytochrome P450 induction in mice",
                    "Selenium deficiency was studied.",
                ),
            )
        )

    assert copies[0] is not None
    assert copies[0].headline.startswith("Нехватка селена")
    assert "мышах" in copies[0].summary


@pytest.mark.asyncio
async def test_groq_localizer_records_prompt_and_completion_tokens() -> None:
    ledger = InMemoryUsageLedger()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        await GroqTopicLocalizer(
            SecretStr("gsk-secret"),
            model="openai/gpt-oss-120b",
            client=client,
            usage_ledger=ledger,
            owner_id=42,
        ).localize_batch(
            (
                (
                    "Hepatic cytochrome P450 induction in mice",
                    "Selenium deficiency was studied.",
                ),
            )
        )

    budget = await ledger.for_owner(42)
    assert budget.input_tokens == 111
    assert budget.output_tokens == 22
    assert budget.calls == 1


@pytest.mark.asyncio
async def test_groq_localizer_http_error_returns_none() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        copies = await GroqTopicLocalizer(
            SecretStr("gsk-secret"), model="openai/gpt-oss-120b", client=client
        ).localize_batch((("title", "summary"),))

    assert copies == (None,)
