from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeErrorCode


class SourceRole(StrEnum):
    EVIDENCE = "evidence"
    TOPIC = "topic"
    FORMAT = "format"
    ANTI_EXAMPLE = "anti_example"


class FetchStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FetchResult:
    source_document_id: str = field(repr=False)
    final_url: str = field(repr=False)
    status: FetchStatus
    fetched_at: datetime
    http_status: int | None
    content_hash: str | None = None
    bounded_excerpt: str | None = field(default=None, repr=False)
    raw_content: bytes | None = field(default=None, repr=False)
    raw_expires_at: datetime | None = None
    error_code: SafeErrorCode | None = None
    sanitized_content: str | None = field(default=None, repr=False)

    @classmethod
    def available(
        cls,
        *,
        source_document_id: str,
        final_url: str,
        content: str,
        fetched_at: datetime,
        http_status: int,
        raw_content: bytes | None = None,
    ) -> FetchResult:
        return cls(
            source_document_id=source_document_id,
            final_url=final_url,
            status=FetchStatus.AVAILABLE,
            fetched_at=fetched_at,
            http_status=http_status,
            content_hash=content_hash(content),
            bounded_excerpt=content[:65_536],
            raw_content=raw_content if raw_content is not None else content.encode("utf-8"),
            raw_expires_at=fetched_at + timedelta(hours=24),
            sanitized_content=content,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        source_document_id: str,
        final_url: str,
        fetched_at: datetime,
        http_status: int | None,
        error_code: SafeErrorCode = SafeErrorCode.SOURCE_UNAVAILABLE,
    ) -> FetchResult:
        return cls(
            source_document_id=source_document_id,
            final_url=final_url,
            status=FetchStatus.UNAVAILABLE,
            fetched_at=fetched_at,
            http_status=http_status,
            error_code=error_code,
        )


__all__ = ["FetchResult", "FetchStatus", "SourceRole"]
