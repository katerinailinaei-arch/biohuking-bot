from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.repositories.digest_runs import SqlAlchemyDigestRunStore
from bodrye_bot.digest.worker import DigestRunStatus


@pytest.mark.asyncio
async def test_claim_is_committed_before_crash_and_expired_lease_becomes_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlAlchemyDigestRunStore(session_factory)
    owner_id = uuid4().int % 2_000_000_000
    now = datetime(2026, 9, 1, 7, tzinfo=UTC)

    claim = await store.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now)
    assert claim is not None
    assert await store.expire_leases(now=now + timedelta(minutes=16)) >= 1

    run = await store.get(owner_id=owner_id, digest_date=date(2026, 9, 1))
    assert run.status is DigestRunStatus.DELIVERY_UNKNOWN
    assert await store.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now) is None


@pytest.mark.asyncio
async def test_retryable_claim_rotates_attempt_and_stale_attempt_cannot_complete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlAlchemyDigestRunStore(session_factory)
    owner_id = uuid4().int % 2_000_000_000
    now = datetime(2026, 9, 1, 7, tzinfo=UTC)
    first = await store.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now)
    assert first is not None
    assert await store.mark_retryable(
        owner_id=owner_id, digest_date=date(2026, 9, 1), attempt_id=first.attempt_id
    )
    second = await store.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now)
    assert second is not None and second.attempt_id != first.attempt_id
    assert not await store.mark_delivered(
        owner_id=owner_id,
        digest_date=date(2026, 9, 1),
        attempt_id=first.attempt_id,
        delivered_at=now,
        late=False,
    )
    assert await store.mark_delivered(
        owner_id=owner_id,
        digest_date=date(2026, 9, 1),
        attempt_id=second.attempt_id,
        delivered_at=now,
        late=False,
    )


@pytest.mark.asyncio
async def test_two_independent_stores_race_to_one_durable_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4().int % 2_000_000_000
    now = datetime(2026, 9, 1, 7, tzinfo=UTC)
    first, second = await asyncio.gather(
        SqlAlchemyDigestRunStore(session_factory).claim(
            owner_id=owner_id, digest_date=date(2026, 9, 1), now=now
        ),
        SqlAlchemyDigestRunStore(session_factory).claim(
            owner_id=owner_id, digest_date=date(2026, 9, 1), now=now
        ),
    )
    assert sum(claim is not None for claim in (first, second)) == 1


@pytest.mark.asyncio
async def test_expire_leases_sweeps_prior_day(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlAlchemyDigestRunStore(session_factory)
    owner_id = uuid4().int % 2_000_000_000
    yesterday = datetime(2026, 8, 31, 7, tzinfo=UTC)
    assert await store.claim(owner_id=owner_id, digest_date=date(2026, 8, 31), now=yesterday)
    assert await store.expire_leases(now=datetime(2026, 9, 1, 7, tzinfo=UTC)) == 1
    assert (
        await store.get(owner_id=owner_id, digest_date=date(2026, 8, 31))
    ).status is DigestRunStatus.DELIVERY_UNKNOWN
