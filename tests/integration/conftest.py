from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from bodrye_bot.db.models import ContentWorkflow
from bodrye_bot.domain.workflow import WorkflowStatus

if os.getenv("CI") and not os.getenv("TEST_DATABASE_URL"):
    raise pytest.UsageError("TEST_DATABASE_URL is required in CI")


ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def migrated_database() -> None:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def session_factory(
    migrated_database: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    del migrated_database
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def seeded_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[ContentWorkflow]:
    workflow = ContentWorkflow(
        id=uuid4(),
        owner_id=42,
        origin_type="manual_text",
        status=WorkflowStatus.INGESTED,
        recommended_format="medium",
        version=1,
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(workflow)
    yield workflow
    async with session_factory() as session:
        async with session.begin():
            stored = await session.get(ContentWorkflow, workflow.id)
            if stored is not None:
                await session.delete(stored)


@pytest.fixture
def other_workflow_id() -> UUID:
    return uuid4()
