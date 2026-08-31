from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from bodrye_bot.digest.service import Digest, DigestCandidate, DigestService, SourceFailure
from bodrye_bot.digest.views import render_digest

# Moscow has used UTC+3 without daylight-saving transitions since 2014.  Keeping
# this standard-library value makes the worker portable to Windows Python builds
# that do not bundle IANA tzdata, while retaining the named business timezone.
MOSCOW = timezone(timedelta(hours=3), name="Europe/Moscow")
_DUE_TIME = time(10, 0)
_LATE_TIME = time(10, 10)


class DigestCandidateLoader(Protocol):
    async def load(
        self, *, owner_id: int, digest_date: date
    ) -> tuple[tuple[DigestCandidate, ...], tuple[SourceFailure, ...]]: ...


class DigestRunRepository(Protocol):
    """Backed by an atomic owner/date uniqueness claim in production."""

    async def claim(self, *, owner_id: int, digest_date: date) -> bool: ...

    async def record_delivered(
        self, *, owner_id: int, digest_date: date, delivered_at: datetime, late: bool
    ) -> None: ...


class TelegramDigestPort(Protocol):
    async def deliver(self, *, owner_id: int, text: str) -> None: ...


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
        service: DigestService | None = None,
    ) -> None:
        self._owner_id = owner_id
        self._loader = loader
        self._runs = runs
        self._telegram = telegram
        self._service = service or DigestService()

    async def run_due(self, now: datetime) -> DigestDelivery | None:
        if now.tzinfo is None:
            raise ValueError("Digest worker requires timezone-aware time")
        moscow_now = now.astimezone(MOSCOW)
        if moscow_now.weekday() >= 5 or moscow_now.time() < _DUE_TIME:
            return None
        digest_date = moscow_now.date()
        if not await self._runs.claim(owner_id=self._owner_id, digest_date=digest_date):
            return None
        candidates, failures = await self._loader.load(
            owner_id=self._owner_id, digest_date=digest_date
        )
        digest = self._service.build(
            candidates, digest_date=digest_date, source_failures=failures
        )
        await self._telegram.deliver(owner_id=self._owner_id, text=render_digest(digest))
        late = moscow_now.time() > _LATE_TIME
        await self._runs.record_delivered(
            owner_id=self._owner_id,
            digest_date=digest_date,
            delivered_at=now,
            late=late,
        )
        return DigestDelivery(digest=digest, delivered_at=now, late=late)


__all__ = [
    "DigestCandidateLoader",
    "DigestDelivery",
    "DigestRunRepository",
    "DigestWorker",
    "MOSCOW",
    "TelegramDigestPort",
]
