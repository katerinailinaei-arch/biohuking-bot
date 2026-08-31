from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.repositories.digest_runs import SqlAlchemyDigestRunRepository
from bodrye_bot.db.repositories.sources import SqlAlchemySourceCatalogRepository
from bodrye_bot.db.repositories.workflows import SqlAlchemyWorkflowRepository
from bodrye_bot.operations.audit import SqlAlchemyAuditWriter
from bodrye_bot.ports.repositories import ConcurrentUpdate


class SqlAlchemyUnitOfWork:
    """Transaction scope binding owner-scoped repositories and audit writes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._workflows: SqlAlchemyWorkflowRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._catalogs: SqlAlchemySourceCatalogRepository | None = None
        self._digest_runs: SqlAlchemyDigestRunRepository | None = None
        self._active = False
        self._finished = False

    @property
    def session(self) -> AsyncSession:
        self._ensure_active()
        assert self._session is not None
        return self._session

    @property
    def workflows(self) -> SqlAlchemyWorkflowRepository:
        self._ensure_transaction_open()
        assert self._workflows is not None
        return self._workflows

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        self._ensure_transaction_open()
        assert self._audit is not None
        return self._audit

    @property
    def catalogs(self) -> SqlAlchemySourceCatalogRepository:
        self._ensure_transaction_open()
        assert self._catalogs is not None
        return self._catalogs

    @property
    def digest_runs(self) -> SqlAlchemyDigestRunRepository:
        self._ensure_transaction_open()
        assert self._digest_runs is not None
        return self._digest_runs

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        if self._active:
            raise RuntimeError("UnitOfWork is already active")

        session = self._session_factory()
        try:
            await session.begin()
        except BaseException:
            await session.close()
            raise

        self._session = session
        self._active = True
        self._finished = False
        self._audit = SqlAlchemyAuditWriter(session, ensure_active=self._ensure_transaction_open)
        self._workflows = SqlAlchemyWorkflowRepository(
            session,
            self._audit,
            ensure_active=self._ensure_transaction_open,
        )
        self._catalogs = SqlAlchemySourceCatalogRepository(
            session,
            ensure_active=self._ensure_transaction_open,
        )
        self._digest_runs = SqlAlchemyDigestRunRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._active or self._session is None:
            return
        try:
            if not self._finished and self._session.in_transaction():
                await self._session.rollback()
        finally:
            try:
                await self._session.close()
            finally:
                self._active = False
                self._finished = False
                self._session = None
                self._workflows = None
                self._audit = None
                self._catalogs = None
                self._digest_runs = None

    async def commit(self) -> None:
        self._ensure_transaction_open()
        assert self._session is not None
        await self._session.commit()
        self._finished = True

    async def rollback(self) -> None:
        self._ensure_transaction_open()
        assert self._session is not None
        await self._session.rollback()
        self._finished = True

    def _ensure_active(self) -> None:
        if not self._active:
            raise RuntimeError("UnitOfWork is not active")

    def _ensure_transaction_open(self) -> None:
        self._ensure_active()
        if self._finished:
            raise RuntimeError("UnitOfWork transaction is finished")


__all__ = ["ConcurrentUpdate", "SqlAlchemyUnitOfWork"]
