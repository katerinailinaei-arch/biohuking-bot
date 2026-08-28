from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.workflow import WorkflowStatus

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    assert TEST_DATABASE_URL is not None
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture(scope="module")
def migrated_database() -> Iterator[None]:
    command.upgrade(_alembic_config(), "head")
    yield


@pytest_asyncio.fixture
async def pg_connection(migrated_database: None) -> AsyncIterator[AsyncConnection]:
    del migrated_database
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()


async def _insert_workflow(
    connection: AsyncConnection,
    *,
    owner_id: int,
    status: WorkflowStatus = WorkflowStatus.DRAFT,
) -> UUID:
    workflow_id = uuid4()
    await connection.execute(
        text(
            """
            INSERT INTO content_workflows
                (id, owner_id, origin_type, status, recommended_format, version)
            VALUES
                (:id, :owner_id, 'manual_text', :status, 'medium', 1)
            """
        ),
        {"id": workflow_id, "owner_id": owner_id, "status": status.value},
    )
    return workflow_id


async def _insert_draft(
    connection: AsyncConnection,
    *,
    owner_id: int,
    workflow_id: UUID,
    version_number: int = 1,
    body: str = "Проверенный текст",
) -> tuple[UUID, str]:
    draft_id = uuid4()
    body_hash = content_hash(body)
    await connection.execute(
        text(
            """
            INSERT INTO draft_versions
                (id, owner_id, workflow_id, version_number, body, body_hash,
                 format, headlines, public_sources, style_profile_version)
            VALUES
                (:id, :owner_id, :workflow_id, :version_number, :body, :body_hash,
                 'medium', ARRAY['Заголовок'], ARRAY[]::text[], 1)
            """
        ),
        {
            "id": draft_id,
            "owner_id": owner_id,
            "workflow_id": workflow_id,
            "version_number": version_number,
            "body": body,
            "body_hash": body_hash,
        },
    )
    return draft_id, body_hash


async def _insert_review_and_approval(
    connection: AsyncConnection,
    *,
    owner_id: int,
    workflow_id: UUID,
    draft_id: UUID,
    body_hash: str,
    revoked_at: datetime | None = None,
) -> UUID:
    await connection.execute(
        text(
            """
            INSERT INTO review_decisions
                (id, owner_id, draft_version_id, status, blocking_reasons,
                 changed_claim_ids, reviewed_at, policy_version)
            VALUES
                (:id, :owner_id, :draft_id, 'passed', ARRAY[]::text[],
                 ARRAY[]::uuid[], now(), 'medical-v1')
            """
        ),
        {"id": uuid4(), "owner_id": owner_id, "draft_id": draft_id},
    )
    await connection.execute(
        text(
            """
            UPDATE content_workflows
            SET current_version_id = :draft_id,
                status = 'draft_review_passed',
                updated_at = now()
            WHERE id = :workflow_id AND owner_id = :owner_id
            """
        ),
        {"draft_id": draft_id, "workflow_id": workflow_id, "owner_id": owner_id},
    )
    approval_id = uuid4()
    await connection.execute(
        text(
            """
            INSERT INTO approvals
                (id, owner_id, workflow_id, draft_version_id, content_hash,
                 approved_by, approved_at, revoked_at)
            VALUES
                (:id, :owner_id, :workflow_id, :draft_id, :content_hash,
                 'owner', now(), :revoked_at)
            """
        ),
        {
            "id": approval_id,
            "owner_id": owner_id,
            "workflow_id": workflow_id,
            "draft_id": draft_id,
            "content_hash": body_hash,
            "revoked_at": revoked_at,
        },
    )
    return approval_id


async def _insert_publication_job(
    connection: AsyncConnection,
    *,
    owner_id: int,
    draft_id: UUID,
    approval_id: UUID,
    status: str = "scheduled",
) -> UUID:
    job_id = uuid4()
    await connection.execute(
        text(
            """
            INSERT INTO publication_jobs
                (id, owner_id, draft_version_id, approval_id, scheduled_at_utc,
                 status, idempotency_key)
            VALUES
                (:id, :owner_id, :draft_id, :approval_id, now(), :status,
                 :idempotency_key)
            """
        ),
        {
            "id": job_id,
            "owner_id": owner_id,
            "draft_id": draft_id,
            "approval_id": approval_id,
            "status": status,
            "idempotency_key": f"job-{job_id}",
        },
    )
    return job_id


async def _assert_statement_is_rejected(
    connection: AsyncConnection,
    statement: str,
    parameters: dict[str, object],
    message: str,
) -> None:
    nested_transaction = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError, match=message):
            await connection.execute(text(statement), parameters)
    finally:
        await nested_transaction.rollback()


@pytest.mark.asyncio
async def test_owner_qualified_fk_rejects_cross_owner_claim(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            text(
                """
                INSERT INTO claims
                    (id, owner_id, workflow_id, exact_text, claim_type, is_medical, status)
                VALUES
                    (:id, 202, :workflow_id, 'Тезис', 'effect', true, 'pending')
                """
            ),
            {"id": uuid4(), "workflow_id": workflow_id},
        )


@pytest.mark.asyncio
async def test_draft_version_number_is_unique_per_workflow(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    await _insert_draft(pg_connection, owner_id=101, workflow_id=workflow_id)

    with pytest.raises(IntegrityError):
        await _insert_draft(
            pg_connection,
            owner_id=101,
            workflow_id=workflow_id,
            body="Другой текст",
        )


@pytest.mark.asyncio
async def test_draft_versions_are_immutable_in_postgresql(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, _ = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )

    with pytest.raises(DBAPIError, match="draft_versions are immutable"):
        await pg_connection.execute(
            text("UPDATE draft_versions SET body = 'Подменённый текст' WHERE id = :id"),
            {"id": draft_id},
        )


@pytest.mark.asyncio
async def test_approval_requires_current_reviewed_hash(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            text(
                """
                INSERT INTO approvals
                    (id, owner_id, workflow_id, draft_version_id, content_hash,
                     approved_by, approved_at)
                VALUES
                    (:id, 101, :workflow_id, :draft_id, :body_hash, 'owner', now())
                """
            ),
            {
                "id": uuid4(),
                "workflow_id": workflow_id,
                "draft_id": draft_id,
                "body_hash": body_hash,
            },
        )


@pytest.mark.asyncio
async def test_approval_rejects_a_hash_that_does_not_match_the_reviewed_draft(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
        revoked_at=datetime.now(UTC),
    )

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            text(
                """
                INSERT INTO approvals
                    (id, owner_id, workflow_id, draft_version_id, content_hash,
                     approved_by, approved_at)
                VALUES
                    (:id, 101, :workflow_id, :draft_id, :body_hash, 'owner', now())
                """
            ),
            {
                "id": uuid4(),
                "workflow_id": workflow_id,
                "draft_id": draft_id,
                "body_hash": content_hash("Подменённый hash"),
            },
        )


@pytest.mark.asyncio
async def test_workflow_approved_status_requires_the_current_active_approval(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)

    nested_transaction = await pg_connection.begin_nested()
    try:
        with pytest.raises(DBAPIError, match="approved workflow requires current approval"):
            await pg_connection.execute(
                text(
                    "UPDATE content_workflows SET status = 'approved' WHERE id = :workflow_id"
                ),
                {"workflow_id": workflow_id},
            )
    finally:
        await nested_transaction.rollback()

    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    await pg_connection.execute(
        text("UPDATE content_workflows SET status = 'approved' WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    )


@pytest.mark.asyncio
async def test_approved_workflow_rejects_revoking_its_active_approval(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    await pg_connection.execute(
        text("UPDATE content_workflows SET status = 'approved' WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    )

    await _assert_statement_is_rejected(
        pg_connection,
        "UPDATE approvals SET revoked_at = now() WHERE id = :approval_id",
        {"approval_id": approval_id},
        "cannot deactivate approval for approved workflow",
    )


@pytest.mark.asyncio
async def test_approved_workflow_rejects_deleting_its_active_approval(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    await pg_connection.execute(
        text("UPDATE content_workflows SET status = 'approved' WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    )

    await _assert_statement_is_rejected(
        pg_connection,
        "DELETE FROM approvals WHERE id = :approval_id",
        {"approval_id": approval_id},
        "cannot deactivate approval for approved workflow",
    )


@pytest.mark.asyncio
async def test_review_decision_reviewed_at_is_immutable_after_insert(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )

    result = await pg_connection.execute(
        text(
            "SELECT id FROM review_decisions WHERE draft_version_id = :draft_id "
            "AND owner_id = 101"
        ),
        {"draft_id": draft_id},
    )
    review_id = result.scalar_one()
    assert approval_id is not None

    await _assert_statement_is_rejected(
        pg_connection,
        "UPDATE review_decisions SET reviewed_at = now() + interval '1 day' "
        "WHERE id = :review_id",
        {"review_id": review_id},
        "review_decisions are immutable",
    )


@pytest.mark.asyncio
async def test_scheduled_job_rejects_a_revoked_approval_on_insert(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
        revoked_at=datetime.now(UTC),
    )

    await _assert_statement_is_rejected(
        pg_connection,
        """
        INSERT INTO publication_jobs
            (id, owner_id, draft_version_id, approval_id, scheduled_at_utc,
             status, idempotency_key)
        VALUES
            (:id, 101, :draft_id, :approval_id, now(), 'scheduled', :idempotency_key)
        """,
        {
            "id": uuid4(),
            "draft_id": draft_id,
            "approval_id": approval_id,
            "idempotency_key": "revoked-approval-insert",
        },
        "publication job requires current active approval",
    )


@pytest.mark.asyncio
async def test_cancelled_job_cannot_be_rescheduled_after_approval_revocation(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    job_id = await _insert_publication_job(
        pg_connection,
        owner_id=101,
        draft_id=draft_id,
        approval_id=approval_id,
        status="cancelled",
    )
    await pg_connection.execute(
        text("UPDATE approvals SET revoked_at = now() WHERE id = :approval_id"),
        {"approval_id": approval_id},
    )

    await _assert_statement_is_rejected(
        pg_connection,
        "UPDATE publication_jobs SET status = 'scheduled' WHERE id = :job_id",
        {"job_id": job_id},
        "publication job requires current active approval",
    )


@pytest.mark.asyncio
async def test_revoking_approval_requires_scheduled_job_to_be_cancelled_first(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    job_id = await _insert_publication_job(
        pg_connection,
        owner_id=101,
        draft_id=draft_id,
        approval_id=approval_id,
    )

    await _assert_statement_is_rejected(
        pg_connection,
        "UPDATE approvals SET revoked_at = now() WHERE id = :approval_id",
        {"approval_id": approval_id},
        "cannot deactivate approval backing active publication job",
    )
    await pg_connection.execute(
        text("UPDATE publication_jobs SET status = 'cancelled' WHERE id = :job_id"),
        {"job_id": job_id},
    )
    await pg_connection.execute(
        text("UPDATE approvals SET revoked_at = now() WHERE id = :approval_id"),
        {"approval_id": approval_id},
    )


@pytest.mark.asyncio
async def test_deleting_approval_requires_scheduled_job_to_be_cancelled_first(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    job_id = await _insert_publication_job(
        pg_connection,
        owner_id=101,
        draft_id=draft_id,
        approval_id=approval_id,
    )

    await _assert_statement_is_rejected(
        pg_connection,
        "DELETE FROM approvals WHERE id = :approval_id",
        {"approval_id": approval_id},
        "cannot deactivate approval backing active publication job",
    )
    await pg_connection.execute(
        text("UPDATE publication_jobs SET status = 'cancelled' WHERE id = :job_id"),
        {"job_id": job_id},
    )
    await pg_connection.execute(
        text("DELETE FROM approvals WHERE id = :approval_id"),
        {"approval_id": approval_id},
    )


@pytest.mark.asyncio
async def test_non_passing_review_cannot_supersede_an_active_approval(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )

    with pytest.raises(DBAPIError, match="non-passing review cannot supersede active approval"):
        await pg_connection.execute(
            text(
                """
                INSERT INTO review_decisions
                    (id, owner_id, draft_version_id, status, blocking_reasons,
                     changed_claim_ids, reviewed_at, policy_version)
                VALUES
                    (:id, 101, :draft_id, 'blocked', ARRAY['red']::text[],
                     ARRAY[]::uuid[], now() + interval '1 second', 'medical-v1')
                """
            ),
            {"id": uuid4(), "draft_id": draft_id},
        )


@pytest.mark.asyncio
async def test_only_one_active_approval_exists_per_workflow(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            text(
                """
                INSERT INTO approvals
                    (id, owner_id, workflow_id, draft_version_id, content_hash,
                     approved_by, approved_at)
                VALUES
                    (:id, 101, :workflow_id, :draft_id, :body_hash, 'owner', now())
                """
            ),
            {
                "id": uuid4(),
                "workflow_id": workflow_id,
                "draft_id": draft_id,
                "body_hash": body_hash,
            },
        )


@pytest.mark.asyncio
async def test_publication_idempotency_key_is_unique(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    parameters = {
        "owner_id": 101,
        "draft_id": draft_id,
        "approval_id": approval_id,
        "scheduled_at": datetime.now(UTC) + timedelta(hours=1),
        "idempotency_key": "publish-once",
    }
    statement = text(
        """
        INSERT INTO publication_jobs
            (id, owner_id, draft_version_id, approval_id, scheduled_at_utc,
             status, idempotency_key)
        VALUES
            (:id, :owner_id, :draft_id, :approval_id, :scheduled_at,
             'scheduled', :idempotency_key)
        """
    )
    await pg_connection.execute(statement, {**parameters, "id": uuid4()})

    with pytest.raises(IntegrityError):
        await pg_connection.execute(statement, {**parameters, "id": uuid4()})


@pytest.mark.asyncio
async def test_non_null_telegram_message_id_is_globally_unique(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    statement = text(
        """
        INSERT INTO publication_jobs
            (id, owner_id, draft_version_id, approval_id, scheduled_at_utc,
             status, idempotency_key, telegram_message_id)
        VALUES
            (:id, 101, :draft_id, :approval_id, now(), 'published',
             :idempotency_key, 777)
        """
    )
    await pg_connection.execute(
        statement,
        {
            "id": uuid4(),
            "draft_id": draft_id,
            "approval_id": approval_id,
            "idempotency_key": "message-one",
        },
    )

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            statement,
            {
                "id": uuid4(),
                "draft_id": draft_id,
                "approval_id": approval_id,
                "idempotency_key": "message-two",
            },
        )


async def _insert_source_document(connection: AsyncConnection) -> UUID:
    source_id = uuid4()
    document_id = uuid4()
    await connection.execute(
        text(
            """
            INSERT INTO sources
                (id, owner_id, name, canonical_url, source_type, roles,
                 access_method, status, failure_count, config_json)
            VALUES
                (:source_id, 101, 'WHO', 'https://www.who.int/', 'evidence',
                 ARRAY['evidence'], 'http', 'active', 0, '{}'::jsonb)
            """
        ),
        {"source_id": source_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO source_documents
                (id, owner_id, source_id, url, fetched_at, content_hash,
                 bounded_excerpt, raw_expires_at, fetch_status, http_metadata)
            VALUES
                (:document_id, 101, :source_id, 'https://www.who.int/item', now(),
                 :content_hash, 'Короткий фрагмент', now() + interval '24 hours',
                 'fetched', '{}'::jsonb)
            """
        ),
        {
            "document_id": document_id,
            "source_id": source_id,
            "content_hash": content_hash("Короткий фрагмент"),
        },
    )
    return document_id


@pytest.mark.asyncio
async def test_source_payload_cache_expiry_is_at_most_24_hours(
    pg_connection: AsyncConnection,
) -> None:
    document_id = await _insert_source_document(pg_connection)

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            text(
                """
                INSERT INTO source_payload_cache
                    (id, owner_id, source_document_id, payload, fetched_at, expires_at)
                VALUES
                    (:id, 101, :document_id, :payload, now(),
                     now() + interval '24 hours 1 second')
                """
            ),
            {"id": uuid4(), "document_id": document_id, "payload": b"bounded"},
        )


@pytest.mark.asyncio
async def test_source_payload_cache_rejects_more_than_10_mib(
    pg_connection: AsyncConnection,
) -> None:
    document_id = await _insert_source_document(pg_connection)

    with pytest.raises(IntegrityError):
        await pg_connection.execute(
            text(
                """
                INSERT INTO source_payload_cache
                    (id, owner_id, source_document_id, payload, fetched_at, expires_at)
                VALUES
                    (:id, 101, :document_id, :payload, now(),
                     now() + interval '1 hour')
                """
            ),
            {
                "id": uuid4(),
                "document_id": document_id,
                "payload": b"x" * (10 * 1024 * 1024 + 1),
            },
        )


@pytest.mark.asyncio
async def test_deleting_a_workflow_cascades_its_live_draft_approval_and_job(
    pg_connection: AsyncConnection,
) -> None:
    workflow_id = await _insert_workflow(pg_connection, owner_id=101)
    draft_id, body_hash = await _insert_draft(
        pg_connection, owner_id=101, workflow_id=workflow_id
    )
    approval_id = await _insert_review_and_approval(
        pg_connection,
        owner_id=101,
        workflow_id=workflow_id,
        draft_id=draft_id,
        body_hash=body_hash,
    )
    job_id = uuid4()
    await pg_connection.execute(
        text(
            """
            INSERT INTO publication_jobs
                (id, owner_id, draft_version_id, approval_id, scheduled_at_utc,
                 status, idempotency_key)
            VALUES
                (:id, 101, :draft_id, :approval_id, now(), 'scheduled', :idempotency_key)
            """
        ),
        {
            "id": job_id,
            "draft_id": draft_id,
            "approval_id": approval_id,
            "idempotency_key": "workflow-deletion-cascade",
        },
    )

    await pg_connection.execute(
        text("DELETE FROM content_workflows WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    )

    for table, object_id in (
        ("content_workflows", workflow_id),
        ("draft_versions", draft_id),
        ("approvals", approval_id),
        ("publication_jobs", job_id),
    ):
        result = await pg_connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE id = :object_id"),
            {"object_id": object_id},
        )
        assert result.scalar_one() == 0, table


def test_named_constraints_owner_lineage_and_delete_policy(
    migrated_database: None,
) -> None:
    del migrated_database

    async def inspect_schema() -> tuple[set[str], dict[str, list[dict[str, object]]]]:
        assert TEST_DATABASE_URL is not None
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_connection: (
                        {
                            row[0]
                            for row in sync_connection.execute(
                                text(
                                    """
                                    SELECT conname FROM pg_constraint
                                    UNION ALL
                                    SELECT indexname FROM pg_indexes
                                    WHERE schemaname = current_schema()
                                    UNION ALL
                                    SELECT tgname FROM pg_trigger
                                    WHERE NOT tgisinternal
                                    """
                                )
                            )
                        },
                        {
                            table: inspect(sync_connection).get_foreign_keys(table)
                            for table in (
                                "approvals",
                                "angles",
                                "audit_events",
                                "claims",
                                "content_workflows",
                                "cost_events",
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
                                "style_examples",
                                "style_profiles",
                                "style_rules",
                            )
                        },
                    )
                )
        finally:
            await engine.dispose()

    import asyncio

    names, foreign_keys = asyncio.run(inspect_schema())
    assert {
        "ck_source_payload_expiry_24h",
        "ck_source_payload_max_10mib",
        "ck_workflow_version_positive",
        "fk_claim_workflow_owner",
        "trg_draft_versions_immutable",
        "uq_active_approval",
        "uq_draft_workflow_version",
        "uq_publication_idempotency",
        "uq_publication_message",
    } <= names

    for table, table_foreign_keys in foreign_keys.items():
        for foreign_key in table_foreign_keys:
            constrained = set(foreign_key["constrained_columns"])
            referred = set(foreign_key["referred_columns"])
            assert "owner_id" in constrained, (table, foreign_key["name"])
            assert "owner_id" in referred, (table, foreign_key["name"])

    cache_fk = next(
        fk
        for fk in foreign_keys["source_payload_cache"]
        if "source_document_id" in fk["constrained_columns"]
    )
    assert cache_fk["options"].get("ondelete") == "CASCADE"

    evidence_source_fk = next(
        fk
        for fk in foreign_keys["evidence"]
        if "source_document_id" in fk["constrained_columns"]
    )
    assert evidence_source_fk["options"].get("ondelete") == "RESTRICT"
