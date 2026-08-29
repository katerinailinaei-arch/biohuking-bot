from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

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


def test_callback_token_contains_no_owner_or_workflow_status_and_rejects_tampering() -> None:
    codec = CallbackCodec(b"test-secret")
    record_id = uuid4()
    token = codec.encode(
        "approve", record_id, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )

    assert "42" not in token
    assert WorkflowStatus.APPROVED.value not in token
    assert codec.decode(token).record_id == record_id
    with pytest.raises(ValueError):
        codec.decode(f"{token}tampered")


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
