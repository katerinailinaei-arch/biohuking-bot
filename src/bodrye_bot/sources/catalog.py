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
        checked_at = datetime(2026, 8, 28, tzinfo=UTC)
        standard = "Registry metadata checked during onboarding; follow publisher terms."
        return cls(
            version="source-registry-v1",
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
                _web(
                    "WHO News",
                    "https://www.who.int/news",
                    (SourceRole.TOPIC,),
                    AccessMethod.FETCH,
                    SourceStatus.ACTIVE,
                    "who-news-v1",
                    "www.who.int",
                    checked_at,
                    standard,
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
                SourceDefinition(
                    name="Telegram: вручную утверждённые источники",
                    canonical_url="https://t.me/",
                    kind=SourceKind.TELEGRAM_MANUAL,
                    roles=(SourceRole.TOPIC, SourceRole.FORMAT, SourceRole.ANTI_EXAMPLE),
                    access_method=AccessMethod.OWNER_FORWARDED_OR_EXPLICIT_LINK,
                    status=SourceStatus.MANUAL,
                    version="telegram-manual-v1",
                    license_note=(
                        "Only owner-forwarded messages or explicit links; "
                        "no scraping or credentials."
                    ),
                    checked_at=checked_at,
                    allowed_hosts=("t.me",),
                ),
            ),
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
        canonical_url=source.canonical_url,
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


def _pubmed(name: str, query: str, checked_at: datetime, license_note: str) -> SourceDefinition:
    return SourceDefinition(
        f"PubMed RSS: {name}",
        f"https://pubmed.ncbi.nlm.nih.gov/rss/?term={quote_plus(query)}",
        SourceKind.PUBMED_RSS,
        (SourceRole.TOPIC,),
        AccessMethod.RSS,
        SourceStatus.ACTIVE,
        "pubmed-rss-v1",
        license_note,
        checked_at,
        ("pubmed.ncbi.nlm.nih.gov",),
        {"query_version": "pubmed-rss-v1", "query": query},
    )


__all__ = [
    "AccessMethod",
    "SourceCatalog",
    "SourceCatalogUpdater",
    "SourceDefinition",
    "SourceKind",
    "SourceStatus",
]
