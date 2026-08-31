from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import bleach  # type: ignore[import-untyped]

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.sources import FetchResult
from bodrye_bot.sources.catalog import SourceDefinition

MAX_ENCODED_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT_SECONDS = 5
TOTAL_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class TransportRequest:
    url: str
    pinned_ip: str
    host_header: str
    connect_timeout_seconds: int = CONNECT_TIMEOUT_SECONDS
    total_timeout_seconds: int = TOTAL_TIMEOUT_SECONDS


class Resolver(Protocol):
    async def resolve(self, hostname: str) -> tuple[str, ...]: ...


class Transport(Protocol):
    async def request(self, request: TransportRequest) -> HttpResponse: ...


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver,
        transport: Transport,
        now: Callable[[], datetime],
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._now = now

    async def fetch(self, url: str, source: SourceDefinition) -> FetchResult:
        current_url = self._validate_url(url, source)
        redirects = 0
        while True:
            parsed = urlsplit(current_url)
            assert parsed.hostname is not None
            try:
                pinned_ip = await self._resolve_safe_ip(parsed.hostname)
                response = await self._transport.request(
                    TransportRequest(
                        url=current_url,
                        pinned_ip=pinned_ip,
                        host_header=parsed.hostname,
                    )
                )
            except SafeError:
                raise
            except (OSError, TimeoutError):
                return FetchResult.unavailable(
                    source_document_id=_document_id(current_url),
                    final_url=current_url,
                    fetched_at=self._now(),
                    http_status=None,
                )

            location = _header(response.headers, "location")
            if response.status_code in {301, 302, 303, 307, 308} and location is not None:
                if redirects >= MAX_REDIRECTS:
                    return self._unavailable(current_url, response.status_code)
                redirects += 1
                current_url = self._validate_url(urljoin(current_url, location), source)
                continue
            if not 200 <= response.status_code < 300 or len(response.body) > MAX_ENCODED_BYTES:
                return self._unavailable(current_url, response.status_code)

            content = _sanitize_response(response)
            return FetchResult.available(
                source_document_id=_document_id(current_url),
                final_url=current_url,
                content=content,
                fetched_at=self._now(),
                http_status=response.status_code,
                raw_content=response.body,
            )

    def _unavailable(self, url: str, status_code: int) -> FetchResult:
        return FetchResult.unavailable(
            source_document_id=_document_id(url),
            final_url=url,
            fetched_at=self._now(),
            http_status=status_code,
        )

    def _validate_url(self, url: str, source: SourceDefinition) -> str:
        try:
            parsed = urlsplit(url)
            _ = parsed.port
        except ValueError:
            raise SafeError.for_code(SafeErrorCode.SOURCE_BLOCKED) from None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise SafeError.for_code(SafeErrorCode.SOURCE_BLOCKED)
        hostname = parsed.hostname.lower().rstrip(".")
        if source.allowed_hosts and not any(
            hostname == allowed or hostname.endswith(f".{allowed}")
            for allowed in source.allowed_hosts
        ):
            raise SafeError.for_code(SafeErrorCode.SOURCE_BLOCKED)
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise SafeError.for_code(SafeErrorCode.SOURCE_BLOCKED)
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, "")
        )

    async def _resolve_safe_ip(self, hostname: str) -> str:
        try:
            addresses = await self._resolver.resolve(hostname)
        except OSError:
            raise SafeError.for_code(SafeErrorCode.SOURCE_UNAVAILABLE) from None
        if not addresses:
            raise SafeError.for_code(SafeErrorCode.SOURCE_UNAVAILABLE)
        parsed = tuple(_public_ip(address) for address in addresses)
        return parsed[0]


def _public_ip(address: str) -> str:
    try:
        candidate = ipaddress.ip_address(address)
    except ValueError:
        raise SafeError.for_code(SafeErrorCode.SOURCE_BLOCKED) from None
    if not candidate.is_global:
        raise SafeError.for_code(SafeErrorCode.SOURCE_BLOCKED)
    return str(candidate)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _sanitize_response(response: HttpResponse) -> str:
    decoded = response.body.decode("utf-8", errors="replace")
    if "html" not in (_header(response.headers, "content-type") or "").lower():
        return " ".join(decoded.split())[:65_536]
    parser = _VisibleText()
    parser.feed(decoded)
    visible_text = " ".join(parser.parts)
    return " ".join(bleach.clean(visible_text, tags=[], attributes={}, strip=True).split())[:65_536]


def _document_id(url: str) -> str:
    return f"fetched:{url}"


__all__ = ["HttpResponse", "SafeFetcher", "TransportRequest"]
