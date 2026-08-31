from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import AuditEvent, Source, SourceDocument
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


@pytest.mark.asyncio
async def test_pubmed_update_preserves_document_provenance_and_loads_only_current_catalog(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4().int % 2_000_000_000
    initial = SourceCatalog.initial()
    old_pubmed_url = next(
        source.canonical_url
        for source in initial.sources
        if source.config.get("query") == "physical activity AND healthy aging"
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.catalogs.save(owner_id, initial)
        await uow.commit()

    fetched_at = datetime(2026, 8, 29, tzinfo=UTC)
    async with session_factory() as session:
        old_source = await session.scalar(
            select(Source).where(
                Source.owner_id == owner_id,
                Source.canonical_url == old_pubmed_url,
            )
        )
        assert old_source is not None
        unchanged_ids = {
            source.canonical_url: source.id
            for source in (
                await session.execute(
                    select(Source).where(
                        Source.owner_id == owner_id,
                        Source.source_type != "pubmed_rss",
                    )
                )
            ).scalars()
        }
        document = SourceDocument(
            owner_id=owner_id,
            source_id=old_source.id,
            url=old_pubmed_url,
            fetched_at=fetched_at,
            content_hash="a" * 64,
            bounded_excerpt="Historical PubMed provenance",
            raw_expires_at=fetched_at,
            fetch_status="available",
            http_metadata={"status": 200},
        )
        session.add(document)
        await session.commit()
        old_source_id = old_source.id
        document_id = document.id

    queries = ("activity", "sleep", "metabolism")
    await SourceCatalogUpdater(
        uow=SqlAlchemyUnitOfWork(session_factory)
    ).update_pubmed_queries(
        owner_id=owner_id,
        current=initial,
        version="source-registry-v2",
        queries=queries,
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        loaded = await uow.catalogs.get(owner_id)

    async with session_factory() as session:
        preserved_source = await session.get(Source, old_source_id)
        preserved_document = await session.get(SourceDocument, document_id)
        owner_sources = list(
            (
                await session.execute(select(Source).where(Source.owner_id == owner_id))
            ).scalars()
        )
        audit_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.owner_id == owner_id,
                AuditEvent.event_type == AuditEventType.CONFIGURATION_CHANGED,
            )
        )

    assert preserved_source is not None
    assert preserved_source.owner_id == owner_id
    assert preserved_source.status == "retired"
    assert preserved_source.config_json["catalog_current"] is False
    assert (
        preserved_source.config_json["superseded_by_registry_version"]
        == "source-registry-v2"
    )
    assert preserved_document is not None
    assert preserved_document.owner_id == owner_id
    assert preserved_document.source_id == old_source_id
    assert all(source.owner_id == owner_id for source in owner_sources)
    assert len(owner_sources) == 13
    assert sum(
        source.config_json.get("catalog_current") is True for source in owner_sources
    ) == 10
    assert sum(source.status == "retired" for source in owner_sources) == 3
    assert {
        source.canonical_url: source.id
        for source in owner_sources
        if source.source_type != "pubmed_rss"
    } == unchanged_ids
    assert loaded.version == "source-registry-v2"
    assert len(loaded.sources) == 10
    assert {
        source.config["query"]
        for source in loaded.sources
        if source.kind.value == "pubmed_rss"
    } == set(queries)
    assert old_pubmed_url not in {source.canonical_url for source in loaded.sources}
    assert audit_event is not None
    assert audit_event.metadata_json == {
        "registry_version": "source-registry-v2",
        "pubmed_query_version": "pubmed-rss-v2",
        "source_count": 10,
    }
