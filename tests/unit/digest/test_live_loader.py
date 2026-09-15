from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bodrye_bot.digest.live_loader import CatalogRssLoader
from bodrye_bot.digest.service import DigestService
from bodrye_bot.sources.catalog import (
    SourceCatalog,
    SourceDefinition,
    SourceKind,
    SourceStatus,
)

_NPLUS_RSS = """
<rss version="2.0"><channel>
<title>N+1</title>
<item>
<title>Почему колени любят лестницу</title>
<link>https://nplus1.ru/news/knees</link>
<description>Короткий научпоп про суставы.</description>
</item>
<item>
<title>Чужой ролик</title>
<link>https://evil.example/video</link>
<description>Should be dropped.</description>
</item>
</channel></rss>
"""


def _active_nplus() -> SourceCatalog:
    seed = next(source for source in SourceCatalog.initial().sources if source.name == "N+1")
    active = SourceDefinition(
        name=seed.name,
        canonical_url=seed.canonical_url,
        kind=seed.kind,
        roles=seed.roles,
        access_method=seed.access_method,
        status=SourceStatus.ACTIVE,
        version=seed.version,
        license_note=seed.license_note,
        checked_at=seed.checked_at,
        allowed_hosts=seed.allowed_hosts,
        config=dict(seed.config),
    )
    return SourceCatalog(version="test-nplus", sources=(active,))


def _forced_pubmed() -> SourceCatalog:
    seed = next(
        source for source in SourceCatalog.initial().sources if source.kind is SourceKind.PUBMED_RSS
    )
    pubmed = SourceDefinition(
        name=seed.name,
        canonical_url=seed.canonical_url,
        kind=seed.kind,
        roles=seed.roles,
        access_method=seed.access_method,
        status=SourceStatus.ACTIVE,
        version=seed.version,
        license_note=seed.license_note,
        checked_at=seed.checked_at,
        allowed_hosts=seed.allowed_hosts,
        config=dict(seed.config),
    )
    return SourceCatalog(version="test-pubmed", sources=(pubmed,))


async def _nplus_get(url: str) -> str:
    assert "eutils.ncbi" not in url
    assert "pubmed.ncbi.nlm.nih.gov" not in url
    return _NPLUS_RSS


async def _ncbi_get(url: str) -> str:
    del url
    raise AssertionError("PubMed must not be fetched")


@pytest.mark.asyncio
async def test_topic_rss_becomes_digest_cards_and_drops_off_host_links() -> None:
    loader = CatalogRssLoader(catalog=_active_nplus(), getter=_nplus_get)
    candidates, failures = await loader.load(owner_id=42, digest_date=datetime.now(UTC).date())
    digest = DigestService().build(
        candidates, digest_date=datetime.now(UTC).date(), source_failures=failures
    )

    assert failures == ()
    nplus = [card for card in digest.cards if "nplus1.ru" in card.provenance_urls[0]]
    assert nplus
    assert all("evil.example" not in card.provenance_urls[0] for card in digest.cards)
    assert all("pubmed.ncbi.nlm.nih.gov" not in card.provenance_urls[0] for card in digest.cards)


@pytest.mark.asyncio
async def test_pubmed_is_ignored_even_if_marked_active() -> None:
    loader = CatalogRssLoader(catalog=_forced_pubmed(), getter=_ncbi_get)
    candidates, failures = await loader.load(owner_id=42, digest_date=datetime.now(UTC).date())

    assert failures == ()
    assert candidates == ()


def test_initial_catalog_retires_pubmed() -> None:
    pubmed = [
        source
        for source in SourceCatalog.initial().sources
        if source.kind is SourceKind.PUBMED_RSS
    ]
    assert pubmed
    assert all(source.status is SourceStatus.RETIRED for source in pubmed)
