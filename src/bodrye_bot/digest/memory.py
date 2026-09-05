from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from bodrye_bot.digest.service import DigestCard
from bodrye_bot.digest.worker import DigestRunClaim


class MemoryDigestRunStore:
    """Process-local digest leases so the laptop path works without PostgreSQL."""

    def __init__(self) -> None:
        self._claimed: dict[tuple[int, date], UUID] = {}
        self._delivered: set[tuple[int, date]] = set()

    async def expire_leases(self, *, now: datetime) -> int:
        del now
        return 0

    async def forget(self, *, owner_id: int, digest_date: date) -> None:
        key = (owner_id, digest_date)
        self._claimed.pop(key, None)
        self._delivered.discard(key)

    async def claim(
        self, *, owner_id: int, digest_date: date, now: datetime
    ) -> DigestRunClaim | None:
        del now
        key = (owner_id, digest_date)
        if key in self._claimed or key in self._delivered:
            return None
        attempt_id = uuid4()
        self._claimed[key] = attempt_id
        return DigestRunClaim(attempt_id=attempt_id)

    async def mark_delivered(
        self,
        *,
        owner_id: int,
        digest_date: date,
        attempt_id: UUID,
        delivered_at: datetime,
        late: bool,
    ) -> bool:
        del delivered_at, late
        key = (owner_id, digest_date)
        if self._claimed.get(key) != attempt_id:
            return False
        self._claimed.pop(key, None)
        self._delivered.add(key)
        return True

    async def mark_retryable(self, *, owner_id: int, digest_date: date, attempt_id: UUID) -> bool:
        key = (owner_id, digest_date)
        if self._claimed.get(key) != attempt_id:
            return False
        self._claimed.pop(key, None)
        return True

    async def mark_unknown(self, *, owner_id: int, digest_date: date, attempt_id: UUID) -> bool:
        return await self.mark_retryable(
            owner_id=owner_id, digest_date=digest_date, attempt_id=attempt_id
        )


class DigestCardShelf:
    """In-memory digest cards so inline buttons can reopen a topic."""

    def __init__(self) -> None:
        self._cards: dict[tuple[int, UUID], DigestCard] = {}
        self._kept: dict[int, list[str]] = {}

    def clear_owner(self, owner_id: int) -> None:
        self._cards = {key: card for key, card in self._cards.items() if key[0] != owner_id}

    def put(self, owner_id: int, card_id: UUID, card: DigestCard) -> None:
        self._cards[(owner_id, card_id)] = card

    def get(self, owner_id: int, card_id: UUID) -> DigestCard | None:
        return self._cards.get((owner_id, card_id))

    def keep(self, owner_id: int, title: str) -> None:
        self._kept.setdefault(owner_id, []).append(title)

    def kept_titles(self, owner_id: int) -> tuple[str, ...]:
        return tuple(self._kept.get(owner_id, ()))


CARD_SHELF = DigestCardShelf()
