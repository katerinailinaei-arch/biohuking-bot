from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import DigestRun
from bodrye_bot.digest.worker import DigestRunClaim, DigestRunStatus

_LEASE = timedelta(minutes=15)


@dataclass(frozen=True)
class DigestRunRecord:
    owner_id: int
    digest_date: date
    status: DigestRunStatus
    lease_until: datetime | None
    delivered_at: datetime | None
    late: bool | None


class _SessionDigestRunReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, *, owner_id: int, digest_date: date, now: datetime) -> bool:
        statement = (
            insert(DigestRun)
            .values(
                owner_id=owner_id,
                digest_date=digest_date,
                attempt_id=uuid4(),
                status=DigestRunStatus.PROCESSING.value,
                lease_until=now + _LEASE,
            )
            .on_conflict_do_nothing(index_elements=("owner_id", "digest_date"))
        )
        created = await self._session.execute(statement)
        if created.rowcount:  # type: ignore[attr-defined]
            return True

        query = select(DigestRun).where(
            DigestRun.owner_id == owner_id,
            DigestRun.digest_date == digest_date,
        ).with_for_update()
        run = await self._session.scalar(query)
        assert run is not None
        if (
            run.status == DigestRunStatus.PROCESSING.value
            and run.lease_until is not None
            and run.lease_until <= now
        ):
            run.status = DigestRunStatus.DELIVERY_UNKNOWN.value
            run.lease_until = None
            await self._session.flush()
        if run.status == DigestRunStatus.RETRYABLE.value:
            run.status = DigestRunStatus.PROCESSING.value
            run.lease_until = now + _LEASE
            await self._session.flush()
            return True
        return False

    async def delivered(
        self,
        *,
        owner_id: int,
        digest_date: date,
        delivered_at: datetime,
        late: bool,
    ) -> None:
        statement = (
            update(DigestRun)
            .where(
                DigestRun.owner_id == owner_id,
                DigestRun.digest_date == digest_date,
                DigestRun.status == DigestRunStatus.PROCESSING.value,
            )
            .values(
                status=DigestRunStatus.DELIVERED.value,
                lease_until=None,
                delivered_at=delivered_at,
                late=late,
            )
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("digest run is not processing")

    async def release(self, *, owner_id: int, digest_date: date) -> None:
        await self._transition(
            owner_id=owner_id,
            digest_date=digest_date,
            target=DigestRunStatus.RETRYABLE,
        )

    async def unknown(self, *, owner_id: int, digest_date: date) -> None:
        await self._transition(
            owner_id=owner_id,
            digest_date=digest_date,
            target=DigestRunStatus.DELIVERY_UNKNOWN,
        )

    async def _transition(
        self,
        *,
        owner_id: int,
        digest_date: date,
        target: DigestRunStatus,
    ) -> None:
        statement = (
            update(DigestRun)
            .where(
                DigestRun.owner_id == owner_id,
                DigestRun.digest_date == digest_date,
                DigestRun.status == DigestRunStatus.PROCESSING.value,
            )
            .values(status=target.value, lease_until=None)
        )
        await self._session.execute(statement)

    async def get(self, *, owner_id: int, digest_date: date) -> DigestRunRecord:
        query = select(DigestRun).where(
            DigestRun.owner_id == owner_id,
            DigestRun.digest_date == digest_date,
        )
        row = await self._session.scalar(query)
        if row is None:
            raise LookupError("digest run not found")
        return DigestRunRecord(
            owner_id=row.owner_id,
            digest_date=row.digest_date,
            status=DigestRunStatus(row.status),
            lease_until=row.lease_until,
            delivered_at=row.delivered_at,
            late=row.late,
        )


class SqlAlchemyDigestRunStore:
    """Durable short-transaction lifecycle store; never spans Telegram I/O."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def expire_leases(self, *, now: datetime) -> int:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(DigestRun)
                .where(
                    DigestRun.status == DigestRunStatus.PROCESSING.value,
                    DigestRun.lease_until <= now,
                )
                .values(status=DigestRunStatus.DELIVERY_UNKNOWN.value, lease_until=None)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def claim(
        self, *, owner_id: int, digest_date: date, now: datetime
    ) -> DigestRunClaim | None:
        attempt_id = uuid4()
        async with self._session_factory.begin() as session:
            created = await session.execute(
                insert(DigestRun)
                .values(
                    owner_id=owner_id,
                    digest_date=digest_date,
                    attempt_id=attempt_id,
                    status=DigestRunStatus.PROCESSING.value,
                    lease_until=now + _LEASE,
                )
                .on_conflict_do_nothing(index_elements=("owner_id", "digest_date"))
            )
            if created.rowcount:  # type: ignore[attr-defined]
                return DigestRunClaim(attempt_id)
            query = select(DigestRun).where(
                DigestRun.owner_id == owner_id,
                DigestRun.digest_date == digest_date,
            ).with_for_update()
            run = await session.scalar(query)
            assert run is not None
            if run.status != DigestRunStatus.RETRYABLE.value:
                return None
            run.status = DigestRunStatus.PROCESSING.value
            run.attempt_id = attempt_id
            run.lease_until = now + _LEASE
            return DigestRunClaim(attempt_id)

    async def mark_retryable(
        self, *, owner_id: int, digest_date: date, attempt_id: UUID
    ) -> bool:
        return await self._mark(owner_id, digest_date, attempt_id, DigestRunStatus.RETRYABLE)

    async def mark_unknown(
        self, *, owner_id: int, digest_date: date, attempt_id: UUID
    ) -> bool:
        return await self._mark(owner_id, digest_date, attempt_id, DigestRunStatus.DELIVERY_UNKNOWN)

    async def _mark(
        self, owner_id: int, digest_date: date, attempt_id: UUID, target: DigestRunStatus
    ) -> bool:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(DigestRun)
                .where(
                    DigestRun.owner_id == owner_id,
                    DigestRun.digest_date == digest_date,
                    DigestRun.attempt_id == attempt_id,
                    DigestRun.status == DigestRunStatus.PROCESSING.value,
                )
                .values(status=target.value, lease_until=None)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def mark_delivered(
        self,
        *,
        owner_id: int,
        digest_date: date,
        attempt_id: UUID,
        delivered_at: datetime,
        late: bool,
    ) -> bool:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(DigestRun)
                .where(
                    DigestRun.owner_id == owner_id,
                    DigestRun.digest_date == digest_date,
                    DigestRun.attempt_id == attempt_id,
                    DigestRun.status == DigestRunStatus.PROCESSING.value,
                )
                .values(
                    status=DigestRunStatus.DELIVERED.value,
                    lease_until=None,
                    delivered_at=delivered_at,
                    late=late,
                )
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def get(self, *, owner_id: int, digest_date: date) -> DigestRunRecord:
        async with self._session_factory() as session:
            return await _SessionDigestRunReader(session).get(
                owner_id=owner_id, digest_date=digest_date
            )


__all__ = [
    "DigestRunClaim",
    "DigestRunRecord",
    "SqlAlchemyDigestRunStore",
]
