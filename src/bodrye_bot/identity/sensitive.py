from __future__ import annotations

import asyncio
import re
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
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
    warning_text: str | None = None
    confirmation_action: str | None = None


@dataclass(frozen=True)
class _TransientPayload:
    owner_id: int
    payload: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class _PermanentPayload:
    owner_id: int
    payload: str = field(repr=False)


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
        self._expiration_handles: dict[str, asyncio.TimerHandle] = {}
        self._permanent: dict[str, _PermanentPayload] = {}

    async def inspect(self, owner_id: int, payload: str) -> SensitiveInspection:
        self._owner_guard.authorize(owner_id)
        self._purge_expired()
        transient_id = uuid4().hex
        if _SENSITIVE_PATTERN.search(payload):
            transient = _TransientPayload(
                owner_id=owner_id,
                payload=payload,
                expires_at=self._clock() + self._ttl,
            )
            self._transient[transient_id] = transient
            self._schedule_expiration(transient_id, transient.expires_at)
            return SensitiveInspection(
                transient_id=transient_id,
                requires_confirmation=True,
                warning_text=(
                    "Похоже, в материале есть личные медицинские данные. "
                    f"Чтобы сохранить его, выберите: {SENSITIVE_CONFIRMATION_TEXT}."
                ),
                confirmation_action=SENSITIVE_CONFIRMATION_TEXT,
            )

        self._permanent[transient_id] = _PermanentPayload(owner_id=owner_id, payload=payload)
        return SensitiveInspection(transient_id=transient_id, requires_confirmation=False)

    async def confirm(self, owner_id: int, transient_id: str, confirmation: str) -> bool:
        self._owner_guard.authorize(owner_id)
        self._purge_expired()
        if confirmation != SENSITIVE_CONFIRMATION_TEXT:
            return False
        transient = self._take_current(owner_id, transient_id)
        if transient is None:
            return False
        self._permanent[transient_id] = _PermanentPayload(
            owner_id=owner_id, payload=transient.payload
        )
        return True

    async def cancel(self, owner_id: int, transient_id: str) -> None:
        self._owner_guard.authorize(owner_id)
        self._purge_expired()
        transient = self._transient.get(transient_id)
        if transient is not None and transient.owner_id == owner_id:
            self._discard_transient(transient_id)

    async def transient_payload(self, owner_id: int, transient_id: str) -> str | None:
        self._owner_guard.authorize(owner_id)
        self._purge_expired()
        transient = self._transient.get(transient_id)
        if transient is None or transient.owner_id != owner_id:
            return None
        return transient.payload

    async def permanent_payload(self, owner_id: int, transient_id: str) -> str | None:
        self._owner_guard.authorize(owner_id)
        self._purge_expired()
        permanent = self._permanent.get(transient_id)
        if permanent is None or permanent.owner_id != owner_id:
            return None
        return permanent.payload

    async def purge_expired(self) -> int:
        return self._purge_expired()

    def _take_current(self, owner_id: int, transient_id: str) -> _TransientPayload | None:
        transient = self._transient.get(transient_id)
        if (
            transient is None
            or transient.owner_id != owner_id
            or transient.expires_at <= self._clock()
        ):
            return None
        self._discard_transient(transient_id)
        return transient

    def _purge_expired(self) -> int:
        now = self._clock()
        expired_ids = [
            transient_id
            for transient_id, transient in self._transient.items()
            if transient.expires_at <= now
        ]
        for transient_id in expired_ids:
            self._discard_transient(transient_id)
        return len(expired_ids)

    def _schedule_expiration(self, transient_id: str, expires_at: datetime) -> None:
        delay = max(0.0, (expires_at - self._clock()).total_seconds())
        guard_ref = weakref.ref(self)
        handle = asyncio.get_running_loop().call_later(
            delay, _expire_transient, guard_ref, transient_id
        )
        previous = self._expiration_handles.pop(transient_id, None)
        if previous is not None:
            previous.cancel()
        self._expiration_handles[transient_id] = handle

    def _expire_transient(self, transient_id: str) -> None:
        self._expiration_handles.pop(transient_id, None)
        self._transient.pop(transient_id, None)

    def _discard_transient(self, transient_id: str) -> None:
        self._transient.pop(transient_id, None)
        handle = self._expiration_handles.pop(transient_id, None)
        if handle is not None:
            handle.cancel()


def _expire_transient(
    guard_ref: weakref.ReferenceType[SensitiveInputGuard], transient_id: str
) -> None:
    guard = guard_ref()
    if guard is not None:
        guard._expire_transient(transient_id)


__all__ = ["SENSITIVE_CONFIRMATION_TEXT", "SensitiveInputGuard", "SensitiveInspection"]
