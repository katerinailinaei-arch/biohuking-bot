from __future__ import annotations

from dataclasses import replace

import pytest

from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.sources.catalog import AccessMethod, SourceCatalog, SourceKind


def test_catalog_seeds_versioned_approved_sources_with_pubmed_queries():
    """Break caught: a registry edit drops provenance or one approved topic feed."""
    catalog = SourceCatalog.initial()

    assert catalog.version == "source-registry-v1"
    assert len(catalog.sources) == 10
    assert {source.name for source in catalog.sources} >= {
        "Минздрав РФ: клинические рекомендации",
        "WHO Fact Sheets",
        "WHO News",
        "USPSTF",
        "NICE",
        "Cochrane Reviews",
    }
    pubmed = [source for source in catalog.sources if source.kind is SourceKind.PUBMED_RSS]
    assert len(pubmed) == 3
    assert {source.config["query_version"] for source in pubmed} == {"pubmed-rss-v1"}
    assert all(source.checked_at is not None for source in catalog.sources)
    assert all(source.license_note for source in catalog.sources)
    assert all(source.allowed_hosts for source in catalog.sources if source.is_evidence)


def test_catalog_rejects_evidence_role_for_manual_telegram_source():
    """Break caught: a Telegram source can be promoted to medical evidence."""
    telegram = next(
        source
        for source in SourceCatalog.initial().sources
        if source.kind is SourceKind.TELEGRAM_MANUAL
    )

    with pytest.raises(ValueError, match="Telegram"):
        replace(telegram, roles=(SourceRole.EVIDENCE,))

    assert telegram.access_method is AccessMethod.OWNER_FORWARDED_OR_EXPLICIT_LINK
    assert SourceRole.EVIDENCE not in telegram.roles
