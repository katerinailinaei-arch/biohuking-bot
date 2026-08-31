from __future__ import annotations

from dataclasses import replace

import pytest

from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.sources.catalog import (
    AccessMethod,
    SourceCatalog,
    SourceCatalogUpdater,
    SourceKind,
)


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


def test_catalog_config_is_deeply_immutable():
    """Break caught: an onboarding caller mutates a published query without audit."""
    pubmed = next(
        source for source in SourceCatalog.initial().sources if source.kind is SourceKind.PUBMED_RSS
    )

    with pytest.raises(TypeError):
        pubmed.config["query"] = "different"  # type: ignore[index]


@pytest.mark.asyncio
async def test_owner_update_persists_new_catalog_version_and_redacted_audit_atomically():
    """Break caught: an editable PubMed configuration is saved without a versioned audit record."""
    from bodrye_bot.operations.audit import AuditEntry

    class FakeRepository:
        saved: list[tuple[int, SourceCatalog]] = []

        async def save(self, owner_id: int, catalog: SourceCatalog) -> None:
            self.saved.append((owner_id, catalog))

    class FakeAudit:
        recorded: list[AuditEntry] = []

        async def record(self, event: AuditEntry) -> None:
            self.recorded.append(event)

    class FakeUow:
        def __init__(self) -> None:
            self.catalogs = FakeRepository()
            self.audit = FakeAudit()
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            if exc is not None:
                self.catalogs.saved.clear()
                self.audit.recorded.clear()

        async def commit(self) -> None:
            self.committed = True

    uow = FakeUow()
    changed = await SourceCatalogUpdater(uow=uow).update_pubmed_queries(
        owner_id=42,
        current=SourceCatalog.initial(),
        version="source-registry-v2",
        queries=("activity", "sleep", "metabolism"),
    )

    assert changed.version == "source-registry-v2"
    assert uow.committed is True
    assert uow.catalogs.saved == [(42, changed)]
    assert uow.audit.recorded[0].metadata == {
        "registry_version": "source-registry-v2",
        "pubmed_query_version": "pubmed-rss-v2",
        "source_count": 10,
    }


@pytest.mark.asyncio
async def test_owner_update_rolls_back_save_if_audit_fails():
    """Break caught: a changed registry survives when its mandatory audit append fails."""
    class FailingAudit:
        async def record(self, event) -> None:
            raise RuntimeError("audit unavailable")

    class Repository:
        saved = False

        async def save(self, owner_id, catalog) -> None:
            self.saved = True

    class Uow:
        def __init__(self) -> None:
            self.catalogs = Repository()
            self.audit = FailingAudit()
            self.rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            self.rolled_back = exc is not None
            if exc is not None:
                self.catalogs.saved = False

        async def commit(self) -> None:
            raise AssertionError("commit must not run")

    uow = Uow()
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await SourceCatalogUpdater(uow=uow).update_pubmed_queries(
            owner_id=42,
            current=SourceCatalog.initial(),
            version="source-registry-v2",
            queries=("activity", "sleep", "metabolism"),
        )

    assert uow.rolled_back is True
    assert uow.catalogs.saved is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "queries",
    (("one", "two"), ("one", "two", "three", "four"), ("one", "", "three")),
)
async def test_query_update_rejects_nonexact_query_tuple_before_persistence(queries):
    """Break caught: malformed onboarding query counts crash or silently change registry scope."""
    class Uow:
        async def __aenter__(self):
            raise AssertionError("invalid input must not open persistence")

    from bodrye_bot.domain.errors import SafeError

    with pytest.raises(SafeError):
        await SourceCatalogUpdater(uow=Uow()).update_pubmed_queries(  # type: ignore[arg-type]
            owner_id=42,
            current=SourceCatalog.initial(),
            version="source-registry-v2",
            queries=queries,  # type: ignore[arg-type]
        )
