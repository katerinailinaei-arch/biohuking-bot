from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from bodrye_bot.digest.memory import DigestCardShelf, MemoryDigestRunStore
from bodrye_bot.digest.service import DigestCard, PreliminaryRisk
from bodrye_bot.digest.worker import DigestWorker
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.editorial.memory import InMemoryManualPostStore
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.router import (
    CallbackCodec,
    IncomingCallback,
    IncomingMessage,
    TelegramShell,
)
from tests.e2e.test_digest_delivery import (
    FakeClock,
    FakeLoader,
    FakeRuns,
    FakeTelegram,
    _candidate,
)


@pytest.mark.asyncio
async def test_forced_digest_runs_on_saturday() -> None:
    loader = FakeLoader((_candidate(),), ())
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=FakeRuns(),
        telegram=telegram,
        clock=FakeClock(datetime(2026, 9, 5, 7, tzinfo=UTC)),
    )

    saturday = datetime(2026, 9, 5, 7, tzinfo=UTC)
    assert await worker.run_due(saturday) is None
    delivered = await worker.run_due(saturday, force=True)

    assert delivered is not None
    assert telegram.calls


@pytest.mark.asyncio
async def test_digest_command_sends_cards_to_owner() -> None:
    loader = FakeLoader((_candidate(),), ())
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=FakeRuns(),
        telegram=telegram,
        clock=FakeClock(datetime(2026, 9, 5, 7, tzinfo=UTC)),
    )
    shell = TelegramShell(owner_guard=OwnerGuard(42), digest_worker=worker)

    reply = await shell.handle(IncomingMessage(sender_id=42, text="/digest"))

    assert "отдельным сообщением" in reply.text
    assert telegram.calls
    assert "Движение после 35" in telegram.calls[0][1]


@pytest.mark.asyncio
async def test_forced_digest_can_run_again_the_same_day() -> None:
    loader = FakeLoader((_candidate(),), ())
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=MemoryDigestRunStore(),
        telegram=telegram,
        clock=FakeClock(datetime(2026, 9, 5, 7, tzinfo=UTC)),
    )
    now = datetime(2026, 9, 5, 7, tzinfo=UTC)

    first = await worker.run_due(now, force=True)
    second = await worker.run_due(now, force=True)

    assert first is not None
    assert second is not None
    assert len(telegram.calls) == 2


@pytest.mark.asyncio
async def test_digest_accepts_bot_mention() -> None:
    loader = FakeLoader((_candidate(),), ())
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=FakeRuns(),
        telegram=telegram,
        clock=FakeClock(datetime(2026, 9, 5, 7, tzinfo=UTC)),
    )
    shell = TelegramShell(owner_guard=OwnerGuard(42), digest_worker=worker)

    reply = await shell.handle(IncomingMessage(sender_id=42, text="/digest@biohuking_bot"))

    assert telegram.calls
    assert "отдельным сообщением" in reply.text


@pytest.mark.asyncio
async def test_python_in_chat_explains_powershell() -> None:
    shell = TelegramShell(owner_guard=OwnerGuard(42))

    reply = await shell.handle(
        IncomingMessage(sender_id=42, text="python -m bodrye_bot.main_bot")
    )

    assert "PowerShell" in reply.text
    assert "/digest" in reply.text


def _shelf_card() -> DigestCard:
    return DigestCard(
        title="Движение после 35",
        topic_fingerprint="activity",
        summary="Первая фраза. Вторая фраза.",
        rubric="Движение",
        published_at=date(2026, 9, 1),
        audience_reason="Подходит активной аудитории 35–50.",
        provenance_urls=("https://example.org/activity",),
        source_roles=(SourceRole.TOPIC,),
        preliminary_risk=PreliminaryRisk.GREEN,
        score=0.9,
        raw_score=0.9,
        score_components={},
        scoring_snapshot={},
        score_version="test-v1",
        selection_reason="выбрано",
    )


@pytest.mark.asyncio
async def test_develop_card_writes_short_post_and_cover() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    card_id = uuid4()
    shelf = DigestCardShelf()
    shelf.put(42, card_id, _shelf_card())
    codec = CallbackCodec(b"test-secret", clock=lambda: now)
    shell = TelegramShell(
        owner_guard=OwnerGuard(42),
        callback_codec=codec,
        callback_ttl=timedelta(hours=1),
        clock=lambda: now,
        manual_post_store=InMemoryManualPostStore(),
        card_shelf=shelf,
    )

    reply = await shell.handle_callback(
        IncomingCallback(
            sender_id=42,
            data=codec.encode("develop", card_id, expires_at=now + timedelta(hours=1)),
        )
    )

    assert "Коротко" in reply.text
    assert "Движение после 35" in reply.text
    assert reply.photo_url is not None
    assert reply.photo_url.startswith("https://image.pollinations.ai/")
    assert reply.buttons


@pytest.mark.asyncio
async def test_keep_card_remembers_title() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    card_id = uuid4()
    shelf = DigestCardShelf()
    shelf.put(42, card_id, _shelf_card())
    codec = CallbackCodec(b"test-secret", clock=lambda: now)
    shell = TelegramShell(
        owner_guard=OwnerGuard(42),
        callback_codec=codec,
        callback_ttl=timedelta(hours=1),
        clock=lambda: now,
        card_shelf=shelf,
    )

    reply = await shell.handle_callback(
        IncomingCallback(
            sender_id=42,
            data=codec.encode("keep", card_id, expires_at=now + timedelta(hours=1)),
        )
    )

    assert "Сохранила" in reply.text
    assert shelf.kept_titles(42) == ("Движение после 35",)
