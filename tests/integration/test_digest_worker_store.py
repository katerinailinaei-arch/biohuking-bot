from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.repositories.digest_runs import SqlAlchemyDigestRunStore
from bodrye_bot.digest.service import DigestCandidate, SourceFailure
from bodrye_bot.digest.worker import (
    MOSCOW,
    DeliveryOutcome,
    DigestRunStatus,
    DigestWorker,
)


@dataclass
class EmptyLoader:
    failures: tuple[SourceFailure, ...] = ()
    calls: list[tuple[int, date]] = field(default_factory=list)

    async def load(
        self, *, owner_id: int, digest_date: date
    ) -> tuple[tuple[DigestCandidate, ...], tuple[SourceFailure, ...]]:
        self.calls.append((owner_id, digest_date))
        return (), self.failures


@dataclass
class RecordingTelegram:
    calls: list[tuple[int, str]] = field(default_factory=list)

    async def deliver(self, *, owner_id: int, text: str) -> DeliveryOutcome:
        self.calls.append((owner_id, text))
        return DeliveryOutcome.SENT


@dataclass(frozen=True)
class CompletionClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@pytest.mark.asyncio
async def test_empty_digest_worker_store_delivers_once_and_persists_owner_date_terminal_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Break caught: an empty durable run is retried or never reaches delivered."""
    owner_id = uuid4().int % 2_000_000_000
    digest_date = date(2026, 9, 1)
    due = datetime(2026, 9, 1, 10, tzinfo=MOSCOW)
    loader = EmptyLoader((SourceFailure("WHO", "source_unavailable"),))
    telegram = RecordingTelegram()
    store = SqlAlchemyDigestRunStore(session_factory)
    worker = DigestWorker(
        owner_id=owner_id,
        loader=loader,
        runs=store,
        telegram=telegram,
        clock=CompletionClock(due),
    )

    first = await worker.run_due(due)
    second = await worker.run_due(due)
    record = await store.get(owner_id=owner_id, digest_date=digest_date)

    assert first is not None
    assert first.digest.cards == ()
    assert second is None
    assert loader.calls == [(owner_id, digest_date)]
    assert len(telegram.calls) == 1
    assert telegram.calls[0][0] == owner_id
    assert "Утренний дайджест" in telegram.calls[0][1]
    assert "Сегодня сильных тем не найдено" in telegram.calls[0][1]
    assert "WHO: временно недоступен" in telegram.calls[0][1]
    assert record.owner_id == owner_id
    assert record.digest_date == digest_date
    assert record.status is DigestRunStatus.DELIVERED


@pytest.mark.asyncio
async def test_successful_worker_store_uses_true_late_completion_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Break caught: lateness is computed from the 10:09 start instead of 10:12 completion."""
    owner_id = uuid4().int % 2_000_000_000
    digest_date = date(2026, 9, 1)
    started_at = datetime(2026, 9, 1, 10, 9, tzinfo=MOSCOW)
    completed_at = datetime(2026, 9, 1, 10, 12, tzinfo=MOSCOW)
    store = SqlAlchemyDigestRunStore(session_factory)
    telegram = RecordingTelegram()
    worker = DigestWorker(
        owner_id=owner_id,
        loader=EmptyLoader(),
        runs=store,
        telegram=telegram,
        clock=CompletionClock(completed_at),
    )

    result = await worker.run_due(started_at)
    record = await store.get(owner_id=owner_id, digest_date=digest_date)

    assert result is not None
    assert result.delivered_at == completed_at
    assert result.late is True
    assert len(telegram.calls) == 1
    assert record.owner_id == owner_id
    assert record.digest_date == digest_date
    assert record.status is DigestRunStatus.DELIVERED
    assert record.delivered_at == completed_at
    assert record.late is True
