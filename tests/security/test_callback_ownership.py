from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bodrye_bot.domain.errors import SafeErrorCode
from bodrye_bot.domain.workflow import WorkflowState, WorkflowStatus
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.router import CallbackCodec, IncomingCallback, TelegramShell


class WorkflowRepositorySpy:
    def __init__(self) -> None:
        self.get_calls: list[tuple[int, object]] = []
        self.save_calls: list[tuple[WorkflowState, int]] = []

    async def get(self, owner_id: int, workflow_id: object) -> WorkflowState:
        self.get_calls.append((owner_id, workflow_id))
        return WorkflowState(
            status=WorkflowStatus.DRAFT_REVIEW_PASSED,
            current_version_id="version",
            current_hash="hash",
            review_version_id="version",
            review_hash="hash",
            id=workflow_id,  # type: ignore[arg-type]
            owner_id=owner_id,
            version=1,
        )

    async def save(self, workflow: WorkflowState, expected_version: int) -> None:
        self.save_calls.append((workflow, expected_version))


def test_callback_token_has_deterministic_opaque_structure_and_accepts_allowed_action() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    codec = CallbackCodec(b"test-secret", clock=lambda: now)
    record_id = uuid4()
    token = codec.encode("approve", record_id, expires_at=now + timedelta(minutes=1))

    action, opaque_id, expiry, signature = token.split(":")
    decoded = codec.decode(token)

    assert action == "approve"
    assert opaque_id == base64.urlsafe_b64encode(record_id.bytes).rstrip(b"=").decode("ascii")
    assert opaque_id != record_id.hex
    assert expiry.isalnum()
    assert len(signature) == 12
    assert decoded.record_id == record_id
    assert decoded.expires_at == now + timedelta(minutes=1)


def test_callback_codec_rejects_expiry_boundary_malformed_action_and_malformed_id() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    secret = b"test-secret"
    codec = CallbackCodec(secret, clock=lambda: now)
    record_id = uuid4()

    expired = codec.encode("approve", record_id, expires_at=now)
    with pytest.raises(ValueError, match="Expired callback"):
        codec.decode(expired)
    with pytest.raises(ValueError, match="Unsupported callback action"):
        codec.encode("retry", record_id, expires_at=now + timedelta(minutes=1))

    unsigned = "approve:not-a-valid-uuid:tcw"
    signature = base64.urlsafe_b64encode(
        hmac.new(secret, unsigned.encode("ascii"), hashlib.sha256).digest()[:9]
    ).rstrip(b"=").decode("ascii")
    with pytest.raises(ValueError):
        codec.decode(f"{unsigned}:{signature}")


@pytest.mark.asyncio
async def test_callback_reauthorizes_before_owner_qualified_load_and_reruns_policy() -> None:
    repository = WorkflowRepositorySpy()
    codec = CallbackCodec(b"test-secret")
    record_id = uuid4()
    token = codec.encode(
        "approve", record_id, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    shell = TelegramShell(
        owner_guard=OwnerGuard(42), workflow_repository=repository, callback_codec=codec
    )

    denied = await shell.handle_callback(IncomingCallback(sender_id=999, data=token))
    assert denied.text == "Доступ закрыт. Если это ошибка, проверьте Telegram ID владельца."
    assert repository.get_calls == []

    accepted = await shell.handle_callback(IncomingCallback(sender_id=42, data=token))
    assert "утвержд" in accepted.text.lower()
    assert repository.get_calls == [(42, record_id)]
    assert repository.save_calls[0][0].status is WorkflowStatus.APPROVED


class ExplodingWorkflowRepository:
    async def get(self, owner_id: int, workflow_id: object) -> WorkflowState:
        raise RuntimeError("Bearer secret-token raw sensitive payload")

    async def save(self, workflow: WorkflowState, expected_version: int) -> None:
        raise AssertionError("save must not run")


@pytest.mark.asyncio
async def test_unexpected_callback_failure_is_safe_for_user_and_redacted_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    codec = CallbackCodec(b"test-secret", clock=lambda: now)
    token = codec.encode("approve", uuid4(), expires_at=now + timedelta(minutes=1))
    shell = TelegramShell(
        owner_guard=OwnerGuard(42),
        workflow_repository=ExplodingWorkflowRepository(),
        callback_codec=codec,
    )

    response = await shell.handle_callback(IncomingCallback(sender_id=42, data=token))

    assert "Произошла внутренняя ошибка" in response.text
    assert "secret-token" not in response.text
    assert "raw sensitive payload" not in response.text
    assert any(
        getattr(record, "safe_error_code", None) == SafeErrorCode.INTERNAL_ERROR.value
        and getattr(record, "event", None) == "callback"
        for record in caplog.records
    )
    assert "secret-token" not in caplog.text
    assert "raw sensitive payload" not in caplog.text
