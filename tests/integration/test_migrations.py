from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
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
    "style_edit_observations",
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


async def _cleanup_style_fixture(profile_id: str) -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM style_examples WHERE profile_id = :profile_id"),
                {"profile_id": profile_id},
            )
            await connection.execute(
                text("DELETE FROM style_rules WHERE profile_id = :profile_id"),
                {"profile_id": profile_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM style_profiles "
                    "WHERE id = :profile_id AND owner_id = 42"
                ),
                {"profile_id": profile_id},
            )
    finally:
        await engine.dispose()


def _run_style_migration_scenario(
    *,
    profile_id: str,
    seed: Callable[[], Awaitable[None]],
    expected_error: str,
) -> None:
    """Run one 0005-to-0006 preflight in isolation and always restore head."""
    config = _alembic_config()
    command.downgrade(config, "0005_style_repository_fields")
    try:
        asyncio.run(seed())
        with pytest.raises(Exception, match=expected_error):
            command.upgrade(config, "head")
    finally:
        try:
            asyncio.run(_cleanup_style_fixture(profile_id))
        finally:
            command.upgrade(config, "head")


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


def test_alembic_preserves_existing_application_logger() -> None:
    importlib.import_module("bodrye_bot.telegram.router")
    application_logger = logging.getLogger("bodrye_bot.telegram.router")
    application_logger.disabled = False

    command.upgrade(_alembic_config(), "head")

    assert application_logger.disabled is False


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


def test_style_metadata_matches_idempotency_constraints() -> None:
    importlib.import_module("bodrye_bot.db.models")
    base_module = importlib.import_module("bodrye_bot.db.base")
    style_rules = base_module.Base.metadata.tables["style_rules"]
    style_edits = base_module.Base.metadata.tables["style_edit_observations"]

    proposed_index = next(
        index for index in style_rules.indexes if index.name == "uq_style_rule_proposed_pattern"
    )
    assert [column.name for column in proposed_index.columns] == [
        "owner_id",
        "profile_id",
        "pattern_key",
    ]
    assert proposed_index.unique is True
    assert str(proposed_index.dialect_options["postgresql"]["where"]) == (
        "status = 'proposed' AND pattern_key <> ''"
    )
    assert any(
        constraint.name == "uq_style_edit_observation_source"
        for constraint in style_edits.constraints
    )


def test_0008_preflight_rejects_legacy_active_profile_without_calibration_binding() -> None:
    """0008 must fail rather than silently bless an unbound active profile."""
    assert TEST_DATABASE_URL is not None
    profile_id = str(uuid4())
    config = _alembic_config()
    command.downgrade(config, "0007_style_calibration_report")
    try:
        async def seed() -> None:
            engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO style_profiles "
                            "(id, owner_id, created_at, updated_at, version, status, activated_at) "
                            "VALUES (:profile_id, 42, now(), now(), 1, 'active', now())"
                        ),
                        {"profile_id": profile_id},
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed())
        expected_error = (
            "style migration preflight failed: active style profiles require "
            "calibration report binding"
        )
        with pytest.raises(Exception, match=expected_error):
            command.upgrade(config, "head")
    finally:
        try:
            asyncio.run(_cleanup_style_fixture(profile_id))
        finally:
            command.upgrade(config, "head")


def test_0008_preflight_rejects_unpaired_legacy_calibration_fields() -> None:
    """0008 must not silently discard half of a report binding."""
    assert TEST_DATABASE_URL is not None
    profile_id = str(uuid4())
    report_id = str(uuid4())
    config = _alembic_config()
    command.downgrade(config, "0007_style_calibration_report")
    try:
        async def seed() -> None:
            engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO style_profiles "
                            "(id, owner_id, created_at, updated_at, version, status, "
                            "calibration_report_id) "
                            "VALUES (:profile_id, 42, now(), now(), 1, 'calibrating', "
                            ":report_id)"
                        ),
                        {"profile_id": profile_id, "report_id": report_id},
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed())
        with pytest.raises(
            Exception,
            match="style migration preflight failed: calibration report id/hash must be paired",
        ):
            command.upgrade(config, "head")
    finally:
        try:
            asyncio.run(_cleanup_style_fixture(profile_id))
        finally:
            command.upgrade(config, "head")


def test_upgrade_from_0005_accepts_legacy_empty_pattern_keys() -> None:
    assert TEST_DATABASE_URL is not None
    profile_id = str(uuid4())
    rule_one = str(uuid4())
    rule_two = str(uuid4())

    async def seed() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO style_profiles "
                        "(id, owner_id, created_at, updated_at, version, status) "
                        "VALUES (:profile_id, 42, now(), now(), 101, 'calibrating')"
                    ),
                    {"profile_id": profile_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO style_rules "
                        "(id, owner_id, created_at, updated_at, profile_id, scope, "
                        "rule_text, origin, status, risks, tags, pattern_key) "
                        "VALUES "
                        "(:rule_one, 42, now(), now(), :profile_id, 'global', "
                        "'Первое.', 'edit', 'proposed', '{}', '{}', ''), "
                        "(:rule_two, 42, now(), now(), :profile_id, 'global', "
                        "'Второе.', 'edit', 'proposed', '{}', '{}', '')"
                    ),
                    {"rule_one": rule_one, "rule_two": rule_two, "profile_id": profile_id},
                )
        finally:
            await engine.dispose()

    config = _alembic_config()
    command.downgrade(config, "0005_style_repository_fields")
    try:
        asyncio.run(seed())
        command.upgrade(config, "head")
        async def count_empty_keys() -> int:
            engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
            try:
                async with engine.connect() as connection:
                    result = await connection.execute(
                        text(
                            "SELECT count(*) FROM style_rules "
                            "WHERE profile_id = :profile_id AND pattern_key = ''"
                        ),
                        {"profile_id": profile_id},
                    )
                    return int(result.scalar_one())
            finally:
                await engine.dispose()

        assert asyncio.run(count_empty_keys()) == 2
    finally:
        try:
            asyncio.run(_cleanup_style_fixture(profile_id))
        finally:
            command.upgrade(config, "head")

    tables, _ = _snapshot()
    assert "style_edit_observations" in tables


@pytest.mark.parametrize(
    ("table", "column", "limit", "message"),
    [
        (
            "style_rules",
            "risks",
            16,
            "style migration preflight failed: style_rules.risks exceeds 16 items",
        ),
        (
            "style_rules",
            "tags",
            32,
            "style migration preflight failed: style_rules.tags exceeds 32 items",
        ),
        (
            "style_examples",
            "risks",
            16,
            "style migration preflight failed: style_examples.risks exceeds 16 items",
        ),
    ],
)
def test_0006_preflight_rejects_overbounded_style_arrays(
    table: str, column: str, limit: int, message: str
) -> None:
    assert TEST_DATABASE_URL is not None
    profile_id = str(uuid4())
    record_id = str(uuid4())
    values = ", ".join(f"'value-{index}'" for index in range(limit + 1))
    array_sql = f"ARRAY[{values}]::varchar[]"
    risks_sql = array_sql if column == "risks" else "'{}'"
    tags_sql = array_sql if column == "tags" else "'{}'"

    async def seed() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO style_profiles "
                        "(id, owner_id, created_at, updated_at, version, status) "
                        "VALUES (:profile_id, 42, now(), now(), 1, 'calibrating')"
                    ),
                    {"profile_id": profile_id},
                )
                if table == "style_rules":
                    await connection.execute(
                        text(
                            "INSERT INTO style_rules "
                            "(id, owner_id, created_at, updated_at, profile_id, scope, "
                            "rule_text, origin, status, risks, tags, pattern_key) "
                            "VALUES (:record_id, 42, now(), now(), :profile_id, "
                            "'global', 'Правило.', 'edit', 'proposed', "
                            f"{risks_sql}, {tags_sql}, '')"
                        ),
                        {"record_id": record_id, "profile_id": profile_id},
                    )
                else:
                    await connection.execute(
                        text(
                            "INSERT INTO style_examples "
                            "(id, owner_id, created_at, profile_id, text, rubric, "
                            "format, tags, rating, is_holdout, risks) "
                            f"VALUES (:record_id, 42, now(), :profile_id, 'Пример.', "
                            f"'energy', 'post', '{{}}', 5, false, {array_sql})"
                        ),
                        {"record_id": record_id, "profile_id": profile_id},
                    )
        finally:
            await engine.dispose()

    _run_style_migration_scenario(
        profile_id=profile_id, seed=seed, expected_error=message
    )


def test_0006_preflight_rejects_duplicate_nonempty_proposed_pattern_keys() -> None:
    assert TEST_DATABASE_URL is not None
    profile_id = str(uuid4())

    async def seed() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO style_profiles "
                        "(id, owner_id, created_at, updated_at, version, status) "
                        "VALUES (:profile_id, 42, now(), now(), 1, 'calibrating')"
                    ),
                    {"profile_id": profile_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO style_rules "
                        "(id, owner_id, created_at, updated_at, profile_id, scope, "
                        "rule_text, origin, status, risks, tags, pattern_key) "
                        "VALUES "
                        "(:rule_one, 42, now(), now(), :profile_id, 'global', "
                        "'Первое.', 'edit', 'proposed', '{}', '{}', 'opening:action'), "
                        "(:rule_two, 42, now(), now(), :profile_id, 'global', "
                        "'Второе.', 'edit', 'proposed', '{}', '{}', 'opening:action')"
                    ),
                    {
                        "profile_id": profile_id,
                        "rule_one": str(uuid4()),
                        "rule_two": str(uuid4()),
                    },
                )
        finally:
            await engine.dispose()

    _run_style_migration_scenario(
        profile_id=profile_id,
        seed=seed,
        expected_error="style migration preflight failed: duplicate proposed nonempty pattern_key",
    )


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
