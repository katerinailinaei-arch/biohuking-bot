from __future__ import annotations

from typing import Protocol

from bodrye_bot.operations.token_budget import TokenBudget, TokenCall


class UsageLedger(Protocol):
    async def record(self, call: TokenCall) -> None: ...

    async def for_owner(self, owner_id: int) -> TokenBudget: ...


__all__ = ["UsageLedger"]
