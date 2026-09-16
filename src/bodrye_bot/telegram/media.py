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
    return await _download_bytes(bot, file_id, SafeErrorCode.TRANSCRIPTION_FAILED)


def telegram_image_file_id(message: Any) -> str | None:
    photos = getattr(message, "photo", None) or ()
    if photos:
        file_id = getattr(photos[-1], "file_id", None)
        if isinstance(file_id, str) and file_id.strip():
            return file_id
    document = getattr(message, "document", None)
    mime = getattr(document, "mime_type", None) or ""
    if isinstance(mime, str) and mime.startswith("image/"):
        file_id = getattr(document, "file_id", None)
        if isinstance(file_id, str) and file_id.strip():
            return file_id
    return None


async def read_image_bytes(bot: Any, file_id: str) -> bytes:
    if not file_id.strip():
        raise SafeError.for_code(SafeErrorCode.COVER_FAILED)
    return await _download_bytes(bot, file_id, SafeErrorCode.COVER_FAILED)


async def _download_bytes(bot: Any, file_id: str, error: SafeErrorCode) -> bytes:
    try:
        downloaded = await bot.download(file_id, timeout=60)
        payload = downloaded.read() if downloaded is not None else b""
    except SafeError:
        raise
    except Exception:
        raise SafeError.for_code(error) from None
    if not payload:
        raise SafeError.for_code(error)
    return payload
