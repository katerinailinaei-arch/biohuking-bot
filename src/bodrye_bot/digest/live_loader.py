from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from html import unescape
from re import sub
from time import struct_time
from urllib.parse import urlsplit

import feedparser  # type: ignore[import-untyped]

from bodrye_bot.digest.service import DigestCandidate, PreliminaryRisk, SourceFailure
from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.sources.catalog import (
    AccessMethod,
    SourceCatalog,
    SourceDefinition,
    SourceKind,
    SourceStatus,
)

PageGet = Callable[[str], Awaitable[str]]


class CatalogRssLoader:
    """Load topic cards from allowlisted public RSS feeds."""

    def __init__(
        self,
        *,
        catalog: SourceCatalog | None = None,
        getter: PageGet,
    ) -> None:
        self._catalog = catalog if catalog is not None else SourceCatalog.initial()
        self._getter = getter

    async def load(
        self, *, owner_id: int, digest_date: date
    ) -> tuple[tuple[DigestCandidate, ...], tuple[SourceFailure, ...]]:
        del owner_id, digest_date
        candidates: list[DigestCandidate] = []
        failures: list[SourceFailure] = []
        for source in self._catalog.sources:
            if source.kind is SourceKind.PUBMED_RSS:
                continue
            if source.status is not SourceStatus.ACTIVE:
                continue
            if source.access_method is not AccessMethod.RSS:
                continue
            cards, failure = await _load_source(self._getter, source)
            candidates.extend(cards)
            if failure is not None:
                failures.append(failure)
        return tuple(candidates), tuple(failures)


async def _load_source(
    getter: PageGet, source: SourceDefinition
) -> tuple[tuple[DigestCandidate, ...], SourceFailure | None]:
    try:
        feed_url = str(source.config.get("feed_url", "")).strip()
        target = feed_url or source.canonical_url
        return _candidates_from_feed(source, await getter(target))[:4], None
    except Exception:
        return (), SourceFailure(source.name, "source_unavailable")


def _candidates_from_feed(source: SourceDefinition, xml: str) -> tuple[DigestCandidate, ...]:
    parsed = feedparser.parse(xml)
    cards: list[DigestCandidate] = []
    for entry in parsed.entries:
        link = str(getattr(entry, "link", "") or "").strip()
        title = _plain(str(getattr(entry, "title", "") or ""))
        if not link.startswith("http") or not title:
            continue
        if not _host_allowed(link, source.allowed_hosts):
            continue
        cards.append(
            _card(
                source.name,
                title,
                link,
                _published(entry),
                _entry_summary(entry),
            )
        )
    return tuple(cards)


def _host_allowed(link: str, allowed: tuple[str, ...]) -> bool:
    host = (urlsplit(link).hostname or "").lower().rstrip(".")
    if not host or not allowed:
        return False
    needles = tuple(item.lower().rstrip(".") for item in allowed)
    return any(host == needle or host.endswith(f".{needle}") for needle in needles)


def _card(
    source_name: str,
    title: str,
    link: str,
    published_at: date,
    summary: str = "",
) -> DigestCandidate:
    return DigestCandidate(
        canonical_url=link,
        content_hash=content_hash(f"{link}\n{title}"),
        topic_fingerprint=_fingerprint(title),
        title=title[:200],
        summary=_source_blurb(title, summary),
        rubric=source_name,
        published_at=published_at,
        audience_reason="Тема из разрешённой ленты. Чужой текст не копируем — пишем свой пост.",
        source_roles=(SourceRole.TOPIC,),
        relevance=0.86,
        freshness=0.88,
        source_authority=0.78,
        audience_fit=0.88,
        novelty=0.8,
        preliminary_risk=PreliminaryRisk.GREEN,
    )


def _entry_summary(entry: object) -> str:
    return _plain(str(getattr(entry, "summary", "") or ""))


def _published(entry: object) -> date:
    parsed_time = getattr(entry, "published_parsed", None)
    if isinstance(parsed_time, struct_time):
        return date(parsed_time.tm_year, parsed_time.tm_mon, parsed_time.tm_mday)
    return datetime.now(UTC).date()


def _plain(value: str) -> str:
    text = unescape(sub(r"<[^>]+>", " ", value))
    return sub(r"\s+", " ", text).strip()


def _source_blurb(title: str, summary: str) -> str:
    raw = (summary or title).strip()
    parts = [part.strip() for part in sub(r"[!?]", ".", raw).split(".") if part.strip()]
    head = parts[:2] if parts else [title[:180] or "Тема"]
    if len(head) == 1:
        head.append("Тема для канала, не диагноз")
    return ". ".join(head) + "."


def _fingerprint(title: str) -> str:
    return sub(r"[^a-z0-9а-яё]+", "-", title.lower())[:80].strip("-") or "topic"


__all__ = ["CatalogRssLoader"]
