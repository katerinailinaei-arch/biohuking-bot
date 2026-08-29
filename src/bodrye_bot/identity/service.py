from __future__ import annotations

import hmac

from bodrye_bot.domain.errors import SafeError, SafeErrorCode


class OwnerGuard:
    """Checks the sole configured owner before any application lookup."""

    def __init__(self, owner_id: int) -> None:
        self._owner_id = owner_id

    def authorize(self, telegram_id: int) -> int:
        if not hmac.compare_digest(str(telegram_id), str(self._owner_id)):
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        return self._owner_id


__all__ = ["OwnerGuard"]
