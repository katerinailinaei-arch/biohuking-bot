from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.repositories.medical import SqlAlchemyMedicalRepository
from bodrye_bot.domain.medical import ClaimReview
from bodrye_bot.medical.policy import MedicalPolicy, MedicalReviewConfiguration
from bodrye_bot.medical.review import ClaimReviewService, ClaimsEvidenceProvider


class MedicalReviewUseCase:
    """Narrow production composition seam; provider activation remains external."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: ClaimsEvidenceProvider,
        configuration: MedicalReviewConfiguration,
        clock: Callable[[], datetime],
        attempt_lease: timedelta = timedelta(minutes=5),
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._configuration = configuration
        self._clock = clock
        self._attempt_lease = attempt_lease

    async def review(self, *, owner_id: int, workflow_id: UUID) -> ClaimReview:
        repository = SqlAlchemyMedicalRepository(
            self._session_factory,
            configuration=self._configuration,
        )
        return await ClaimReviewService(
            owner_id=owner_id,
            repository=repository,
            provider=self._provider,
            policy=MedicalPolicy(self._configuration),
            configuration=self._configuration,
            clock=self._clock,
            attempt_lease=self._attempt_lease,
        ).review(workflow_id)


__all__ = ["MedicalReviewUseCase"]
