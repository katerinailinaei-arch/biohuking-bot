from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LocalizedCopy:
    headline: str
    summary: str


class TopicLocalizer(Protocol):
    async def localize_batch(
        self, items: tuple[tuple[str, str], ...]
    ) -> tuple[LocalizedCopy | None, ...]: ...


__all__ = ["LocalizedCopy", "TopicLocalizer"]
