from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import ProviderRun
from bodrye_bot.db.repositories.usage import SqlAlchemyUsageLedger
from bodrye_bot.operations.token_budget import TokenCall


async def test_usage_ledger_persists_tokens_per_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ledger = SqlAlchemyUsageLedger(session_factory)
    await ledger.record(
        TokenCall(
            owner_id=42,
            operation="digest_localize",
            provider="groq",
            model="openai/gpt-oss-120b",
            status="succeeded",
            input_tokens=40,
            output_tokens=12,
        )
    )
    await ledger.record(
        TokenCall(
            owner_id=7,
            operation="digest_localize",
            provider="groq",
            model="openai/gpt-oss-120b",
            status="succeeded",
            input_tokens=999,
            output_tokens=1,
        )
    )

    mine = await ledger.for_owner(42)
    assert mine.calls == 1
    assert mine.input_tokens == 40
    assert mine.output_tokens == 12

    async with session_factory() as session:
        total = await session.scalar(select(func.count()).select_from(ProviderRun))
    assert total == 2
