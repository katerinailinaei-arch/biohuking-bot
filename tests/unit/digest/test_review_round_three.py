from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bodrye_bot.digest.service import ScoringSnapshot
from bodrye_bot.digest.worker import DeliveryOutcome, DigestWorker
from bodrye_bot.domain.errors import SafeError, SafeErrorCode


def test_snapshot_declares_risk_min_aggregation_algorithm() -> None:
    snapshot = ScoringSnapshot.default()
    assert snapshot.aggregation == "component_max_risk_min_v2"
    assert "risk_min_v2" in snapshot.id


@pytest.mark.asyncio
async def test_stale_delivered_fence_never_reports_delivery_success() -> None:
    class Runs:
        async def expire_leases(self, *, now):
            return 0

        async def claim(self, **kwargs):
            return SimpleNamespace(attempt_id=uuid4())

        async def mark_delivered(self, **kwargs):
            return False

        async def mark_retryable(self, **kwargs):
            return False

        async def mark_unknown(self, **kwargs):
            return False

    class Loader:
        async def load(self, **kwargs):
            return (), ()

    class Telegram:
        async def deliver(self, **kwargs):
            return DeliveryOutcome.SENT

    class Clock:
        def now(self):
            return datetime(2026, 9, 1, 7, 12, tzinfo=UTC)

    worker = DigestWorker(
        owner_id=42, loader=Loader(), runs=Runs(), telegram=Telegram(), clock=Clock()
    )
    with pytest.raises(SafeError) as caught:
        await worker.run_due(datetime(2026, 9, 1, 7, tzinfo=UTC))
    assert caught.value.code is SafeErrorCode.DELIVERY_UNKNOWN
