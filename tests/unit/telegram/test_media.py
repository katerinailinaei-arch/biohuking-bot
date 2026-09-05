from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.telegram.media import audio_mime_type, read_clip_bytes, telegram_audio_clip


def test_prefers_spoken_note_over_music_file() -> None:
    spoken = SimpleNamespace(file_id="spoken", mime_type="audio/ogg")
    music = SimpleNamespace(file_id="music", mime_type="audio/mpeg")
    message = SimpleNamespace(audio=music, video_note=None, voice=spoken)

    assert telegram_audio_clip(message) is spoken
    assert audio_mime_type(spoken) == "audio/ogg"


def test_ignores_round_video_notes() -> None:
    message = SimpleNamespace(
        audio=None,
        video_note=SimpleNamespace(file_id="round", mime_type="video/mp4"),
        voice=None,
    )

    assert telegram_audio_clip(message) is None


@pytest.mark.asyncio
async def test_reads_bytes_from_file_id() -> None:
    class FakeBot:
        async def download(self, file: str, timeout: int = 30) -> BytesIO:
            assert file == "AgFILE"
            assert timeout >= 60
            return BytesIO(b"OggS-test")

    payload = await read_clip_bytes(FakeBot(), SimpleNamespace(file_id="AgFILE"))

    assert payload == b"OggS-test"


@pytest.mark.asyncio
async def test_missing_download_is_safe() -> None:
    class FakeBot:
        async def download(self, file: str, timeout: int = 30) -> None:
            del file, timeout
            return None

    with pytest.raises(SafeError) as caught:
        await read_clip_bytes(FakeBot(), SimpleNamespace(file_id="AgFILE"))

    assert caught.value.code is SafeErrorCode.TRANSCRIPTION_FAILED
