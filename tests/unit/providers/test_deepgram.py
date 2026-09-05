from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.providers.deepgram import DeepgramTranscriber


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": {
                "channels": [{"alternatives": [{"transcript": "  сон после 35  "}]}]
            }
        },
    )


@pytest.mark.asyncio
async def test_deepgram_returns_trimmed_transcript_and_keeps_key_out_of_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await DeepgramTranscriber(SecretStr("dg-secret"), client=client).transcribe(
            b"OggS-test", mime_type="audio/ogg"
        )

    assert text == "сон после 35"
    assert "dg-secret" not in str(captured[0].url)
    assert captured[0].url.params["model"] == "nova-3"
    assert captured[0].url.params["language"] == "ru"
    assert captured[0].headers["Authorization"] == "Token dg-secret"


@pytest.mark.asyncio
async def test_deepgram_http_error_is_safe() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SafeError) as caught:
            await DeepgramTranscriber(SecretStr("dg-secret"), client=client).transcribe(
                b"OggS-test", mime_type="audio/ogg"
            )

    assert caught.value.code is SafeErrorCode.TRANSCRIPTION_FAILED
    assert "nope" not in caught.value.message_ru
    assert "dg-secret" not in caught.value.message_ru
