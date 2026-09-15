from __future__ import annotations

import pytest

from bodrye_bot.operations.token_budget import (
    InMemoryUsageLedger,
    TokenCall,
    render_token_budget,
    summarize_token_calls,
)


def _call(
    *,
    owner_id: int = 42,
    operation: str = "digest_localize",
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    status: str = "succeeded",
) -> TokenCall:
    return TokenCall(
        owner_id=owner_id,
        operation=operation,
        provider="groq",
        model="openai/gpt-oss-120b",
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_summarize_does_not_treat_unknown_tokens_as_zero() -> None:
    budget = summarize_token_calls(
        (
            _call(input_tokens=100, output_tokens=20),
            _call(input_tokens=None, output_tokens=None, status="failed"),
        )
    )

    assert budget.calls == 2
    assert budget.input_tokens == 100
    assert budget.output_tokens == 20
    assert budget.unknown_calls == 1
    assert budget.total_tokens == 120


def test_render_budget_explains_tokens_and_unknowns_in_russian() -> None:
    text = render_token_budget(
        summarize_token_calls((_call(input_tokens=1200, output_tokens=80),))
    )

    assert "1 200" in text
    assert "80" in text
    assert "перевод тем" in text.lower()
    assert "Groq" in text
    assert "0 ₽" in text or "бесплатн" in text.lower()


def test_render_empty_budget_explains_when_tokens_appear() -> None:
    text = render_token_budget(summarize_token_calls(()))

    assert "нет" in text.lower()
    assert "Пост" in text
    assert "0" in text


@pytest.mark.asyncio
async def test_memory_ledger_isolates_owners() -> None:
    ledger = InMemoryUsageLedger()
    await ledger.record(_call(owner_id=42, input_tokens=9, output_tokens=1))
    await ledger.record(_call(owner_id=7, input_tokens=400, output_tokens=50))

    mine = await ledger.for_owner(42)
    assert mine.input_tokens == 9
    assert mine.calls == 1
