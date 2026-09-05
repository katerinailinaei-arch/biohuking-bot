from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from bodrye_bot.editorial.studio import StudioKind


class StudioWait(StrEnum):
    TOPIC = "topic"
    REVISE = "revise"
    TONE = "tone"


@dataclass
class StudioSession:
    kind: StudioKind
    wait: StudioWait
    topic: str = ""
    last_text: str = ""
    variant: int = 1
    post_id: UUID | None = None


class StudioSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[int, StudioSession] = {}

    def get(self, owner_id: int) -> StudioSession | None:
        return self._sessions.get(owner_id)

    def put(self, owner_id: int, session: StudioSession) -> None:
        self._sessions[owner_id] = session

    def clear(self, owner_id: int) -> None:
        self._sessions.pop(owner_id, None)


__all__ = ["StudioSession", "StudioSessionStore", "StudioWait"]
