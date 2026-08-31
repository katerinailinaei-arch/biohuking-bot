from __future__ import annotations

# ruff: noqa: E501, E701
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from bodrye_bot.digest.service import Digest, DigestCandidate, DigestService, SourceFailure
from bodrye_bot.digest.views import render_digest

MOSCOW = timezone(timedelta(hours=3), name="Europe/Moscow")
_DUE, _LATE, _LEASE = time(10), time(10, 10), timedelta(minutes=15)


class DigestRunStatus(StrEnum):
    PROCESSING = "processing"
    RETRYABLE = "retryable"
    DELIVERED = "delivered"
    DELIVERY_UNKNOWN = "delivery_unknown"


class DeliveryOutcome(StrEnum):
    SENT = "sent"
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


class DigestCandidateLoader(Protocol):
    async def load(
        self, *, owner_id: int, digest_date: date
    ) -> tuple[tuple[DigestCandidate, ...], tuple[SourceFailure, ...]]: ...


class DigestRunRepository(Protocol):
    async def claim(self, *, owner_id: int, digest_date: date, now: datetime) -> bool: ...
    async def delivered(
        self, *, owner_id: int, digest_date: date, delivered_at: datetime, late: bool
    ) -> None: ...
    async def release(self, *, owner_id: int, digest_date: date) -> None: ...
    async def unknown(self, *, owner_id: int, digest_date: date) -> None: ...


class TelegramDigestPort(Protocol):
    async def deliver(self, *, owner_id: int, text: str) -> DeliveryOutcome: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class DigestDelivery:
    digest: Digest
    delivered_at: datetime
    late: bool


class DigestWorker:
    def __init__(
        self,
        *,
        owner_id: int,
        loader: DigestCandidateLoader,
        runs: DigestRunRepository,
        telegram: TelegramDigestPort,
        clock: Clock,
        service: DigestService | None = None,
    ) -> None:
        self._owner_id, self._loader, self._runs, self._telegram, self._clock = (
            owner_id,
            loader,
            runs,
            telegram,
            clock,
        )
        self._service = service or DigestService()

    async def run_due(self, now: datetime) -> DigestDelivery | None:
        if now.tzinfo is None:
            raise ValueError("Digest worker requires timezone-aware time")
        moscow = now.astimezone(MOSCOW)
        if moscow.weekday() >= 5 or moscow.time() < _DUE:
            return None
        digest_date = moscow.date()
        if not await self._runs.claim(owner_id=self._owner_id, digest_date=digest_date, now=now):
            return None
        try:
            candidates, failures = await self._loader.load(
                owner_id=self._owner_id, digest_date=digest_date
            )
            digest = self._service.build(
                candidates, digest_date=digest_date, source_failures=failures
            )
        except Exception:
            await self._runs.release(owner_id=self._owner_id, digest_date=digest_date)
            raise
        try:
            outcome = await self._telegram.deliver(
                owner_id=self._owner_id, text=render_digest(digest)
            )
        except Exception:
            await self._runs.unknown(owner_id=self._owner_id, digest_date=digest_date)
            raise
        if outcome is DeliveryOutcome.NOT_SENT:
            await self._runs.release(owner_id=self._owner_id, digest_date=digest_date)
            return None
        if outcome is not DeliveryOutcome.SENT:
            await self._runs.unknown(owner_id=self._owner_id, digest_date=digest_date)
            return None
        delivered_at = self._clock.now()
        if delivered_at.tzinfo is None:
            raise ValueError("Digest clock requires timezone-aware time")
        late = delivered_at.astimezone(MOSCOW).time() > _LATE
        await self._runs.delivered(
            owner_id=self._owner_id, digest_date=digest_date, delivered_at=delivered_at, late=late
        )
        return DigestDelivery(digest, delivered_at, late)


__all__ = [
    "Clock",
    "DeliveryOutcome",
    "DigestCandidateLoader",
    "DigestDelivery",
    "DigestRunRepository",
    "DigestRunStatus",
    "DigestWorker",
    "MOSCOW",
    "TelegramDigestPort",
]
