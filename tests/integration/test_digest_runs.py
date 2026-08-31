from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.repositories.digest_runs import SqlAlchemyDigestRunRepository
from bodrye_bot.digest.worker import DigestRunStatus


@pytest.mark.asyncio
async def test_digest_run_claim_is_atomic_owner_scoped_and_round_trips(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 1, 7, tzinfo=UTC)
    owner_id = uuid4().int % 2_000_000_000
    async with session_factory() as session:
        repository = SqlAlchemyDigestRunRepository(session)
        assert await repository.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now)
        assert not await repository.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now)
        assert await repository.claim(
            owner_id=owner_id + 1, digest_date=date(2026, 9, 1), now=now
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyDigestRunRepository(session)
        run = await repository.get(owner_id=owner_id, digest_date=date(2026, 9, 1))

    assert run.status is DigestRunStatus.PROCESSING
    assert run.digest_date == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_expired_lease_becomes_delivery_unknown_never_reclaimable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 1, 7, tzinfo=UTC)
    owner_id = uuid4().int % 2_000_000_000
    async with session_factory() as session:
        repository = SqlAlchemyDigestRunRepository(session)
        assert await repository.claim(owner_id=owner_id, digest_date=date(2026, 9, 1), now=now)
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyDigestRunRepository(session)
        assert not await repository.claim(
            owner_id=owner_id, digest_date=date(2026, 9, 1), now=now + timedelta(minutes=16)
        )
        run = await repository.get(owner_id=owner_id, digest_date=date(2026, 9, 1))
        await session.commit()

    assert run.status is DigestRunStatus.DELIVERY_UNKNOWN
