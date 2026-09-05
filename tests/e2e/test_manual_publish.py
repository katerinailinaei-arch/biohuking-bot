from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bodrye_bot.domain.errors import SafeErrorCode
from bodrye_bot.editorial.memory import InMemoryChannelPublisher, InMemoryManualPostStore
from bodrye_bot.editorial.template_draft import TemplateDraftWriter
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.router import (
    CallbackCodec,
    IncomingCallback,
    IncomingMessage,
    TelegramShell,
)
from bodrye_bot.telegram.views import INLINE_PUBLISH, INLINE_REVIEWED, NEUTRAL_DENIAL


@pytest.fixture
def shell() -> tuple[TelegramShell, InMemoryChannelPublisher, InMemoryManualPostStore]:
    store = InMemoryManualPostStore()
    publisher = InMemoryChannelPublisher()
    now = datetime(2026, 9, 4, tzinfo=UTC)
    shell = TelegramShell(
        owner_guard=OwnerGuard(42),
        callback_codec=CallbackCodec(b"test-secret", clock=lambda: now),
        callback_ttl=timedelta(minutes=10),
        clock=lambda: now,
        manual_post_store=store,
        draft_writer=TemplateDraftWriter(),
        channel_publisher=publisher,
    )
    return shell, publisher, store


@pytest.mark.asyncio
async def test_owner_draft_review_publish_sends_once(
    shell: tuple[TelegramShell, InMemoryChannelPublisher, InMemoryManualPostStore],
) -> None:
    bot, publisher, store = shell

    draft = await bot.handle(IncomingMessage(sender_id=42, text="/draft Бессонница после 35"))
    assert "Бессонница после 35" in draft.text
    assert "я проверила" in draft.text.lower()
    assert draft.buttons

    reviewed_data = next(
        button.callback_data for button in draft.buttons if button.text == INLINE_REVIEWED
    )
    reviewed = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=reviewed_data)
    )
    assert "проверенн" in reviewed.text.lower()

    published = await bot.handle_callback(
        IncomingCallback(
            sender_id=42,
            data=next(
                button.callback_data for button in reviewed.buttons if button.text == INLINE_PUBLISH
            ),
        )
    )
    post = await store.latest(42)

    assert "опубликован" in published.text.lower()
    assert len(publisher.sent) == 1
    assert publisher.sent[0].text == post.body
    assert post.status.value == "published"


@pytest.mark.asyncio
async def test_stranger_cannot_draft_or_publish(
    shell: tuple[TelegramShell, InMemoryChannelPublisher, InMemoryManualPostStore],
) -> None:
    bot, publisher, _store = shell

    denied = await bot.handle(IncomingMessage(sender_id=7, text="/draft секрет"))

    assert denied.text == NEUTRAL_DENIAL
    assert publisher.sent == []


@pytest.mark.asyncio
async def test_publish_without_review_is_blocked(
    shell: tuple[TelegramShell, InMemoryChannelPublisher, InMemoryManualPostStore],
) -> None:
    bot, publisher, _store = shell
    await bot.handle(IncomingMessage(sender_id=42, text="/draft Тема"))

    blocked = await bot.handle(IncomingMessage(sender_id=42, text="/publish"))

    assert publisher.sent == []
    assert "не завершена" in blocked.text.lower() or "провер" in blocked.text.lower()
    assert SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE.value not in blocked.text
