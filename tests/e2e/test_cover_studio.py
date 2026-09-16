from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.router import (
    CallbackCodec,
    IncomingCallback,
    IncomingMessage,
    TelegramResponse,
    TelegramShell,
)
from bodrye_bot.telegram.views import (
    COVER_ASK_TEXT,
    COVER_CHOICE,
    COVER_EDITED,
    COVER_LOGO_HINT,
    INLINE_COVER_BOTTOM,
    INLINE_COVER_LARGER,
    INLINE_COVER_LOGO,
    INLINE_COVER_SMALLER,
    INLINE_COVER_TEXT,
    INLINE_COVER_TOP,
    INLINE_COVER_TR,
)


def _jpeg(width: int = 200, height: int = 100) -> bytes:
    image = Image.new("RGB", (width, height), (0, 180, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, 19), fill=(220, 0, 0))
    draw.rectangle((0, height - 20, width - 1, height - 1), fill=(0, 0, 220))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _button(response: TelegramResponse, label: str) -> str:
    return next(button.callback_data for button in response.buttons if button.text == label)


def _shell() -> TelegramShell:
    now = datetime(2026, 9, 16, tzinfo=UTC)
    return TelegramShell(
        owner_guard=OwnerGuard(42),
        callback_codec=CallbackCodec(b"test-secret", clock=lambda: now),
        callback_ttl=timedelta(minutes=10),
        clock=lambda: now,
    )


@pytest.mark.asyncio
async def test_photo_keeps_full_frame_and_offers_four_buttons() -> None:
    bot = _shell()
    reply = await bot.handle(IncomingMessage(sender_id=42, text="", photo=_jpeg()))

    assert reply.text == COVER_CHOICE
    assert [button.text for button in reply.buttons] == [
        INLINE_COVER_LOGO,
        INLINE_COVER_TOP,
        INLINE_COVER_BOTTOM,
        INLINE_COVER_TEXT,
    ]
    preview = Image.open(BytesIO(reply.photo_jpeg or b""))
    assert preview.size == (200, 100)


@pytest.mark.asyncio
async def test_cover_top_button_crops_only_the_top() -> None:
    bot = _shell()
    opened = await bot.handle(IncomingMessage(sender_id=42, text="", photo=_jpeg()))
    cropped = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(opened, INLINE_COVER_TOP))
    )

    assert cropped.text == COVER_EDITED
    preview = Image.open(BytesIO(cropped.photo_jpeg or b""))
    assert preview.size[0] == 200
    assert preview.size[1] < 100
    assert preview.size[1] > 70


@pytest.mark.asyncio
async def test_cover_text_button_waits_for_caption() -> None:
    bot = _shell()
    opened = await bot.handle(IncomingMessage(sender_id=42, text="", photo=_jpeg()))
    asked = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(opened, INLINE_COVER_TEXT))
    )
    painted = await bot.handle(
        IncomingMessage(sender_id=42, text="Ночью не лежится, утром удобно")
    )

    assert asked.text == COVER_ASK_TEXT
    assert painted.text == COVER_EDITED
    preview = Image.open(BytesIO(painted.photo_jpeg or b""))
    banner = preview.getpixel((100, 8))
    assert banner[0] > 200
    assert banner[1] > 200


@pytest.mark.asyncio
async def test_cover_logo_can_be_moved_and_resized() -> None:
    bot = _shell()
    opened = await bot.handle(
        IncomingMessage(sender_id=42, text="", photo=_jpeg(400, 400))
    )
    stamped = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(opened, INLINE_COVER_LOGO))
    )
    moved = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(stamped, INLINE_COVER_TR))
    )
    smaller = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(moved, INLINE_COVER_SMALLER))
    )

    assert stamped.text == COVER_LOGO_HINT
    labels = [button.text for button in stamped.buttons]
    assert labels[4:6] == [INLINE_COVER_SMALLER, INLINE_COVER_LARGER]
    assert INLINE_COVER_TR in labels
    moved_image = Image.open(BytesIO(moved.photo_jpeg or b""))

    def max_red(left: int, top: int, right: int, bottom: int) -> int:
        return max(
            moved_image.getpixel((x, y))[0]
            for x in range(left, right, 5)
            for y in range(top, bottom, 5)
        )

    assert max_red(300, 0, 400, 100) > max_red(0, 300, 100, 400)
    assert smaller.photo_jpeg is not None


@pytest.mark.asyncio
async def test_stale_cover_button_asks_to_resend_photo() -> None:
    bot = _shell()
    first = await bot.handle(IncomingMessage(sender_id=42, text="", photo=_jpeg()))
    await bot.handle(IncomingMessage(sender_id=42, text="", photo=_jpeg(220, 120)))
    reply = await bot.handle_callback(
        IncomingCallback(sender_id=42, data=_button(first, INLINE_COVER_LOGO))
    )

    assert "Код обращения" not in reply.text
    assert "ещё раз" in reply.text.lower()
