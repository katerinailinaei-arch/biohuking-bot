from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bodrye_bot.editorial.memory import InMemoryChannelPublisher, InMemoryManualPostStore
from bodrye_bot.editorial.studio import StudioWriter
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.router import (
    CallbackCodec,
    IncomingCallback,
    IncomingMessage,
    TelegramResponse,
    TelegramShell,
)
from bodrye_bot.telegram.views import (
    INLINE_PUBLISH,
    INLINE_REFINE,
    INLINE_REGEN,
    INLINE_REVIEWED,
    MENU_POST,
    MENU_PUBLISH,
    MENU_REVIEWED,
    MENU_TOPICS,
)


def _button(response: TelegramResponse, label: str) -> str:
    return next(button.callback_data for button in response.buttons if button.text == label)


@pytest.fixture
def shell() -> tuple[TelegramShell, InMemoryChannelPublisher]:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    publisher = InMemoryChannelPublisher()
    bot = TelegramShell(
        owner_guard=OwnerGuard(42),
        callback_codec=CallbackCodec(b"test-secret", clock=lambda: now),
        callback_ttl=timedelta(minutes=10),
        clock=lambda: now,
        manual_post_store=InMemoryManualPostStore(),
        studio_writer=StudioWriter(),
        channel_publisher=publisher,
    )
    return bot, publisher


@pytest.mark.asyncio
async def test_main_menu_asks_for_topic_then_shows_inline_actions(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, _publisher = shell
    prompt = await bot.handle(IncomingMessage(sender_id=42, text=MENU_POST))
    assert prompt.show_main_keyboard is True
    assert "тему" in prompt.text.lower()

    generated = await bot.handle(IncomingMessage(sender_id=42, text="сон после 35"))
    labels = [button.text for button in generated.buttons]

    assert "сон после 35" in generated.text.lower()
    assert labels == [INLINE_REFINE, INLINE_REGEN, INLINE_REVIEWED, INLINE_PUBLISH]


@pytest.mark.asyncio
async def test_bottom_review_and_publish_labels(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, publisher = shell
    await bot.handle(IncomingMessage(sender_id=42, text=MENU_POST))
    generated = await bot.handle(IncomingMessage(sender_id=42, text="вода"))

    reviewed = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(generated, INLINE_REVIEWED))
    )
    published = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(reviewed, INLINE_PUBLISH))
    )

    assert "проверенн" in reviewed.text.lower()
    assert "опубликован" in published.text.lower()
    assert len(publisher.sent) == 1


@pytest.mark.asyncio
async def test_inline_refine_and_regen(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, _publisher = shell
    await bot.handle(IncomingMessage(sender_id=42, text=MENU_POST))
    generated = await bot.handle(IncomingMessage(sender_id=42, text="вода"))

    asked = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(generated, INLINE_REFINE))
    )
    assert "изменить" in asked.text.lower()

    revised = await bot.handle(IncomingMessage(sender_id=42, text="короче и теплее"))
    assert "короче и теплее" in revised.text

    fresh = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(generated, INLINE_REGEN))
    )
    assert "По делу" in fresh.text or "Без пафоса" in fresh.text or "вариант" in fresh.text.lower()


@pytest.mark.asyncio
async def test_transcribed_text_without_menu_creates_post(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, _publisher = shell
    generated = await bot.handle(
        IncomingMessage(sender_id=42, text="сон после 35", transcribed=True)
    )

    assert "сон после 35" in generated.text.lower()
    assert generated.buttons


@pytest.mark.asyncio
async def test_transcribed_followup_revises_existing_draft(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, _publisher = shell
    await bot.handle(IncomingMessage(sender_id=42, text=MENU_POST))
    await bot.handle(IncomingMessage(sender_id=42, text="вода"))
    revised = await bot.handle(
        IncomingMessage(sender_id=42, text="короче и теплее", transcribed=True)
    )

    assert "короче и теплее" in revised.text


@pytest.mark.asyncio
async def test_topics_button_without_worker_explains_restart(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, _publisher = shell
    reply = await bot.handle(IncomingMessage(sender_id=42, text=MENU_TOPICS))
    assert "дайджест" in reply.text.lower()


@pytest.mark.asyncio
async def test_reply_keyboard_review_label(
    shell: tuple[TelegramShell, InMemoryChannelPublisher],
) -> None:
    bot, _publisher = shell
    await bot.handle(IncomingMessage(sender_id=42, text=MENU_POST))
    await bot.handle(IncomingMessage(sender_id=42, text="сон"))
    reviewed = await bot.handle(IncomingMessage(sender_id=42, text=MENU_REVIEWED))
    blocked = await bot.handle(IncomingMessage(sender_id=42, text=MENU_PUBLISH))

    assert "проверенн" in reviewed.text.lower()
    assert "опубликован" in blocked.text.lower() or "канал" in blocked.text.lower()
