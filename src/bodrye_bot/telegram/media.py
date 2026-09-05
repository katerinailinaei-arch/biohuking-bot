from __future__ import annotations

from typing import Any

from bodrye_bot.domain.errors import SafeError, SafeErrorCode


def telegram_audio_clip(message: Any) -> Any | None:
    return getattr(message, "voice", None) or getattr(message, "audio", None)


def audio_mime_type(clip: Any) -> str:
    mime = getattr(clip, "mime_type", None)
    if isinstance(mime, str) and mime.strip():
        return mime
    return "audio/ogg"


async def read_clip_bytes(bot: Any, clip: Any) -> bytes:
    file_id = getattr(clip, "file_id", None)
    if not isinstance(file_id, str) or not file_id.strip():
        raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED)
    try:
        downloaded = await bot.download(file_id, timeout=60)
        payload = downloaded.read() if downloaded is not None else b""
    except SafeError:
        raise
    except Exception:
        raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED) from None
    if not payload:
        raise SafeError.for_code(SafeErrorCode.TRANSCRIPTION_FAILED)
    return payload
