from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self
from urllib.parse import quote_plus

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.domain.workflow import Actor
from bodrye_bot.operations.audit import AuditEntry, AuditEventType, AuditObjectType
from bodrye_bot.ports.repositories import AuditWriter


class SourceKind(StrEnum):
    WEB = "web"
    PUBMED_RSS = "pubmed_rss"
    TELEGRAM_MANUAL = "telegram_manual"


class AccessMethod(StrEnum):
    MANUAL_SEARCH = "manual_search"
    FETCH = "fetch"
    RSS = "rss"
    OWNER_FORWARDED_OR_EXPLICIT_LINK = "owner_forwarded_or_explicit_link"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    MANUAL = "manual"
    RETIRED = "retired"


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    canonical_url: str
    kind: SourceKind
    roles: tuple[SourceRole, ...]
    access_method: AccessMethod
    status: SourceStatus
    version: str
    license_note: str
    checked_at: datetime
    allowed_hosts: tuple[str, ...] = ()
    config: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))
        if not self.roles:
            raise ValueError("Source needs at least one role")
        if self.kind is SourceKind.TELEGRAM_MANUAL:
            if SourceRole.EVIDENCE in self.roles:
                raise ValueError("Telegram source cannot be evidence")
            if self.access_method is not AccessMethod.OWNER_FORWARDED_OR_EXPLICIT_LINK:
                raise ValueError("Telegram source must be owner-forwarded or explicit link")
        if SourceRole.EVIDENCE in self.roles and not self.allowed_hosts:
            raise ValueError("Evidence source needs an explicit host allowlist")

    @property
    def is_evidence(self) -> bool:
        return SourceRole.EVIDENCE in self.roles


@dataclass(frozen=True)
class SourceCatalog:
    version: str
    sources: tuple[SourceDefinition, ...]

    @classmethod
    def initial(cls) -> SourceCatalog:
        checked_at = datetime(2026, 9, 7, tzinfo=UTC)
        standard = "Registry metadata checked during onboarding; follow publisher terms."
        title_link_only = (
            "Digest may use the public RSS title and canonical link only. "
            "Do not copy article body, photos, video, or jokes. Write original posts."
        )
        telegram_forward = (
            "Owner-forwarded message or explicit link only. "
            "No scraping, no republication, no evidence role."
        )
        telegram_blocked = (
            "Blocked: no copy, scrape, or digest ingest. "
            "Public or invite Telegram is not a license to reuse content."
        )
        return cls(
            version="source-registry-v4",
            sources=(
                _web(
                    "Минздрав РФ: клинические рекомендации",
                    "https://cr.minzdrav.gov.ru/",
                    (SourceRole.EVIDENCE,),
                    AccessMethod.MANUAL_SEARCH,
                    SourceStatus.MANUAL,
                    "minzdrav-v1",
                    "cr.minzdrav.gov.ru",
                    checked_at,
                    standard,
                ),
                _web(
                    "WHO Fact Sheets",
                    "https://www.who.int/news-room/fact-sheets",
                    (SourceRole.EVIDENCE, SourceRole.TOPIC),
                    AccessMethod.FETCH,
                    SourceStatus.ACTIVE,
                    "who-facts-v1",
                    "www.who.int",
                    checked_at,
                    standard,
                ),
                _rss_topic(
                    "WHO News",
                    "https://www.who.int/news",
                    "https://www.who.int/rss-feeds/news-english.xml",
                    "who-news-v2",
                    ("www.who.int", "who.int"),
                    checked_at,
                    title_link_only,
                    SourceStatus.RETIRED,
                ),
                _web(
                    "USPSTF",
                    "https://www.uspreventiveservicestaskforce.org/",
                    (SourceRole.EVIDENCE,),
                    AccessMethod.FETCH,
                    SourceStatus.ACTIVE,
                    "uspstf-v1",
                    "www.uspreventiveservicestaskforce.org",
                    checked_at,
                    standard,
                ),
                _web(
                    "NICE",
                    "https://www.nice.org.uk/",
                    (SourceRole.EVIDENCE,),
                    AccessMethod.FETCH,
                    SourceStatus.ACTIVE,
                    "nice-v1",
                    "www.nice.org.uk",
                    checked_at,
                    standard,
                ),
                _web(
                    "Cochrane Reviews",
                    "https://www.cochranelibrary.com/",
                    (SourceRole.EVIDENCE,),
                    AccessMethod.FETCH,
                    SourceStatus.ACTIVE,
                    "cochrane-v1",
                    "www.cochranelibrary.com",
                    checked_at,
                    standard,
                ),
                _rss_topic(
                    "MedlinePlus: новое о здоровье",
                    "https://medlineplus.gov/",
                    "https://medlineplus.gov/feeds/whatsnew.xml",
                    "medlineplus-v1",
                    ("medlineplus.gov", "www.medlineplus.gov"),
                    checked_at,
                    "U.S. government health pages; title and link for topics, "
                    "not medical evidence.",
                    SourceStatus.RETIRED,
                ),
                _rss_topic(
                    "N+1",
                    "https://nplus1.ru/",
                    "https://nplus1.ru/rss",
                    "nplus1-v1",
                    ("nplus1.ru", "www.nplus1.ru"),
                    checked_at,
                    title_link_only,
                    SourceStatus.RETIRED,
                ),
                _rss_topic(
                    "Naked Science",
                    "https://naked-science.ru/",
                    "https://naked-science.ru/feed",
                    "naked-science-v1",
                    ("naked-science.ru", "www.naked-science.ru"),
                    checked_at,
                    title_link_only,
                    SourceStatus.RETIRED,
                ),
                _rss_topic(
                    "The Conversation: Health",
                    "https://theconversation.com/uk/health",
                    "https://theconversation.com/uk/health/articles.atom",
                    "conversation-health-v1",
                    ("theconversation.com", "www.theconversation.com"),
                    checked_at,
                    title_link_only + " Attribution required if quoting a short excerpt.",
                    SourceStatus.RETIRED,
                ),
                _pubmed(
                    "движение и активное долголетие",
                    "physical activity AND healthy aging",
                    checked_at,
                    standard,
                ),
                _pubmed("сон и восстановление", "sleep AND recovery", checked_at, standard),
                _pubmed(
                    "питание и метаболическое здоровье",
                    "nutrition AND metabolic health",
                    checked_at,
                    standard,
                ),
                _telegram(
                    "Telegram: любой пост от Кети",
                    "https://t.me/",
                    SourceStatus.MANUAL,
                    "telegram-manual-v2",
                    checked_at,
                    telegram_forward,
                    (SourceRole.TOPIC, SourceRole.FORMAT, SourceRole.ANTI_EXAMPLE),
                ),
                _telegram(
                    "Telegram: Коллеги, шутки кончились",
                    "https://t.me/kollegi_joke",
                    SourceStatus.MANUAL,
                    "telegram-kollegi-v1",
                    checked_at,
                    telegram_forward,
                    (SourceRole.FORMAT, SourceRole.ANTI_EXAMPLE),
                ),
                _telegram(
                    "Telegram: лёгкий зож",
                    "https://t.me/easyzozh",
                    SourceStatus.MANUAL,
                    "telegram-easyzozh-v1",
                    checked_at,
                    telegram_forward,
                    (SourceRole.TOPIC, SourceRole.FORMAT),
                ),
                _telegram(
                    "Telegram: Хало, а ю хелси?",
                    "https://t.me/haloareyouhealthy",
                    SourceStatus.MANUAL,
                    "telegram-halo-v1",
                    checked_at,
                    telegram_forward,
                    (SourceRole.TOPIC, SourceRole.FORMAT),
                ),
                _telegram(
                    "Telegram: ВРЕМЯ ЖИТЬ",
                    "https://t.me/vremya_zhit_now",
                    SourceStatus.MANUAL,
                    "telegram-vremya-zhit-v1",
                    checked_at,
                    telegram_forward,
                    (SourceRole.TOPIC, SourceRole.FORMAT),
                ),
                _telegram(
                    "Telegram: СЪЕШЬТЕ ЭТО МЕДЛЕННО",
                    "https://t.me/shkarupaendo",
                    SourceStatus.MANUAL,
                    "telegram-shkarupa-v1",
                    checked_at,
                    telegram_forward,
                    (SourceRole.FORMAT, SourceRole.ANTI_EXAMPLE),
                ),
                _telegram(
                    "Telegram: Кот Шрёдингера (агрегатор) — запрещён",
                    "https://t.me/SchroodingerCat",
                    SourceStatus.RETIRED,
                    "telegram-schroodingercat-blocked-v1",
                    checked_at,
                    telegram_blocked,
                    (SourceRole.ANTI_EXAMPLE,),
                ),
                _telegram(
                    "Telegram: Фитнес меню — запрещён",
                    "https://t.me/+sKU7kz_opcplNzY6",
                    SourceStatus.RETIRED,
                    "telegram-fitness-menu-blocked-v1",
                    checked_at,
                    telegram_blocked,
                    (SourceRole.ANTI_EXAMPLE,),
                ),
            ),
        )

    def blocked_telegram_handles(self) -> frozenset[str]:
        return frozenset(
            handle
            for source in self.sources
            if source.kind is SourceKind.TELEGRAM_MANUAL
            and source.status is SourceStatus.RETIRED
            for handle in (_telegram_handle(source.canonical_url),)
            if handle
        )

    def inspiration_telegram_handles(self) -> frozenset[str]:
        return frozenset(
            handle
            for source in self.sources
            if source.kind is SourceKind.TELEGRAM_MANUAL
            and source.status is SourceStatus.MANUAL
            for handle in (_telegram_handle(source.canonical_url),)
            if handle
        )


class SourceCatalogRepository(Protocol):
    async def save(self, owner_id: int, catalog: SourceCatalog) -> None: ...


class SourceCatalogUnitOfWork(Protocol):
    catalogs: SourceCatalogRepository
    audit: AuditWriter

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    async def commit(self) -> None: ...


class SourceCatalogUpdater:
    def __init__(self, *, uow: SourceCatalogUnitOfWork) -> None:
        self._uow = uow

    async def update_pubmed_queries(
        self,
        *,
        owner_id: int,
        current: SourceCatalog,
        version: str,
        queries: tuple[str, str, str],
    ) -> SourceCatalog:
        if (
            not version
            or version == current.version
            or not isinstance(queries, tuple)
            or len(queries) != 3
            or any(not isinstance(query, str) or not query.strip() for query in queries)
        ):
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        query_version = version.replace("source-registry", "pubmed-rss")
        iterator = iter(queries)
        sources = tuple(
            replace_source_query(source, next(iterator), query_version)
            if source.kind is SourceKind.PUBMED_RSS
            else source
            for source in current.sources
        )
        updated = SourceCatalog(version=version, sources=sources)
        async with self._uow as uow:
            await uow.catalogs.save(owner_id, updated)
            await uow.audit.record(
                AuditEntry(
                    owner_id=owner_id,
                    event_type=AuditEventType.CONFIGURATION_CHANGED,
                    actor=Actor.OWNER,
                    object_type=AuditObjectType.CONFIGURATION,
                    metadata={
                        "registry_version": version,
                        "pubmed_query_version": query_version,
                        "source_count": len(updated.sources),
                    },
                )
            )
            await uow.commit()
        return updated


def replace_source_query(
    source: SourceDefinition, query: str, query_version: str
) -> SourceDefinition:
    return SourceDefinition(
        name=source.name,
        canonical_url=_pubmed_url(query),
        kind=source.kind,
        roles=source.roles,
        access_method=source.access_method,
        status=source.status,
        version=query_version,
        license_note=source.license_note,
        checked_at=source.checked_at,
        allowed_hosts=source.allowed_hosts,
        config={"query_version": query_version, "query": query},
    )


def _web(
    name: str,
    canonical_url: str,
    roles: tuple[SourceRole, ...],
    access_method: AccessMethod,
    status: SourceStatus,
    version: str,
    allowed_host: str,
    checked_at: datetime,
    license_note: str,
) -> SourceDefinition:
    return SourceDefinition(
        name=name,
        canonical_url=canonical_url,
        kind=SourceKind.WEB,
        roles=roles,
        access_method=access_method,
        status=status,
        version=version,
        license_note=license_note,
        checked_at=checked_at,
        allowed_hosts=(allowed_host,),
    )


def _rss_topic(
    name: str,
    canonical_url: str,
    feed_url: str,
    version: str,
    allowed_hosts: tuple[str, ...],
    checked_at: datetime,
    license_note: str,
    status: SourceStatus = SourceStatus.ACTIVE,
) -> SourceDefinition:
    return SourceDefinition(
        name=name,
        canonical_url=canonical_url,
        kind=SourceKind.WEB,
        roles=(SourceRole.TOPIC,),
        access_method=AccessMethod.RSS,
        status=status,
        version=version,
        license_note=license_note,
        checked_at=checked_at,
        allowed_hosts=allowed_hosts,
        config={"feed_url": feed_url},
    )


def _telegram_handle(canonical_url: str) -> str | None:
    prefix = "https://t.me/"
    if not canonical_url.startswith(prefix):
        return None
    rest = canonical_url[len(prefix) :].split("/", 1)[0].strip()
    if not rest or rest.startswith("+"):
        return None
    return rest.lower()


def _telegram(
    name: str,
    canonical_url: str,
    status: SourceStatus,
    version: str,
    checked_at: datetime,
    license_note: str,
    roles: tuple[SourceRole, ...],
) -> SourceDefinition:
    return SourceDefinition(
        name=name,
        canonical_url=canonical_url,
        kind=SourceKind.TELEGRAM_MANUAL,
        roles=roles,
        access_method=AccessMethod.OWNER_FORWARDED_OR_EXPLICIT_LINK,
        status=status,
        version=version,
        license_note=license_note,
        checked_at=checked_at,
        allowed_hosts=("t.me",),
    )


def _pubmed(name: str, query: str, checked_at: datetime, license_note: str) -> SourceDefinition:
    del license_note
    return SourceDefinition(
        f"PubMed RSS: {name}",
        _pubmed_url(query),
        SourceKind.PUBMED_RSS,
        (SourceRole.TOPIC,),
        AccessMethod.RSS,
        SourceStatus.RETIRED,
        "pubmed-rss-v1",
        "Owner cancelled PubMed: not used for digest or topic search.",
        checked_at,
        ("pubmed.ncbi.nlm.nih.gov",),
        {"query_version": "pubmed-rss-v1", "query": query},
    )


def _pubmed_url(query: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/rss/?term={quote_plus(query)}"


__all__ = [
    "AccessMethod",
    "SourceCatalog",
    "SourceCatalogUpdater",
    "SourceDefinition",
    "SourceKind",
    "SourceStatus",
]
