from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from bodrye_bot.domain.manual_post import ManualPost


class ManualPostStore(Protocol):
    async def save(self, post: ManualPost) -> None: ...

    async def get(self, owner_id: int, post_id: UUID) -> ManualPost: ...

    async def latest(self, owner_id: int) -> ManualPost: ...


class DraftWriter(Protocol):
    def write(self, topic: str) -> str: ...


class ChannelPublisher(Protocol):
    async def publish(self, *, owner_id: int, text: str) -> str: ...


Clock = Callable[[], datetime]
