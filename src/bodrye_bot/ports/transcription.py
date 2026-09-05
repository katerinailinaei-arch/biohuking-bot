from __future__ import annotations

from typing import Protocol


class AudioTranscriber(Protocol):
    async def transcribe(self, payload: bytes, *, mime_type: str) -> str: ...
