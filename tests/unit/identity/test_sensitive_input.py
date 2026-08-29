from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bodrye_bot.identity.sensitive import SENSITIVE_CONFIRMATION_TEXT, SensitiveInputGuard
from bodrye_bot.identity.service import OwnerGuard


@pytest.mark.asyncio
async def test_possible_medical_record_stays_transient_until_explicit_consent() -> None:
    service = SensitiveInputGuard(OwnerGuard(42))

    result = await service.inspect(
        owner_id=42, payload="Мои анализы: ФИО, дата рождения и диагноз"
    )

    assert result.requires_confirmation is True
    assert await service.permanent_payload(result.transient_id) is None
    assert await service.confirm(42, result.transient_id, SENSITIVE_CONFIRMATION_TEXT) is True
    assert await service.permanent_payload(result.transient_id) == (
        "Мои анализы: ФИО, дата рождения и диагноз"
    )


@pytest.mark.asyncio
async def test_cancel_and_expiry_destroy_transient_payload() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    service = SensitiveInputGuard(
        OwnerGuard(42), ttl=timedelta(seconds=1), clock=lambda: now
    )
    result = await service.inspect(42, "Диагноз и анализ крови")

    await service.cancel(42, result.transient_id)
    assert await service.confirm(42, result.transient_id, SENSITIVE_CONFIRMATION_TEXT) is False

    second = await service.inspect(42, "Диагноз и анализ крови")
    service._clock = lambda: now + timedelta(seconds=2)  # type: ignore[method-assign]
    assert await service.confirm(42, second.transient_id, SENSITIVE_CONFIRMATION_TEXT) is False
