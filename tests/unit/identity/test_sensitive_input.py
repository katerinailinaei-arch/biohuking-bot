from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.identity.sensitive import SENSITIVE_CONFIRMATION_TEXT, SensitiveInputGuard
from bodrye_bot.identity.service import OwnerGuard


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.mark.asyncio
async def test_possible_medical_record_stays_transient_until_explicit_consent() -> None:
    service = SensitiveInputGuard(OwnerGuard(42))

    result = await service.inspect(
        owner_id=42, payload="Мои анализы: ФИО, дата рождения и диагноз"
    )

    assert result.requires_confirmation is True
    assert result.warning_text is not None
    assert SENSITIVE_CONFIRMATION_TEXT in result.warning_text
    assert result.confirmation_action == SENSITIVE_CONFIRMATION_TEXT
    assert await service.permanent_payload(42, result.transient_id) is None
    assert await service.confirm(42, result.transient_id, SENSITIVE_CONFIRMATION_TEXT) is True
    assert await service.permanent_payload(42, result.transient_id) == (
        "Мои анализы: ФИО, дата рождения и диагноз"
    )


@pytest.mark.asyncio
async def test_cancel_and_expiry_destroy_transient_payload() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    clock = MutableClock(now)
    service = SensitiveInputGuard(
        OwnerGuard(42), ttl=timedelta(seconds=1), clock=clock
    )
    result = await service.inspect(42, "Диагноз и анализ крови")

    await service.cancel(42, result.transient_id)
    assert await service.confirm(42, result.transient_id, SENSITIVE_CONFIRMATION_TEXT) is False

    second = await service.inspect(42, "Диагноз и анализ крови")
    clock.now = now + timedelta(seconds=2)
    assert await service.confirm(42, second.transient_id, SENSITIVE_CONFIRMATION_TEXT) is False


@pytest.mark.asyncio
async def test_sensitive_payload_reads_and_mutations_require_the_record_owner() -> None:
    service = SensitiveInputGuard(OwnerGuard(42))
    result = await service.inspect(42, "Мои анализы: ФИО, дата рождения и диагноз")

    for operation in (
        service.transient_payload(999, result.transient_id),
        service.permanent_payload(999, result.transient_id),
        service.confirm(999, result.transient_id, SENSITIVE_CONFIRMATION_TEXT),
        service.cancel(999, result.transient_id),
    ):
        with pytest.raises(SafeError) as caught:
            await operation
        assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN

    assert await service.transient_payload(42, result.transient_id) == (
        "Мои анализы: ФИО, дата рождения и диагноз"
    )


@pytest.mark.asyncio
async def test_purge_expired_removes_raw_payload_without_confirmation() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    clock = MutableClock(now)
    raw_payload = "Диагноз и анализ крови: секретные значения"
    service = SensitiveInputGuard(OwnerGuard(42), ttl=timedelta(seconds=1), clock=clock)
    result = await service.inspect(42, raw_payload)

    clock.now = now + timedelta(seconds=1)
    assert await service.purge_expired() == 1
    assert await service.transient_payload(42, result.transient_id) is None
    assert raw_payload not in repr(service._transient)


@pytest.mark.asyncio
async def test_transient_holder_repr_never_contains_raw_sensitive_payload() -> None:
    raw_payload = "Диагноз и анализ крови: секретные значения"
    service = SensitiveInputGuard(OwnerGuard(42))
    result = await service.inspect(42, raw_payload)

    holder = service._transient[result.transient_id]
    assert raw_payload not in repr(holder)
