from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bodrye_bot.identity.service import OwnerGuard

SENSITIVE_CONFIRMATION_TEXT = "Сохранить несмотря на предупреждение"
_SENSITIVE_PATTERN = re.compile(
    r"\b(анализ(?:ы|а|ов)?|диагноз(?:а|ы|ов)?|медицинск\w*\s+карт\w*|"
    r"истори\w*\s+болезн\w*|фио|дата\s+рождени\w*|подписчик\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SensitiveInspection:
    transient_id: str
    requires_confirmation: bool


@dataclass(frozen=True)
class _TransientPayload:
    owner_id: int
    payload: str
    expires_at: datetime


class SensitiveInputGuard:
    """Holds flagged text only in process memory until explicit consent."""

    def __init__(
        self,
        owner_guard: OwnerGuard,
        *,
        ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._owner_guard = owner_guard
        self._ttl = ttl
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._transient: dict[str, _TransientPayload] = {}
        self._permanent: dict[str, str] = {}

    async def inspect(self, owner_id: int, payload: str) -> SensitiveInspection:
        self._owner_guard.authorize(owner_id)
        transient_id = uuid4().hex
        if _SENSITIVE_PATTERN.search(payload):
            self._transient[transient_id] = _TransientPayload(
                owner_id=owner_id,
                payload=payload,
                expires_at=self._clock() + self._ttl,
            )
            return SensitiveInspection(transient_id=transient_id, requires_confirmation=True)

        self._permanent[transient_id] = payload
        return SensitiveInspection(transient_id=transient_id, requires_confirmation=False)

    async def confirm(self, owner_id: int, transient_id: str, confirmation: str) -> bool:
        self._owner_guard.authorize(owner_id)
        if confirmation != SENSITIVE_CONFIRMATION_TEXT:
            return False
        transient = self._take_current(owner_id, transient_id)
        if transient is None:
            return False
        self._permanent[transient_id] = transient.payload
        return True

    async def cancel(self, owner_id: int, transient_id: str) -> None:
        self._owner_guard.authorize(owner_id)
        transient = self._transient.get(transient_id)
        if transient is not None and transient.owner_id == owner_id:
            self._transient.pop(transient_id, None)

    async def permanent_payload(self, transient_id: str) -> str | None:
        return self._permanent.get(transient_id)

    def _take_current(self, owner_id: int, transient_id: str) -> _TransientPayload | None:
        transient = self._transient.pop(transient_id, None)
        if (
            transient is None
            or transient.owner_id != owner_id
            or transient.expires_at <= self._clock()
        ):
            return None
        return transient


__all__ = ["SENSITIVE_CONFIRMATION_TEXT", "SensitiveInputGuard", "SensitiveInspection"]
