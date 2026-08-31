from __future__ import annotations

# ruff: noqa: E501
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from bodrye_bot.digest.service import (
    Digest,
    DigestCandidate,
    DigestCard,
    PreliminaryRisk,
    SourceFailure,
)
from bodrye_bot.digest.views import render_digest
from bodrye_bot.digest.worker import DeliveryOutcome, DigestWorker
from bodrye_bot.domain.sources import SourceRole


@dataclass
class FakeLoader:
    candidates: tuple[DigestCandidate, ...]
    failures: tuple[SourceFailure, ...]
    requested_owner_ids: list[int] = field(default_factory=list)

    async def load(self, *, owner_id: int, digest_date: date):
        self.requested_owner_ids.append(owner_id)
        return self.candidates, self.failures


@dataclass
class FakeRuns:
    claimed: set[tuple[int, date]] = field(default_factory=set)
    claims: list[tuple[int, date]] = field(default_factory=list)
    records: list[tuple[int, date, datetime, bool]] = field(default_factory=list)

    async def claim(self, *, owner_id: int, digest_date: date, now: datetime) -> bool:
        self.claims.append((owner_id, digest_date))
        key = (owner_id, digest_date)
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    async def delivered(
        self, *, owner_id: int, digest_date: date, delivered_at: datetime, late: bool
    ) -> None:
        self.records.append((owner_id, digest_date, delivered_at, late))

    async def release(self, *, owner_id: int, digest_date: date) -> None:
        pass

    async def unknown(self, *, owner_id: int, digest_date: date) -> None:
        pass


@dataclass
class FakeTelegram:
    calls: list[tuple[int, str]] = field(default_factory=list)

    async def deliver(self, *, owner_id: int, text: str) -> DeliveryOutcome:
        self.calls.append((owner_id, text))
        return DeliveryOutcome.SENT


@dataclass
class FakeClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _candidate() -> DigestCandidate:
    return DigestCandidate(
        canonical_url="https://example.org/activity",
        content_hash="a" * 64,
        topic_fingerprint="activity",
        title="Движение после 35",
        summary="Первая фраза. Вторая фраза.",
        rubric="Движение",
        published_at=date(2026, 9, 1),
        audience_reason="Подходит активной аудитории 35–50.",
        source_roles=(SourceRole.TOPIC,),
        relevance=0.9,
        freshness=0.9,
        source_authority=0.9,
        audience_fit=0.9,
        novelty=0.9,
        preliminary_risk=PreliminaryRisk.GREEN,
    )


@pytest.mark.asyncio
async def test_weekday_worker_delivers_partial_digest_once_and_records_lateness() -> None:
    """Break caught: a retry redelivers a partial digest or omits its failures."""
    loader = FakeLoader((_candidate(),), (SourceFailure("WHO", "unavailable"),))
    runs = FakeRuns()
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=runs,
        telegram=telegram,
        clock=FakeClock(now := datetime(2026, 9, 1, 7, 5, tzinfo=UTC)),
    )

    first = await worker.run_due(now)
    second = await worker.run_due(now)

    assert first is not None and first.late is False
    assert second is None
    assert loader.requested_owner_ids == [42]
    assert runs.claims == [(42, date(2026, 9, 1)), (42, date(2026, 9, 1))]
    assert runs.records == [(42, date(2026, 9, 1), now, False)]
    assert len(telegram.calls) == 1
    assert telegram.calls[0][0] == 42
    assert "WHO: временно недоступен" in telegram.calls[0][1]


@pytest.mark.asyncio
async def test_worker_never_runs_weekends_or_before_ten_moscow() -> None:
    """Break caught: UTC conversion sends a digest on a weekend or at 09:59 MSK."""
    loader = FakeLoader((_candidate(),), ())
    runs = FakeRuns()
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=runs,
        telegram=telegram,
        clock=FakeClock(datetime(2026, 9, 1, 7, tzinfo=UTC)),
    )

    assert await worker.run_due(datetime(2026, 9, 4, 6, 59, tzinfo=UTC)) is None
    assert await worker.run_due(datetime(2026, 9, 5, 7, 0, tzinfo=UTC)) is None
    assert loader.requested_owner_ids == []
    assert runs.claims == []
    assert telegram.calls == []


@pytest.mark.asyncio
async def test_owner_scope_prevents_other_owner_claim_from_suppressing_delivery() -> None:
    """Break caught: another owner can observe or suppress Keti's date run."""
    loader = FakeLoader((_candidate(),), ())
    runs = FakeRuns(claimed={(999, date(2026, 9, 1))})
    telegram = FakeTelegram()
    worker = DigestWorker(
        owner_id=42,
        loader=loader,
        runs=runs,
        telegram=telegram,
        clock=FakeClock(datetime(2026, 9, 1, 7, tzinfo=UTC)),
    )

    result = await worker.run_due(datetime(2026, 9, 1, 7, 0, tzinfo=UTC))

    assert result is not None
    assert telegram.calls[0][0] == 42


def test_digest_view_shows_date_and_escapes_untrusted_source_content() -> None:
    """Break caught: a source title becomes Telegram HTML or the card omits its date."""
    card = DigestCard(
        title="<b>не доверять</b>",
        topic_fingerprint="topic",
        summary="<script>bad()</script>",
        rubric="Сон",
        published_at=date(2026, 9, 1),
        audience_reason="важно",
        provenance_urls=("javascript:alert(1)", "https://example.org/?a=<bad>"),
        source_roles=(SourceRole.TOPIC,),
        preliminary_risk=PreliminaryRisk.GREEN,
        score=0.9,
        raw_score=0.9,
        score_components={},
        scoring_snapshot={},
        score_version="test-v1",
        selection_reason="выбрано",
    )

    rendered = render_digest(Digest(digest_date=date(2026, 9, 1), cards=(card,)))

    assert "<b>не доверять</b>" not in rendered
    assert "&lt;b&gt;не доверять&lt;/b&gt;" in rendered
    assert "javascript:" not in rendered
    assert "01.09.2026" in rendered
