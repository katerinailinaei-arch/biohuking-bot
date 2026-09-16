from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.operations.token_budget import InMemoryUsageLedger, TokenCall
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.owner_guide import InMemoryOwnerGuide
from bodrye_bot.telegram.router import IncomingMessage, TelegramShell
from bodrye_bot.telegram.studio_state import StudioWait
from bodrye_bot.telegram.views import (
    COVER_CHOICE,
    COVER_PROMPT,
    MENU_BUDGET,
    MENU_HELP,
    MENU_POST,
    ONBOARDING_QUICKSTART,
    ONBOARDING_TOV,
    ONBOARDING_WHAT,
    SETTOV_PROMPT,
)


async def _true() -> bool:
    return True


def _shell(
    guide: InMemoryOwnerGuide | None = None,
    ledger: InMemoryUsageLedger | None = None,
) -> TelegramShell:
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
        usage_ledger=ledger,
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


@pytest.mark.asyncio
async def test_forward_from_inspiration_channel_asks_owner_to_write() -> None:
    bot = _shell()
    reply = await bot.handle(
        IncomingMessage(sender_id=42, text="чужая шутка про колени", forward_from="kollegi_joke")
    )

    assert "Крючок приняла" in reply.text
    assert "своими словами" in reply.text
    assert "чужая шутка" not in reply.text
    assert "Пост" in reply.text


@pytest.mark.asyncio
async def test_forward_from_blocked_channel_is_refused() -> None:
    bot = _shell()
    reply = await bot.handle(
        IncomingMessage(sender_id=42, text="вирусный ролик", forward_from="SchroodingerCat")
    )

    assert "стоп-листе" in reply.text
    assert "вирусный ролик" not in reply.text


@pytest.mark.asyncio
async def test_sources_lists_inspiration_channels() -> None:
    bot = _shell()
    reply = await bot.handle(IncomingMessage(sender_id=42, text="/sources"))

    assert "kollegi_joke" in reply.text
    assert "SchroodingerCat" in reply.text


@pytest.mark.asyncio
async def test_budget_button_and_costs_command_show_recorded_tokens() -> None:
    ledger = InMemoryUsageLedger()
    await ledger.record(
        TokenCall(
            owner_id=42,
            operation="digest_localize",
            provider="groq",
            model="openai/gpt-oss-120b",
            status="succeeded",
            input_tokens=90,
            output_tokens=10,
        )
    )
    bot = _shell(ledger=ledger)

    by_button = await bot.handle(IncomingMessage(sender_id=42, text=MENU_BUDGET))
    by_slash = await bot.handle(IncomingMessage(sender_id=42, text="/costs"))

    for reply in (by_button, by_slash):
        assert reply.show_main_keyboard is True
        assert "90" in reply.text
        assert "10" in reply.text
        assert "100" in reply.text
        assert "перевод тем" in reply.text.lower()


def _sample_photo() -> bytes:
    image = Image.new("RGB", (200, 100), (0, 180, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 199, 19), fill=(220, 0, 0))
    draw.rectangle((0, 80, 199, 99), fill=(0, 0, 220))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_cover_command_explains_how_to_send_a_photo() -> None:
    bot = _shell()
    reply = await bot.handle(IncomingMessage(sender_id=42, text="/cover"))

    assert reply.text == COVER_PROMPT
    assert reply.show_main_keyboard is True


@pytest.mark.asyncio
async def test_photo_offers_cover_buttons_without_auto_crop() -> None:
    bot = _shell()
    reply = await bot.handle(
        IncomingMessage(
            sender_id=42,
            text="Ночью не лежится, утром удобно",
            photo=_sample_photo(),
        )
    )

    assert reply.text == COVER_CHOICE
    assert reply.photo_jpeg is not None
    preview = Image.open(BytesIO(reply.photo_jpeg))
    assert preview.size == (200, 100)


@pytest.mark.asyncio
async def test_blocked_forward_photo_is_not_branded() -> None:
    bot = _shell()
    reply = await bot.handle(
        IncomingMessage(
            sender_id=42,
            text="мем",
            photo=_sample_photo(),
            forward_from="SchroodingerCat",
        )
    )

    assert reply.photo_jpeg is None
    assert "стоп-листе" in reply.text

