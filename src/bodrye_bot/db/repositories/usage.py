from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import ProviderRun
from bodrye_bot.operations.token_budget import TokenBudget, TokenCall, summarize_token_calls


class SqlAlchemyUsageLedger:
    """Persist token calls as owner-scoped provider_runs without a workflow."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, call: TokenCall) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                ProviderRun(
                    owner_id=call.owner_id,
                    operation=call.operation,
                    provider=call.provider,
                    model=call.model,
                    status=call.status,
                    prompt_version="digest-localize-v1",
                    schema_version="none",
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                )
            )

    async def for_owner(self, owner_id: int) -> TokenBudget:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ProviderRun).where(ProviderRun.owner_id == owner_id)
                )
            ).all()
        return summarize_token_calls(
            tuple(
                TokenCall(
                    owner_id=row.owner_id,
                    operation=row.operation,
                    provider=row.provider,
                    model=row.model,
                    status=row.status,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                )
                for row in rows
            )
        )


__all__ = ["SqlAlchemyUsageLedger"]
