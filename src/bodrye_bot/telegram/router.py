from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.manual_post import ManualPost, ManualPostPolicy
from bodrye_bot.domain.workflow import Actor, WorkflowPolicy, WorkflowState, WorkflowStatus
from bodrye_bot.editorial.ports import ChannelPublisher, DraftWriter, ManualPostStore
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.views import (
    DRAFT_NEED_TOPIC,
    HELP_TEXT,
    NEUTRAL_DENIAL,
    render_manual_draft,
    render_manual_published,
    render_manual_reviewed,
    render_safe_error,
)

_WORKFLOW_TARGETS: dict[str, WorkflowStatus] = {
    "confirm_extraction": WorkflowStatus.EXTRACTION_CONFIRMED,
    "start_review": WorkflowStatus.CLAIMS_REVIEW_PENDING,
    "approve": WorkflowStatus.APPROVED,
    "schedule": WorkflowStatus.SCHEDULED,
    "mark_published": WorkflowStatus.PUBLISHED,
}
_MANUAL_CALLBACK_ACTIONS = frozenset({"reviewed", "publish_now"})
_CALLBACK_ACTIONS = frozenset(_WORKFLOW_TARGETS) | _MANUAL_CALLBACK_ACTIONS
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    sender_id: int
    text: str


@dataclass(frozen=True)
class IncomingCallback:
    sender_id: int
    data: str


@dataclass(frozen=True)
class TelegramButton:
    text: str
    callback_data: str


@dataclass(frozen=True)
class TelegramResponse:
    text: str
    gates: frozenset[str] = field(default_factory=frozenset)
    ready: bool | None = None
    buttons: tuple[TelegramButton, ...] = ()


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
        if action not in _CALLBACK_ACTIONS:
            raise ValueError("Unsupported callback action")
        encoded_id = base64.urlsafe_b64encode(record_id.bytes).rstrip(b"=").decode("ascii")
        expires = _to_base36(int(expires_at.timestamp()))
        unsigned = f"{action}:{encoded_id}:{expires}"
        signature = self._sign(unsigned)
        return f"{unsigned}:{signature}"

    def decode(self, token: str) -> CallbackPayload:
        try:
            action, encoded_id, raw_expiry, signature = token.split(":")
            if action not in _CALLBACK_ACTIONS:
                raise ValueError("Unsupported callback action")
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
        workflow_policy: WorkflowPolicy | None = None,
        callback_ttl: timedelta = timedelta(hours=12),
        clock: Callable[[], datetime] | None = None,
        manual_post_store: ManualPostStore | None = None,
        draft_writer: DraftWriter | None = None,
        channel_publisher: ChannelPublisher | None = None,
        manual_post_policy: ManualPostPolicy | None = None,
    ) -> None:
        self._owner_guard = owner_guard
        self._onboarding = onboarding if onboarding is not None else OnboardingService()
        self._workflow_repository = workflow_repository
        self._callback_codec = callback_codec
        self._workflow_policy = workflow_policy if workflow_policy is not None else WorkflowPolicy()
        self._callback_ttl = callback_ttl
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._manual_post_store = manual_post_store
        self._draft_writer = draft_writer
        self._channel_publisher = channel_publisher
        self._manual_post_policy = (
            manual_post_policy if manual_post_policy is not None else ManualPostPolicy()
        )

    async def handle(self, message: IncomingMessage) -> TelegramResponse:
        try:
            owner_id = self._owner_guard.authorize(message.sender_id)
            if message.text == "/start":
                result = await self._onboarding.check()
                text = (
                    f"{result.text}\n\n"
                    "Этот список — большой план на потом. Короткий путь уже можно пробовать:\n"
                    f"{HELP_TEXT}"
                )
                return TelegramResponse(text=text, gates=result.gates, ready=result.ready)
            command, _, rest = message.text.partition(" ")
            if command == "/draft":
                return await self._create_draft(owner_id, rest.strip())
            if command == "/reviewed":
                return await self._review_latest(owner_id)
            if command == "/publish":
                return await self._publish_latest(owner_id)
            text = {
                "/status": "Статус проверяется в рабочем контуре.",
                "/settings": "Настройки доступны только через безопасные шаги мастера.",
                "/sources": "Разрешённые источники будут показаны после проверки доступа.",
                "/style": "Профиль стиля доступен после калибровки.",
                "/costs": "Использование будет показано после подключения учёта.",
                "/help": HELP_TEXT,
            }.get(command, "Не удалось распознать команду. Используйте /help.")
            return TelegramResponse(text=text)
        except SafeError as error:
            return self._owner_denial_or_safe_error(error)
        except Exception as error:
            return _internal_error_response("message", error)

    async def handle_callback(self, callback: IncomingCallback) -> TelegramResponse:
        try:
            owner_id = self._owner_guard.authorize(callback.sender_id)
            if self._callback_codec is None:
                return _invalid_transition_response()
            try:
                payload = self._callback_codec.decode(callback.data)
            except ValueError:
                return _invalid_transition_response()
            if payload.action in _MANUAL_CALLBACK_ACTIONS:
                return await self._handle_manual_callback(owner_id, payload.action, payload.record_id)
            if self._workflow_repository is None:
                return _invalid_transition_response()
            state = await self._workflow_repository.get(owner_id, payload.record_id)
            updated = self._workflow_policy.transition(
                state, _WORKFLOW_TARGETS[payload.action], Actor.OWNER
            )
            if updated.version is None:
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
            await self._workflow_repository.save(updated, expected_version=updated.version)
        except SafeError as error:
            return self._owner_denial_or_safe_error(error)
        except Exception as error:
            return _internal_error_response("callback", error)
        if payload.action == "approve":
            return TelegramResponse("Черновик утверждён после повторной проверки состояния.")
        return TelegramResponse("Действие подтверждено после повторной проверки состояния.")

    async def _create_draft(self, owner_id: int, topic: str) -> TelegramResponse:
        if not topic:
            return TelegramResponse(DRAFT_NEED_TOPIC)
        if self._draft_writer is None or self._manual_post_store is None:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        post = ManualPost.create(
            owner_id=owner_id, topic=topic, body=self._draft_writer.write(topic)
        )
        await self._manual_post_store.save(post)
        return TelegramResponse(
            text=render_manual_draft(post),
            buttons=self._manual_buttons(post.id, reviewed=False),
        )

    async def _review_latest(self, owner_id: int) -> TelegramResponse:
        if self._manual_post_store is None:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        post = await self._manual_post_store.latest(owner_id)
        return await self._review_post(post)

    async def _review_post(self, post: ManualPost) -> TelegramResponse:
        if self._manual_post_store is None:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        reviewed = self._manual_post_policy.mark_reviewed(post)
        await self._manual_post_store.save(reviewed)
        return TelegramResponse(
            text=render_manual_reviewed(reviewed),
            buttons=self._manual_buttons(reviewed.id, reviewed=True),
        )

    async def _publish_latest(self, owner_id: int) -> TelegramResponse:
        if self._manual_post_store is None:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        post = await self._manual_post_store.latest(owner_id)
        return await self._publish_post(post)

    async def _publish_post(self, post: ManualPost) -> TelegramResponse:
        if self._manual_post_store is None or self._channel_publisher is None:
            raise SafeError.for_code(SafeErrorCode.PUBLICATION_FAILED)
        published = self._manual_post_policy.publish(post)
        await self._channel_publisher.publish(owner_id=post.owner_id, text=published.body)
        await self._manual_post_store.save(published)
        return TelegramResponse(text=render_manual_published())

    async def _handle_manual_callback(
        self, owner_id: int, action: str, post_id: UUID
    ) -> TelegramResponse:
        if self._manual_post_store is None:
            return _invalid_transition_response()
        post = await self._manual_post_store.get(owner_id, post_id)
        if action == "reviewed":
            return await self._review_post(post)
        return await self._publish_post(post)

    def _manual_buttons(self, post_id: UUID, *, reviewed: bool) -> tuple[TelegramButton, ...]:
        if self._callback_codec is None:
            return ()
        expires_at = self._clock() + self._callback_ttl
        if reviewed:
            return (
                TelegramButton(
                    text="Опубликовать в канал",
                    callback_data=self._callback_codec.encode(
                        "publish_now", post_id, expires_at=expires_at
                    ),
                ),
            )
        return (
            TelegramButton(
                text="Я проверила факты",
                callback_data=self._callback_codec.encode(
                    "reviewed", post_id, expires_at=expires_at
                ),
            ),
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


def _internal_error_response(event: str, error: Exception) -> TelegramResponse:
    _LOG.error(
        "telegram_shell_internal_error",
        extra={
            "safe_error_code": SafeErrorCode.INTERNAL_ERROR.value,
            "event": event,
            "exception_type": type(error).__name__,
        },
    )
    return TelegramResponse(render_safe_error(SafeError.for_code(SafeErrorCode.INTERNAL_ERROR)))


__all__ = [
    "CallbackCodec",
    "CallbackPayload",
    "IncomingCallback",
    "IncomingMessage",
    "TelegramButton",
    "TelegramResponse",
    "TelegramShell",
]
