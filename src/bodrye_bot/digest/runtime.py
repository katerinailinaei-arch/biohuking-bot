from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bodrye_bot.config import Settings
from bodrye_bot.digest.live_loader import CatalogRssLoader
from bodrye_bot.digest.memory import CARD_SHELF, DigestCardShelf, MemoryDigestRunStore
from bodrye_bot.digest.service import Digest, DigestCard
from bodrye_bot.digest.views import first_source_url, render_digest_card, render_digest_intro
from bodrye_bot.digest.worker import MOSCOW, DeliveryOutcome, DigestWorker
from bodrye_bot.sources.catalog import SourceCatalog
from bodrye_bot.telegram.router import CallbackCodec

_RUNS = MemoryDigestRunStore()


class HttpxPageGetter:
    """Fetch NCBI pages with a pause and retries; NCBI allows about 3 requests/sec."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def __call__(self, url: str) -> str:
        last_error: Exception | None = None
        async with self._lock:
            for attempt in range(4):
                wait = self._next_at - asyncio.get_running_loop().time()
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                        response = await client.get(
                            url,
                            headers={"User-Agent": "bodrye-bot/0.1"},
                        )
                    self._next_at = asyncio.get_running_loop().time() + 0.4
                    if response.status_code == 429:
                        last_error = httpx.HTTPStatusError(
                            "ncbi rate limit",
                            request=response.request,
                            response=response,
                        )
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    return response.text
                except httpx.HTTPError as error:
                    last_error = error
                    await asyncio.sleep(0.6 * (attempt + 1))
        raise last_error if last_error is not None else RuntimeError("ncbi fetch failed")


class OwnerDigestTelegram:
    def __init__(
        self,
        bot: Bot,
        owner_id: int,
        *,
        codec: CallbackCodec,
        shelf: DigestCardShelf | None = None,
        ttl: timedelta = timedelta(hours=12),
    ) -> None:
        self._bot = bot
        self._owner_id = owner_id
        self._codec = codec
        self._shelf = shelf if shelf is not None else CARD_SHELF
        self._ttl = ttl

    async def deliver(
        self, *, owner_id: int, text: str, digest: Digest | None = None
    ) -> DeliveryOutcome:
        if owner_id != self._owner_id:
            return DeliveryOutcome.NOT_SENT
        try:
            await self._send(owner_id, text, digest)
        except Exception:
            return DeliveryOutcome.UNKNOWN
        return DeliveryOutcome.SENT

    async def _send(self, owner_id: int, text: str, digest: Digest | None) -> None:
        if digest is None:
            await self._bot.send_message(owner_id, text)
            return
        await self._bot.send_message(owner_id, render_digest_intro(digest))
        self._shelf.clear_owner(owner_id)
        expires_at = datetime.now(UTC) + self._ttl
        for card in digest.cards:
            card_id = uuid4()
            self._shelf.put(owner_id, card_id, card)
            await self._bot.send_message(
                owner_id,
                render_digest_card(card),
                reply_markup=self._markup(card, card_id, expires_at),
            )

    def _markup(
        self, card: DigestCard, card_id: UUID, expires_at: datetime
    ) -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton(
                text="Развить",
                callback_data=self._codec.encode("develop", card_id, expires_at=expires_at),
            ),
            InlineKeyboardButton(
                text="Сохранить",
                callback_data=self._codec.encode("keep", card_id, expires_at=expires_at),
            ),
            InlineKeyboardButton(
                text="Не интересно",
                callback_data=self._codec.encode("skip", card_id, expires_at=expires_at),
            ),
        ]
        source = first_source_url(card)
        if source is not None:
            buttons.append(InlineKeyboardButton(text="Источник", url=source))
        rows = [buttons[:2], buttons[2:]]
        return InlineKeyboardMarkup(inline_keyboard=rows)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def build_digest_worker(settings: Settings, bot: Bot) -> DigestWorker:
    secret = settings.telegram_bot_token.get_secret_value().encode("utf-8")
    return DigestWorker(
        owner_id=settings.telegram_owner_id,
        loader=CatalogRssLoader(catalog=SourceCatalog.initial(), getter=HttpxPageGetter()),
        runs=_RUNS,
        telegram=OwnerDigestTelegram(
            bot,
            settings.telegram_owner_id,
            codec=CallbackCodec(secret),
        ),
        clock=SystemClock(),
    )


async def pulse_digest(worker: DigestWorker) -> None:
    while True:
        try:
            await worker.run_due(datetime.now(MOSCOW))
        except Exception:
            pass
        await asyncio.sleep(60)


__all__ = ["build_digest_worker", "pulse_digest"]
