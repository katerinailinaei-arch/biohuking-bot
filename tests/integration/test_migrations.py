from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

REQUIRED_TABLES = {
    "approvals",
    "angles",
    "audit_events",
    "backup_runs",
    "claims",
    "content_workflows",
    "cost_events",
    "deletion_tombstones",
    "digest_items",
    "draft_versions",
    "evidence",
    "library_items",
    "provider_runs",
    "publication_jobs",
    "review_decisions",
    "source_documents",
    "source_health_events",
    "source_payload_cache",
    "sources",
    "style_examples",
    "style_profiles",
    "style_rules",
}

OWNER_OWNED_TABLES = REQUIRED_TABLES
MUTABLE_TABLES = {
    "approvals",
    "backup_runs",
    "claims",
    "content_workflows",
    "deletion_tombstones",
    "digest_items",
    "library_items",
    "provider_runs",
    "publication_jobs",
    "sources",
    "style_profiles",
    "style_rules",
}
FORBIDDEN_OPERATIONAL_COLUMNS = {
    "api_key",
    "authorization",
    "full_prompt",
    "provider_secret",
    "raw_response",
    "raw_source",
}

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _alembic_config() -> Config:
    ini_path = ROOT / "alembic.ini"
    revision_path = (
        ROOT
        / "src"
        / "bodrye_bot"
        / "db"
        / "migrations"
        / "versions"
        / "0001_initial.py"
    )
    assert ini_path.is_file(), "P0.T3 must provide alembic.ini"
    assert revision_path.is_file(), "P0.T3 must provide revision 0001_initial"
    config = Config(str(ini_path))
    assert TEST_DATABASE_URL is not None
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


async def _database_snapshot() -> tuple[set[str], dict[str, set[str]]]:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: (
                    set(inspect(sync_connection).get_table_names()),
                    {
                        table: {
                            column["name"]
                            for column in inspect(sync_connection).get_columns(table)
                        }
                        for table in REQUIRED_TABLES
                        if inspect(sync_connection).has_table(table)
                    },
                )
            )
    finally:
        await engine.dispose()


def _snapshot() -> tuple[set[str], dict[str, set[str]]]:
    return asyncio.run(_database_snapshot())


def test_base_registers_every_required_table() -> None:
    db_spec = importlib.util.find_spec("bodrye_bot.db")
    assert db_spec is not None, "P0.T3 must provide the database package"
    models_spec = importlib.util.find_spec("bodrye_bot.db.models")
    assert models_spec is not None, "P0.T3 must provide the ORM model package"

    base_module = importlib.import_module("bodrye_bot.db.base")
    importlib.import_module("bodrye_bot.db.models")

    assert set(base_module.Base.metadata.tables) == REQUIRED_TABLES


def test_upgrade_downgrade_upgrade_round_trip() -> None:
    config = _alembic_config()

    command.downgrade(config, "base")
    tables_at_base, _ = _snapshot()
    assert REQUIRED_TABLES.isdisjoint(tables_at_base)

    command.upgrade(config, "head")
    tables_after_upgrade, _ = _snapshot()
    assert tables_after_upgrade - {"alembic_version"} == REQUIRED_TABLES

    command.downgrade(config, "base")
    tables_after_downgrade, _ = _snapshot()
    assert REQUIRED_TABLES.isdisjoint(tables_after_downgrade)

    command.upgrade(config, "head")
    tables_after_second_upgrade, _ = _snapshot()
    assert tables_after_second_upgrade - {"alembic_version"} == REQUIRED_TABLES


def test_alembic_uses_database_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    config = Config(str(ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    tables, _ = _snapshot()
    assert tables - {"alembic_version"} == REQUIRED_TABLES


def test_migrated_tables_have_owner_and_bounded_operational_shape() -> None:
    command.upgrade(_alembic_config(), "head")
    tables, columns = _snapshot()

    assert REQUIRED_TABLES <= tables
    for table in OWNER_OWNED_TABLES:
        assert {"id", "owner_id", "created_at"} <= columns[table]
    for table in MUTABLE_TABLES:
        assert "updated_at" in columns[table]

    operational_tables = {
        "audit_events",
        "backup_runs",
        "cost_events",
        "provider_runs",
        "source_health_events",
    }
    for table in operational_tables:
        assert FORBIDDEN_OPERATIONAL_COLUMNS.isdisjoint(columns[table])

    assert "payload" not in columns["source_documents"]
    assert "payload" in columns["source_payload_cache"]


def test_async_session_factory_uses_the_configured_secret_url() -> None:
    base_module = importlib.import_module("bodrye_bot.db.base")
    config_module = importlib.import_module("bodrye_bot.config")
    assert TEST_DATABASE_URL is not None
    settings: Any = config_module.Settings(
        telegram_bot_token="telegram-test-token",
        telegram_owner_id=1,
        telegram_channel_id=-1001,
        groq_api_key="groq-test-key",
        database_url=TEST_DATABASE_URL,
    )

    factory = base_module.async_session_factory(settings)
    engine = factory.kw["bind"]

    assert engine.url.render_as_string(hide_password=True) == TEST_DATABASE_URL
    assert TEST_DATABASE_URL not in repr(settings)
    asyncio.run(engine.dispose())
