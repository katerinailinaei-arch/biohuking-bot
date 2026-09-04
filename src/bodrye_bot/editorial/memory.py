from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.manual_post import ManualPost


@dataclass(frozen=True)
class SentChannelMessage:
    owner_id: int
    text: str
    message_id: str


class InMemoryManualPostStore:
    def __init__(self) -> None:
        self._posts: dict[tuple[int, UUID], ManualPost] = {}
        self._latest: dict[int, UUID] = {}

    async def save(self, post: ManualPost) -> None:
        self._posts[(post.owner_id, post.id)] = post
        self._latest[post.owner_id] = post.id

    async def get(self, owner_id: int, post_id: UUID) -> ManualPost:
        try:
            return self._posts[(owner_id, post_id)]
        except KeyError as error:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION) from error

    async def latest(self, owner_id: int) -> ManualPost:
        post_id = self._latest.get(owner_id)
        if post_id is None:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        return await self.get(owner_id, post_id)


class InMemoryChannelPublisher:
    def __init__(self) -> None:
        self.sent: list[SentChannelMessage] = []

    async def publish(self, *, owner_id: int, text: str) -> str:
        message_id = str(len(self.sent) + 1)
        self.sent.append(SentChannelMessage(owner_id=owner_id, text=text, message_id=message_id))
        return message_id
