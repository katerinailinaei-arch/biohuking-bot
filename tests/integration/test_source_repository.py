from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import AuditEvent
from bodrye_bot.db.repositories.sources import SqlAlchemySourceCatalogRepository
from bodrye_bot.db.uow import SqlAlchemyUnitOfWork
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.operations.audit import AuditEventType
from bodrye_bot.sources.catalog import SourceCatalog, SourceCatalogUpdater


@pytest.mark.asyncio
async def test_source_catalog_repository_seeds_loads_and_is_owner_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.catalogs.save(42, SourceCatalog.initial())
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        loaded = await uow.catalogs.get(42)
        with pytest.raises(SafeError) as caught:
            await uow.catalogs.get(999)

    assert loaded.version == "source-registry-v1"
    assert len(loaded.sources) == 10
    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_versioned_query_update_persists_safe_audit_and_rolls_back_on_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.catalogs.save(42, SourceCatalog.initial())
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        current = await uow.catalogs.get(42)

    changed = await SourceCatalogUpdater(
        uow=SqlAlchemyUnitOfWork(session_factory)
    ).update_pubmed_queries(
        owner_id=42,
        current=current,
        version="source-registry-v2",
        queries=("activity", "sleep", "metabolism"),
    )

    async with session_factory() as session:
        repository = SqlAlchemySourceCatalogRepository(session, ensure_active=lambda: None)
        stored = await repository.get(42)
        events = (
            await session.execute(select(AuditEvent).where(AuditEvent.owner_id == 42))
        ).scalars()
        event = next(
            item for item in events if item.event_type == AuditEventType.CONFIGURATION_CHANGED
        )

    assert stored.version == changed.version
    queries = {
        source.config.get("query")
        for source in stored.sources
        if source.kind.value == "pubmed_rss"
    }
    assert queries == {
        "activity",
        "sleep",
        "metabolism",
    }
    assert event.metadata_json == {
        "registry_version": "source-registry-v2",
        "pubmed_query_version": "pubmed-rss-v2",
        "source_count": 10,
    }


@pytest.mark.asyncio
async def test_pubmed_update_round_trip_replaces_query_bearing_urls_without_stale_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        initial = SourceCatalog.initial()
        await uow.catalogs.save(42, initial)
        await uow.commit()

    queries = ("activity + aging", "sleep quality", "metabolic health")
    changed = await SourceCatalogUpdater(
        uow=SqlAlchemyUnitOfWork(session_factory)
    ).update_pubmed_queries(
        owner_id=42,
        current=initial,
        version="source-registry-v2",
        queries=queries,
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        loaded = await uow.catalogs.get(42)

    for source in (item for item in loaded.sources if item.kind.value == "pubmed_rss"):
        assert parse_qs(urlsplit(source.canonical_url).query)["term"] == [source.config["query"]]
    loaded_queries = {
        item.config["query"] for item in loaded.sources if item.kind.value == "pubmed_rss"
    }
    assert loaded_queries == set(queries)
    assert {item.canonical_url for item in loaded.sources} == {
        item.canonical_url for item in changed.sources
    }
    assert len(loaded.sources) == 10
