from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime
from html import unescape
from json import loads
from re import sub
from time import struct_time
from urllib.parse import quote_plus

import feedparser  # type: ignore[import-untyped]

from bodrye_bot.digest.service import DigestCandidate, PreliminaryRisk, SourceFailure
from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.headlines import russian_summary
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.sources.catalog import AccessMethod, SourceCatalog, SourceKind, SourceStatus

PageGet = Callable[[str], Awaitable[str]]
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class CatalogRssLoader:
    """Load topic cards from allowlisted PubMed queries via NCBI E-utilities or RSS."""

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
            if source.status is not SourceStatus.ACTIVE:
                continue
            if source.kind is not SourceKind.PUBMED_RSS:
                continue
            if source.access_method is not AccessMethod.RSS:
                continue
            query = str(source.config.get("query", "")).strip()
            cards, failure = await _load_source(
                self._getter, source.name, source.canonical_url, query
            )
            candidates.extend(cards)
            if failure is not None:
                failures.append(failure)
        return tuple(candidates), tuple(failures)


async def _load_source(
    getter: PageGet, name: str, url: str, query: str
) -> tuple[tuple[DigestCandidate, ...], SourceFailure | None]:
    try:
        first = await getter(_esearch_url(query) if query else url)
        if _looks_like_json(first):
            ids = _pmids(first)
            if not ids:
                return (), None
            summary = await getter(_esummary_url(ids))
            return _candidates_from_summary(name, summary)[:4], None
        return _candidates_from_feed(name, first)[:4], None
    except Exception:
        return (), SourceFailure(name, "source_unavailable")


def _esearch_url(query: str) -> str:
    return (
        f"{_EUTILS}/esearch.fcgi?db=pubmed&retmode=json&retmax=4"
        f"&sort=date&term={quote_plus(query)}&tool=bodrye-bot"
    )


def _esummary_url(ids: tuple[str, ...]) -> str:
    joined = ",".join(ids)
    return f"{_EUTILS}/esummary.fcgi?db=pubmed&retmode=json&id={joined}&tool=bodrye-bot"


def _looks_like_json(payload: str) -> bool:
    return payload.lstrip().startswith("{")


def _pmids(payload: str) -> tuple[str, ...]:
    parsed = loads(payload)
    result = parsed.get("esearchresult")
    if not isinstance(result, dict) or "idlist" not in result:
        raise ValueError("ncbi esearch rejected query")
    raw = result["idlist"]
    return tuple(str(item) for item in raw if str(item).isdigit())


def _candidates_from_summary(source_name: str, payload: str) -> tuple[DigestCandidate, ...]:
    parsed = loads(payload)
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise ValueError("ncbi esummary rejected query")
    cards: list[DigestCandidate] = []
    for pmid in result.get("uids", ()):
        record = result.get(str(pmid))
        if not isinstance(record, Mapping):
            continue
        title = _plain(str(record.get("title", "")))
        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if not title:
            continue
        cards.append(_card(source_name, title, link, _summary_date(record)))
    return tuple(cards)


def _candidates_from_feed(source_name: str, xml: str) -> tuple[DigestCandidate, ...]:
    parsed = feedparser.parse(xml)
    cards: list[DigestCandidate] = []
    for entry in parsed.entries:
        link = str(getattr(entry, "link", "") or "").strip()
        title = _plain(str(getattr(entry, "title", "") or ""))
        if not link.startswith("http") or not title:
            continue
        cards.append(
            _card(source_name, title, link, _published(entry), _entry_summary(entry))
        )
    return tuple(cards)


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
        summary=russian_summary(title, source_name, summary),
        rubric=source_name,
        published_at=published_at,
        audience_reason=("Тема из разрешённого PubMed; Кети решает, брать ли её в выпуск."),
        source_roles=(SourceRole.TOPIC,),
        relevance=0.86,
        freshness=0.88,
        source_authority=0.9,
        audience_fit=0.84,
        novelty=0.8,
        preliminary_risk=PreliminaryRisk.GREEN,
    )


def _entry_summary(entry: object) -> str:
    return _plain(str(getattr(entry, "summary", "") or ""))


def _summary_date(record: Mapping[str, object]) -> date:
    raw = str(record.get("sortpubdate") or record.get("pubdate") or "")
    match = sub(r"^(\d{4}).*", r"\1", raw)
    try:
        year = int(match[:4])
        return date(year, 1, 1)
    except ValueError:
        return datetime.now(UTC).date()


def _published(entry: object) -> date:
    parsed_time = getattr(entry, "published_parsed", None)
    if isinstance(parsed_time, struct_time):
        return date(parsed_time.tm_year, parsed_time.tm_mon, parsed_time.tm_mday)
    return datetime.now(UTC).date()


def _plain(value: str) -> str:
    text = unescape(sub(r"<[^>]+>", " ", value))
    return sub(r"\s+", " ", text).strip()


def _fingerprint(title: str) -> str:
    return sub(r"[^a-z0-9а-яё]+", "-", title.lower())[:80].strip("-") or "topic"


__all__ = ["CatalogRssLoader"]
