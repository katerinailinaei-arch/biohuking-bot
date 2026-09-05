from __future__ import annotations

# ruff: noqa: E501, E701
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from bodrye_bot.digest.service import Digest, DigestCandidate, DigestService, SourceFailure
from bodrye_bot.digest.views import render_digest
from bodrye_bot.domain.errors import SafeError, SafeErrorCode

MOSCOW = timezone(timedelta(hours=3), name="Europe/Moscow")
_DUE, _LATE, _LEASE = time(10), time(10, 10), timedelta(minutes=15)


class DigestRunStatus(StrEnum):
    PROCESSING = "processing"
    RETRYABLE = "retryable"
    DELIVERED = "delivered"
    DELIVERY_UNKNOWN = "delivery_unknown"


@dataclass(frozen=True)
class DigestRunClaim:
    attempt_id: UUID


class DeliveryOutcome(StrEnum):
    SENT = "sent"
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


class DigestCandidateLoader(Protocol):
    async def load(
        self, *, owner_id: int, digest_date: date
    ) -> tuple[tuple[DigestCandidate, ...], tuple[SourceFailure, ...]]: ...


class DigestRunRepository(Protocol):
    async def expire_leases(self, *, now: datetime) -> int: ...
    async def claim(
        self, *, owner_id: int, digest_date: date, now: datetime
    ) -> DigestRunClaim | None: ...
    async def mark_delivered(
        self,
        *,
        owner_id: int,
        digest_date: date,
        attempt_id: UUID,
        delivered_at: datetime,
        late: bool,
    ) -> bool: ...
    async def mark_retryable(
        self, *, owner_id: int, digest_date: date, attempt_id: UUID
    ) -> bool: ...
    async def mark_unknown(self, *, owner_id: int, digest_date: date, attempt_id: UUID) -> bool: ...


class TelegramDigestPort(Protocol):
    async def deliver(
        self, *, owner_id: int, text: str, digest: Digest | None = None
    ) -> DeliveryOutcome: ...


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

    async def run_due(self, now: datetime, *, force: bool = False) -> DigestDelivery | None:
        if now.tzinfo is None:
            raise ValueError("Digest worker requires timezone-aware time")
        moscow = now.astimezone(MOSCOW)
        await self._runs.expire_leases(now=now)
        if not force and (moscow.weekday() >= 5 or moscow.time() < _DUE):
            return None
        digest_date = moscow.date()
        claim = await self._runs.claim(owner_id=self._owner_id, digest_date=digest_date, now=now)
        if claim is None and force:
            forget = getattr(self._runs, "forget", None)
            if callable(forget):
                await forget(owner_id=self._owner_id, digest_date=digest_date)
                claim = await self._runs.claim(
                    owner_id=self._owner_id, digest_date=digest_date, now=now
                )
        if claim is None:
            return None
        attempt_id = claim.attempt_id
        try:
            candidates, failures = await self._loader.load(
                owner_id=self._owner_id, digest_date=digest_date
            )
            digest = self._service.build(
                candidates, digest_date=digest_date, source_failures=failures
            )
        except Exception:
            marked = await self._runs.mark_retryable(
                owner_id=self._owner_id, digest_date=digest_date, attempt_id=attempt_id
            )
            if not marked:
                raise _delivery_unknown("digest retryable fence rejected") from None
            raise
        try:
            outcome = await self._telegram.deliver(
                owner_id=self._owner_id, text=render_digest(digest), digest=digest
            )
        except Exception:
            marked = await self._runs.mark_unknown(
                owner_id=self._owner_id, digest_date=digest_date, attempt_id=attempt_id
            )
            if not marked:
                raise _delivery_unknown("digest unknown fence rejected") from None
            raise
        if outcome is DeliveryOutcome.NOT_SENT:
            marked = await self._runs.mark_retryable(
                owner_id=self._owner_id, digest_date=digest_date, attempt_id=attempt_id
            )
            if not marked:
                raise _delivery_unknown("digest retryable fence rejected")
            return None
        if outcome is not DeliveryOutcome.SENT:
            marked = await self._runs.mark_unknown(
                owner_id=self._owner_id, digest_date=digest_date, attempt_id=attempt_id
            )
            if not marked:
                raise _delivery_unknown("digest unknown fence rejected")
            return None
        delivered_at = self._clock.now()
        if delivered_at.tzinfo is None:
            raise ValueError("Digest clock requires timezone-aware time")
        late = delivered_at.astimezone(MOSCOW).time() > _LATE
        marked = await self._runs.mark_delivered(
            owner_id=self._owner_id,
            digest_date=digest_date,
            attempt_id=attempt_id,
            delivered_at=delivered_at,
            late=late,
        )
        if not marked:
            raise _delivery_unknown("digest delivery fence rejected after Telegram success")
        return DigestDelivery(digest, delivered_at, late)


def _delivery_unknown(detail: str) -> SafeError:
    return SafeError.for_code(SafeErrorCode.DELIVERY_UNKNOWN, developer_detail=detail)


__all__ = [
    "Clock",
    "DeliveryOutcome",
    "DigestCandidateLoader",
    "DigestDelivery",
    "DigestRunRepository",
    "DigestRunClaim",
    "DigestRunStatus",
    "DigestWorker",
    "MOSCOW",
    "TelegramDigestPort",
]
