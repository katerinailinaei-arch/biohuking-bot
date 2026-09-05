from __future__ import annotations

import httpx
from pydantic import SecretStr

from bodrye_bot.domain.errors import SafeError, SafeErrorCode

_LISTEN = "https://api.deepgram.com/v1/listen"
_QUERY = {
    "model": "nova-3",
    "language": "ru",
    "smart_format": "true",
    "punctuate": "true",
}


class DeepgramTranscriber:
    """Pre-recorded listen API; the key never goes into the URL or user copy."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client

    async def transcribe(self, payload: bytes, *, mime_type: str) -> str:
        if not payload:
            raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED)
        headers = {
            "Authorization": f"Token {self._api_key.get_secret_value()}",
            "Content-Type": mime_type or "audio/ogg",
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    _LISTEN, params=_QUERY, headers=headers, content=payload
                )
            else:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        _LISTEN, params=_QUERY, headers=headers, content=payload
                    )
        except httpx.TimeoutException:
            raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED) from None
        except httpx.HTTPError:
            raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED) from None
        if response.status_code >= 400:
            raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED)
        try:
            body = response.json()
            text = str(
                body["results"]["channels"][0]["alternatives"][0]["transcript"]
            ).strip()
        except (KeyError, IndexError, TypeError, ValueError):
            raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED) from None
        if not text:
            raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED)
        return text
