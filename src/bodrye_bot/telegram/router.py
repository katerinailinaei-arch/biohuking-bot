from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.workflow import Actor, WorkflowPolicy, WorkflowState, WorkflowStatus
from bodrye_bot.identity.sensitive import SENSITIVE_CONFIRMATION_TEXT, SensitiveInputGuard
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.views import NEUTRAL_DENIAL, render_safe_error

_TARGETS: dict[str, WorkflowStatus] = {
    "confirm_extraction": WorkflowStatus.EXTRACTION_CONFIRMED,
    "start_review": WorkflowStatus.CLAIMS_REVIEW_PENDING,
    "approve": WorkflowStatus.APPROVED,
    "schedule": WorkflowStatus.SCHEDULED,
    "mark_published": WorkflowStatus.PUBLISHED,
    "retry": WorkflowStatus.SCHEDULED,
}


@dataclass(frozen=True)
class IncomingMessage:
    sender_id: int
    text: str


@dataclass(frozen=True)
class IncomingCallback:
    sender_id: int
    data: str


@dataclass(frozen=True)
class TelegramResponse:
    text: str
    gates: frozenset[str] = field(default_factory=frozenset)
    ready: bool | None = None


@dataclass(frozen=True)
class CallbackPayload:
    action: str
    record_id: UUID
    expires_at: datetime


class WorkflowStore(Protocol):
    async def get(self, owner_id: int, workflow_id: UUID) -> WorkflowState: ...

    async def save(self, workflow: WorkflowState, expected_version: int) -> None: ...


class CallbackCodec:
    """Compact signed callbacks: action, opaque record id, and expiry only."""

    def __init__(
        self, secret: bytes, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        if not secret:
            raise ValueError("Callback signing secret must not be empty")
        self._secret = secret
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def encode(self, action: str, record_id: UUID, *, expires_at: datetime) -> str:
        if not action.isascii() or not action.isidentifier() or len(action) > 16:
            raise ValueError("Unsupported callback action")
        encoded_id = base64.urlsafe_b64encode(record_id.bytes).rstrip(b"=").decode("ascii")
        expires = _to_base36(int(expires_at.timestamp()))
        unsigned = f"{action}:{encoded_id}:{expires}"
        signature = self._sign(unsigned)
        return f"{unsigned}:{signature}"

    def decode(self, token: str) -> CallbackPayload:
        try:
            action, encoded_id, raw_expiry, signature = token.split(":")
            unsigned = f"{action}:{encoded_id}:{raw_expiry}"
            if not hmac.compare_digest(signature, self._sign(unsigned)):
                raise ValueError("Invalid callback signature")
            record_id = UUID(bytes=base64.urlsafe_b64decode(f"{encoded_id}=="))
            expires_at = datetime.fromtimestamp(_from_base36(raw_expiry), tz=UTC)
        except (ValueError, UnicodeEncodeError) as error:
            raise ValueError("Invalid callback") from error
        if expires_at <= self._clock():
            raise ValueError("Expired callback")
        return CallbackPayload(action=action, record_id=record_id, expires_at=expires_at)

    def _sign(self, unsigned: str) -> str:
        digest = hmac.new(self._secret, unsigned.encode("ascii"), hashlib.sha256).digest()[:9]
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class TelegramShell:
    """Guard-first application adapter; it deliberately makes no network calls."""

    def __init__(
        self,
        *,
        owner_guard: OwnerGuard,
        onboarding: OnboardingService | None = None,
        workflow_repository: WorkflowStore | None = None,
        callback_codec: CallbackCodec | None = None,
        sensitive_input: SensitiveInputGuard | None = None,
        workflow_policy: WorkflowPolicy | None = None,
    ) -> None:
        self._owner_guard = owner_guard
        self._onboarding = onboarding if onboarding is not None else OnboardingService()
        self._workflow_repository = workflow_repository
        self._callback_codec = callback_codec
        self._sensitive_input = sensitive_input
        self._workflow_policy = workflow_policy if workflow_policy is not None else WorkflowPolicy()

    async def handle(self, message: IncomingMessage) -> TelegramResponse:
        try:
            self._owner_guard.authorize(message.sender_id)
        except SafeError as error:
            return self._owner_denial_or_safe_error(error)

        if message.text == "/start":
            result = await self._onboarding.check()
            return TelegramResponse(text=result.text, gates=result.gates, ready=result.ready)
        command = message.text.split(maxsplit=1)[0]
        text = {
            "/status": "Статус проверяется в рабочем контуре.",
            "/settings": "Настройки доступны только через безопасные шаги мастера.",
            "/sources": "Разрешённые источники будут показаны после проверки доступа.",
            "/style": "Профиль стиля доступен после калибровки.",
            "/costs": "Использование будет показано после подключения учёта.",
            "/help": "Используйте /start для проверки готовности.",
        }.get(command, "Не удалось распознать команду. Используйте /help.")
        return TelegramResponse(text=text)

    async def handle_callback(self, callback: IncomingCallback) -> TelegramResponse:
        try:
            owner_id = self._owner_guard.authorize(callback.sender_id)
        except SafeError as error:
            return self._owner_denial_or_safe_error(error)
        if self._callback_codec is None:
            return _invalid_transition_response()
        try:
            payload = self._callback_codec.decode(callback.data)
        except ValueError:
            return _invalid_transition_response()

        if payload.action in {"sconfirm", "scancel"}:
            return await self._handle_sensitive_callback(owner_id, payload)
        if self._workflow_repository is None or payload.action not in _TARGETS:
            return _invalid_transition_response()
        try:
            state = await self._workflow_repository.get(owner_id, payload.record_id)
            updated = self._workflow_policy.transition(state, _TARGETS[payload.action], Actor.OWNER)
            if updated.version is None:
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
            await self._workflow_repository.save(updated, expected_version=updated.version)
        except SafeError as error:
            return TelegramResponse(render_safe_error(error))
        if payload.action == "approve":
            return TelegramResponse("Черновик утверждён после повторной проверки состояния.")
        return TelegramResponse("Действие подтверждено после повторной проверки состояния.")

    async def _handle_sensitive_callback(
        self, owner_id: int, payload: CallbackPayload
    ) -> TelegramResponse:
        if self._sensitive_input is None:
            return _invalid_transition_response()
        transient_id = payload.record_id.hex
        if payload.action == "scancel":
            await self._sensitive_input.cancel(owner_id, transient_id)
            return TelegramResponse("Временный материал удалён.")
        stored = await self._sensitive_input.confirm(
            owner_id, transient_id, SENSITIVE_CONFIRMATION_TEXT
        )
        return TelegramResponse(
            "Материал сохранён." if stored else "Подтверждение больше недоступно."
        )

    @staticmethod
    def _owner_denial_or_safe_error(error: SafeError) -> TelegramResponse:
        if error.code is SafeErrorCode.OWNER_FORBIDDEN:
            return TelegramResponse(NEUTRAL_DENIAL)
        return TelegramResponse(render_safe_error(error))


def _to_base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value < 0:
        raise ValueError("Negative timestamp")
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def _from_base36(value: str) -> int:
    if not value or any(char not in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value):
        raise ValueError("Invalid timestamp")
    return int(value, 36)


def _invalid_transition_response() -> TelegramResponse:
    return TelegramResponse(render_safe_error(SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)))


__all__ = [
    "CallbackCodec",
    "CallbackPayload",
    "IncomingCallback",
    "IncomingMessage",
    "TelegramResponse",
    "TelegramShell",
]
