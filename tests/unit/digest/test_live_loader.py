from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bodrye_bot.digest.live_loader import CatalogRssLoader
from bodrye_bot.digest.service import DigestService
from bodrye_bot.sources.catalog import SourceCatalog

_RSS = """
<rss version="2.0"><channel>
<title>PubMed</title>
<item>
<title>Sleep after 35</title>
<link>https://pubmed.ncbi.nlm.nih.gov/123/</link>
<description>Abstract text.</description>
</item>
</channel></rss>
"""


_SEARCH = """
{"esearchresult":{"idlist":["123"]}}
"""
_SUMMARY = """
{"result":{"uids":["123"],"123":{"title":"Sleep after 35","pubdate":"2026 Jan"}}}
"""


async def _fake_get(url: str) -> str:
    if "esummary" in url:
        return _SUMMARY
    if "esearch" in url:
        return _SEARCH
    return _RSS


@pytest.mark.asyncio
async def test_pubmed_rss_becomes_scored_digest_cards() -> None:
    loader = CatalogRssLoader(catalog=SourceCatalog.initial(), getter=_fake_get)
    candidates, failures = await loader.load(owner_id=42, digest_date=datetime.now(UTC).date())
    digest = DigestService().build(
        candidates, digest_date=datetime.now(UTC).date(), source_failures=failures
    )

    assert failures == ()
    assert digest.cards
    assert all("pubmed.ncbi.nlm.nih.gov" in card.provenance_urls[0] for card in digest.cards)


@pytest.mark.asyncio
async def test_rss_payload_still_parses_when_eutils_is_not_used() -> None:
    async def rss_only(url: str) -> str:
        del url
        return _RSS

    loader = CatalogRssLoader(catalog=SourceCatalog.initial(), getter=rss_only)
    candidates, failures = await loader.load(owner_id=42, digest_date=datetime.now(UTC).date())

    assert failures == ()
    assert candidates
    assert candidates[0].title == "Sleep after 35"
