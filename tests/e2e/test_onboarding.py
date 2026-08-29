from __future__ import annotations

import pytest

from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.router import IncomingMessage, TelegramShell


async def _true() -> bool:
    return True


@pytest.mark.asyncio
async def test_onboarding_reports_each_readiness_gate_and_default_provider_is_blocked() -> None:
    shell = TelegramShell(
        owner_guard=OwnerGuard(42),
        onboarding=OnboardingService(
            database_check=_true,
            channel_check=_true,
            sources_check=_true,
            style_check=_true,
        ),
    )

    result = await shell.handle(IncomingMessage(sender_id=42, text="/start"))

    assert result.gates == {"database", "channel", "provider", "sources", "style"}
    assert result.ready is False
    assert "secret" not in result.text.lower()
    assert "token" not in result.text.lower()
