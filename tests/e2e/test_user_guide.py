from __future__ import annotations

import pytest

from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.owner_guide import InMemoryOwnerGuide
from bodrye_bot.telegram.router import IncomingMessage, TelegramShell
from bodrye_bot.telegram.studio_state import StudioWait
from bodrye_bot.telegram.views import (
    MENU_HELP,
    MENU_POST,
    ONBOARDING_QUICKSTART,
    ONBOARDING_TOV,
    ONBOARDING_WHAT,
    SETTOV_PROMPT,
)


async def _true() -> bool:
    return True


def _shell(guide: InMemoryOwnerGuide | None = None) -> TelegramShell:
    return TelegramShell(
        owner_guard=OwnerGuard(42),
        onboarding=OnboardingService(
            database_check=_true,
            channel_check=_true,
            provider_check=_true,
            sources_check=_true,
            style_check=_true,
        ),
        owner_guide=guide if guide is not None else InMemoryOwnerGuide(),
    )


@pytest.mark.asyncio
async def test_first_start_sends_three_onboarding_messages_only_once() -> None:
    guide = InMemoryOwnerGuide()
    bot = _shell(guide)

    first = await bot.handle(IncomingMessage(sender_id=42, text="/start"))
    second = await bot.handle(IncomingMessage(sender_id=42, text="/start"))

    assert first.text == ONBOARDING_WHAT
    assert first.extra_messages == (ONBOARDING_TOV, ONBOARDING_QUICKSTART)
    assert first.show_main_keyboard is True
    assert first.ready is True
    assert "/settov" in ONBOARDING_TOV
    assert "Темы" in ONBOARDING_QUICKSTART
    assert second.text != ONBOARDING_WHAT
    assert second.extra_messages == ()
    assert "Помощь" in second.text or "меню" in second.text.lower()
    assert second.show_main_keyboard is True


@pytest.mark.asyncio
async def test_help_command_and_button_repeat_the_guide() -> None:
    bot = _shell()
    await bot.handle(IncomingMessage(sender_id=42, text="/start"))

    by_slash = await bot.handle(IncomingMessage(sender_id=42, text="/help"))
    by_button = await bot.handle(IncomingMessage(sender_id=42, text=MENU_HELP))

    for reply in (by_slash, by_button):
        assert reply.text == ONBOARDING_WHAT
        assert reply.extra_messages == (ONBOARDING_TOV, ONBOARDING_QUICKSTART)
        assert reply.show_main_keyboard is True


@pytest.mark.asyncio
async def test_post_button_explains_that_it_is_a_draft_not_a_publish() -> None:
    bot = _shell()
    reply = await bot.handle(IncomingMessage(sender_id=42, text=MENU_POST))

    lowered = reply.text.lower()
    assert "черновик" in lowered
    assert "канал" in lowered
    assert "тему" in lowered


@pytest.mark.asyncio
async def test_settov_collects_samples_until_done() -> None:
    guide = InMemoryOwnerGuide()
    bot = _shell(guide)

    prompt = await bot.handle(IncomingMessage(sender_id=42, text="/settov"))
    assert prompt.text == SETTOV_PROMPT
    assert bot._studio_sessions.get(42) is not None
    assert bot._studio_sessions.get(42).wait is StudioWait.TONE  # type: ignore[union-attr]

    more = await bot.handle(
        IncomingMessage(sender_id=42, text="После 35 важнее ритм, чем подвиг.")
    )
    assert "ещё" in more.text.lower() or "готово" in more.text.lower()

    saved = await bot.handle(IncomingMessage(sender_id=42, text="готово"))
    assert "сохранил" in saved.text.lower()
    assert guide.tone_samples(42) == ("После 35 важнее ритм, чем подвиг.",)
