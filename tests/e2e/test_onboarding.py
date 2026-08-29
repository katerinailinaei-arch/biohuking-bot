from __future__ import annotations

import pytest

from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.router import IncomingMessage, TelegramShell


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


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


@pytest.mark.asyncio
async def test_onboarding_is_ready_only_when_exactly_five_gates_are_green() -> None:
    result = await OnboardingService(
        database_check=_true,
        channel_check=_true,
        provider_check=_true,
        sources_check=_true,
        style_check=_true,
    ).check()

    assert result.gates == {"database", "channel", "provider", "sources", "style"}
    assert len(result.checks) == 5
    assert result.ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_gate", ["database", "channel", "provider", "sources", "style"])
async def test_onboarding_blocks_readiness_when_any_single_gate_is_false(failed_gate: str) -> None:
    checks = {
        "database_check": _true,
        "channel_check": _true,
        "provider_check": _true,
        "sources_check": _true,
        "style_check": _true,
    }
    checks[f"{failed_gate}_check"] = _false

    result = await OnboardingService(**checks).check()

    assert len(result.checks) == 5
    assert result.checks[failed_gate] is False
    assert result.ready is False
