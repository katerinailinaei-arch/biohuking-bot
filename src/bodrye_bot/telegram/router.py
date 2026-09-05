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

from bodrye_bot.digest.memory import CARD_SHELF, DigestCardShelf
from bodrye_bot.digest.views import digest_cover_url
from bodrye_bot.digest.worker import DigestWorker
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.headlines import russian_headline
from bodrye_bot.domain.manual_post import ManualPost, ManualPostPolicy
from bodrye_bot.domain.workflow import Actor, WorkflowPolicy, WorkflowState, WorkflowStatus
from bodrye_bot.editorial.ports import ChannelPublisher, DraftWriter, ManualPostStore
from bodrye_bot.editorial.studio import StudioKind, StudioWriter
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService
from bodrye_bot.telegram.owner_guide import InMemoryOwnerGuide, OwnerGuide
from bodrye_bot.telegram.studio_state import StudioSession, StudioSessionStore, StudioWait
from bodrye_bot.telegram.views import (
    CARD_KEEP_PREFIX,
    CARD_SKIP_TEXT,
    DRAFT_NEED_TOPIC,
    INLINE_PUBLISH,
    INLINE_REFINE,
    INLINE_REGEN,
    INLINE_REVIEWED,
    MAIN_MENU_TEXT,
    MENU_HELP,
    MENU_POST,
    MENU_PUBLISH,
    MENU_REVIEWED,
    MENU_TOPICS,
    NEUTRAL_DENIAL,
    ONBOARDING_MESSAGES,
    PYTHON_IN_CHAT,
    RETURNING_START_TEXT,
    REVISE_PROMPT,
    SETTOV_MORE,
    SETTOV_NEED_SAMPLE,
    SETTOV_PROMPT,
    SETTOV_SAVED,
    STUDIO_PROMPTS,
    render_manual_draft,
    render_manual_published,
    render_manual_reviewed,
    render_safe_error,
    render_studio_text,
)

_WORKFLOW_TARGETS: dict[str, WorkflowStatus] = {
    "confirm_extraction": WorkflowStatus.EXTRACTION_CONFIRMED,
    "start_review": WorkflowStatus.CLAIMS_REVIEW_PENDING,
    "approve": WorkflowStatus.APPROVED,
    "schedule": WorkflowStatus.SCHEDULED,
    "mark_published": WorkflowStatus.PUBLISHED,
}
_MANUAL_CALLBACK_ACTIONS = frozenset({"reviewed", "publish_now"})
_STUDIO_CALLBACK_ACTIONS = frozenset({"refine", "regen", "copy", "home"})
_DIGEST_CALLBACK_ACTIONS = frozenset({"develop", "keep", "skip"})
_CALLBACK_ACTIONS = (
    frozenset(_WORKFLOW_TARGETS)
    | _MANUAL_CALLBACK_ACTIONS
    | _STUDIO_CALLBACK_ACTIONS
    | _DIGEST_CALLBACK_ACTIONS
)
_MENU_KIND = {
    MENU_POST: StudioKind.POST,
    "Пост": StudioKind.POST,
    "Написать пост": StudioKind.POST,
}
_PROMPT_BY_KIND = {
    StudioKind.POST: STUDIO_PROMPTS[MENU_POST],
    StudioKind.STORIES: STUDIO_PROMPTS[MENU_POST],
    StudioKind.HEADLINES: STUDIO_PROMPTS[MENU_POST],
    StudioKind.MY_STORY: STUDIO_PROMPTS[MENU_POST],
    StudioKind.SHORT: STUDIO_PROMPTS[MENU_POST],
}
_TOPICS_LABELS = frozenset({MENU_TOPICS, "Темы"})
_REVIEWED_LABELS = frozenset({MENU_REVIEWED, "Я проверила"})
_PUBLISH_LABELS = frozenset({MENU_PUBLISH, "В канал"})
_HELP_LABELS = frozenset({MENU_HELP, "Помощь", "/help"})
_DONE_PHRASES = frozenset({"готово", "готово.", "достаточно"})
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    sender_id: int
    text: str
    transcribed: bool = False


@dataclass(frozen=True)
class IncomingCallback:
    sender_id: int
    data: str


@dataclass(frozen=True)
class TelegramButton:
    text: str
    callback_data: str = ""
    url: str | None = None


@dataclass(frozen=True)
class TelegramResponse:
    text: str
    gates: frozenset[str] = field(default_factory=frozenset)
    ready: bool | None = None
    buttons: tuple[TelegramButton, ...] = ()
    show_main_keyboard: bool = False
    toast: str | None = None
    skip_message: bool = False
    photo_url: str | None = None
    extra_messages: tuple[str, ...] = ()


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
    """Owner-guarded Telegram commands; digest fetch is the only network path."""

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
        digest_worker: DigestWorker | None = None,
        manual_post_policy: ManualPostPolicy | None = None,
        studio_writer: StudioWriter | None = None,
        studio_sessions: StudioSessionStore | None = None,
        card_shelf: DigestCardShelf | None = None,
        owner_guide: OwnerGuide | None = None,
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
        self._digest_worker = digest_worker
        self._manual_post_policy = (
            manual_post_policy if manual_post_policy is not None else ManualPostPolicy()
        )
        self._studio_writer = studio_writer if studio_writer is not None else StudioWriter()
        self._studio_sessions = (
            studio_sessions if studio_sessions is not None else StudioSessionStore()
        )
        self._card_shelf = card_shelf if card_shelf is not None else CARD_SHELF
        self._owner_guide = owner_guide if owner_guide is not None else InMemoryOwnerGuide()

    async def handle(self, message: IncomingMessage) -> TelegramResponse:
        try:
            owner_id = self._owner_guard.authorize(message.sender_id)
            if message.text.strip().lower().startswith("python"):
                return TelegramResponse(PYTHON_IN_CHAT)
            label = message.text.strip()
            if label in _TOPICS_LABELS:
                return await self._send_digest()
            if label in _REVIEWED_LABELS:
                return await self._review_latest(owner_id)
            if label in _PUBLISH_LABELS:
                return await self._publish_latest(owner_id)
            if label in _HELP_LABELS:
                return _guide_response()
            menu_kind = _MENU_KIND.get(label)
            if menu_kind is not None:
                return self._ask_studio_topic(owner_id, menu_kind)
            if message.text == "/start":
                return await self._start(owner_id)
            session = self._studio_sessions.get(owner_id)
            command, rest = _split_command(message.text)
            if command == "/settov":
                return self._begin_tone(owner_id)
            if command == "/help":
                return _guide_response()
            waiting_tone = session is not None and session.wait is StudioWait.TONE
            if waiting_tone and not command.startswith("/"):
                return self._collect_tone(owner_id, message.text)
            if session is not None and not command.startswith("/"):
                if session.wait is StudioWait.REVISE or (
                    message.transcribed and session.post_id is not None
                ):
                    return await self._revise_studio(owner_id, session, message.text)
                if session.wait is StudioWait.TOPIC:
                    return await self._generate_studio(owner_id, session.kind, message.text)
            if command == "/draft":
                return await self._create_draft(owner_id, rest)
            if command == "/reviewed":
                return await self._review_latest(owner_id)
            if command == "/publish":
                return await self._publish_latest(owner_id)
            if command == "/digest":
                return await self._send_digest()
            if message.transcribed and message.text.strip():
                return await self._generate_studio(
                    owner_id, StudioKind.POST, message.text
                )
            text = {
                "/status": "Статус проверяется в рабочем контуре.",
                "/settings": "Настройки доступны только через безопасные шаги мастера.",
                "/sources": "Разрешённые источники будут показаны после проверки доступа.",
                "/style": (
                    "Профиль стиля доступен после калибровки. "
                    "Для живого тона используйте /settov."
                ),
                "/costs": "Использование будет показано после подключения учёта.",
            }.get(command, "Не удалось распознать команду. Используйте меню внизу или /help.")
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
            if payload.action in _DIGEST_CALLBACK_ACTIONS:
                return await self._handle_digest_card(
                    owner_id, payload.action, payload.record_id
                )
            if payload.action in _STUDIO_CALLBACK_ACTIONS:
                return await self._handle_studio_callback(
                    owner_id, payload.action, payload.record_id
                )
            if payload.action in _MANUAL_CALLBACK_ACTIONS:
                return await self._handle_manual_callback(
                    owner_id, payload.action, payload.record_id
                )
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

    async def _start(self, owner_id: int) -> TelegramResponse:
        result = await self._onboarding.check()
        if not self._owner_guide.has_completed_onboarding(owner_id):
            self._owner_guide.mark_onboarding_complete(owner_id)
            return TelegramResponse(
                text=ONBOARDING_MESSAGES[0],
                extra_messages=ONBOARDING_MESSAGES[1:],
                gates=result.gates,
                ready=result.ready,
                show_main_keyboard=True,
            )
        return TelegramResponse(
            text=RETURNING_START_TEXT,
            gates=result.gates,
            ready=result.ready,
            show_main_keyboard=True,
        )

    def _begin_tone(self, owner_id: int) -> TelegramResponse:
        self._owner_guide.replace_tone_samples(owner_id, ())
        self._studio_sessions.put(
            owner_id, StudioSession(kind=StudioKind.POST, wait=StudioWait.TONE)
        )
        return TelegramResponse(SETTOV_PROMPT, show_main_keyboard=True)

    def _collect_tone(self, owner_id: int, raw: str) -> TelegramResponse:
        if raw.strip().lower() in _DONE_PHRASES:
            samples = self._owner_guide.tone_samples(owner_id)
            if not samples:
                return TelegramResponse(SETTOV_NEED_SAMPLE, show_main_keyboard=True)
            self._studio_sessions.clear(owner_id)
            return TelegramResponse(
                SETTOV_SAVED.format(count=len(samples)),
                show_main_keyboard=True,
            )
        self._owner_guide.add_tone_sample(owner_id, raw)
        return TelegramResponse(SETTOV_MORE, show_main_keyboard=True)

    async def _send_digest(self) -> TelegramResponse:
        if self._digest_worker is None:
            return TelegramResponse(
                "Дайджест ещё не подключен в этом запуске. "
                "Перезапустите бота командой python -m bodrye_bot.main_bot в PowerShell."
            )
        delivery = await self._digest_worker.run_due(self._clock(), force=True)
        if delivery is None:
            return TelegramResponse(
                "Сегодня дайджест уже отправлялся. "
                "Новый автоматический — в будний день после 10:00 по Москве."
            )
        return TelegramResponse(
            "Дайджест отправил отдельным сообщением в этот чат. "
            "«Развить» — черновик, «Сохранить» — отложить, «Не интересно» — пропустить, "
            "«Источник» — открыть ссылку. В канал ничего не публикуется, пока вы не нажмёте "
            f"«{MENU_REVIEWED}» и «{MENU_PUBLISH}»."
        )

    async def _handle_digest_card(
        self, owner_id: int, action: str, card_id: UUID
    ) -> TelegramResponse:
        card = self._card_shelf.get(owner_id, card_id)
        if card is None:
            return TelegramResponse("Эта карточка уже неактуальна. Нажмите «Темы» ещё раз.")
        if action == "keep":
            label = russian_headline(card.title, card.rubric)
            self._card_shelf.keep(owner_id, label)
            return TelegramResponse(f"{CARD_KEEP_PREFIX} {label}")
        if action == "skip":
            return TelegramResponse(CARD_SKIP_TEXT)
        topic = russian_headline(card.title, card.rubric)
        generated = await self._generate_studio(owner_id, StudioKind.SHORT, topic)
        return TelegramResponse(
            text=generated.text,
            buttons=generated.buttons,
            photo_url=digest_cover_url(topic),
        )

    def _ask_studio_topic(self, owner_id: int, kind: StudioKind) -> TelegramResponse:
        self._studio_sessions.put(
            owner_id, StudioSession(kind=kind, wait=StudioWait.TOPIC)
        )
        return TelegramResponse(_PROMPT_BY_KIND[kind], show_main_keyboard=True)

    async def _generate_studio(
        self, owner_id: int, kind: StudioKind, topic: str, *, note: str = "", variant: int = 1
    ) -> TelegramResponse:
        cleaned = topic.strip()
        if not cleaned:
            return TelegramResponse(_PROMPT_BY_KIND[kind])
        if self._manual_post_store is None:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        body = self._studio_writer.write(
            cleaned,
            kind=kind,
            variant=variant,
            note=note,
            tone_samples=self._owner_guide.tone_samples(owner_id),
        )
        post = ManualPost.create(owner_id=owner_id, topic=cleaned, body=body)
        await self._manual_post_store.save(post)
        self._studio_sessions.put(
            owner_id,
            StudioSession(
                kind=kind,
                wait=StudioWait.TOPIC,
                topic=cleaned,
                last_text=body,
                variant=variant,
                post_id=post.id,
            ),
        )
        return TelegramResponse(
            text=render_studio_text(body),
            buttons=self._studio_buttons(post.id),
        )

    async def _revise_studio(
        self, owner_id: int, session: StudioSession, note: str
    ) -> TelegramResponse:
        return await self._generate_studio(
            owner_id,
            session.kind,
            session.topic,
            note=note,
            variant=session.variant + 1,
        )

    async def _handle_studio_callback(
        self, owner_id: int, action: str, post_id: UUID
    ) -> TelegramResponse:
        session = self._studio_sessions.get(owner_id)
        if action == "home":
            self._studio_sessions.clear(owner_id)
            return TelegramResponse(MAIN_MENU_TEXT, show_main_keyboard=True)
        if action == "copy":
            return TelegramResponse(
                "",
                toast="Зажмите сообщение с текстом и нажмите Копировать.",
                skip_message=True,
            )
        if session is None or session.post_id != post_id:
            if self._manual_post_store is None:
                return _invalid_transition_response()
            post = await self._manual_post_store.get(owner_id, post_id)
            session = StudioSession(
                kind=StudioKind.POST,
                wait=StudioWait.TOPIC,
                topic=post.topic,
                last_text=post.body,
                post_id=post.id,
            )
            self._studio_sessions.put(owner_id, session)
        if action == "refine":
            session.wait = StudioWait.REVISE
            self._studio_sessions.put(owner_id, session)
            return TelegramResponse(REVISE_PROMPT)
        if action == "regen":
            return await self._generate_studio(
                owner_id,
                session.kind,
                session.topic,
                variant=session.variant + 1,
            )
        return _invalid_transition_response()

    def _studio_buttons(self, post_id: UUID) -> tuple[TelegramButton, ...]:
        if self._callback_codec is None:
            return ()
        expires_at = self._clock() + self._callback_ttl
        return tuple(
            TelegramButton(
                text=label,
                callback_data=self._callback_codec.encode(action, post_id, expires_at=expires_at),
            )
            for action, label in (
                ("refine", INLINE_REFINE),
                ("regen", INLINE_REGEN),
                ("reviewed", INLINE_REVIEWED),
                ("publish_now", INLINE_PUBLISH),
            )
        )

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
            buttons=self._studio_buttons(post.id),
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
            buttons=self._studio_buttons(reviewed.id),
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
        return TelegramResponse(text=render_manual_published(), show_main_keyboard=True)

    async def _handle_manual_callback(
        self, owner_id: int, action: str, post_id: UUID
    ) -> TelegramResponse:
        if self._manual_post_store is None:
            return _invalid_transition_response()
        post = await self._manual_post_store.get(owner_id, post_id)
        if action == "reviewed":
            return await self._review_post(post)
        return await self._publish_post(post)

    @staticmethod
    def _owner_denial_or_safe_error(error: SafeError) -> TelegramResponse:
        if error.code is SafeErrorCode.OWNER_FORBIDDEN:
            return TelegramResponse(NEUTRAL_DENIAL)
        return TelegramResponse(render_safe_error(error))


def _guide_response() -> TelegramResponse:
    return TelegramResponse(
        text=ONBOARDING_MESSAGES[0],
        extra_messages=ONBOARDING_MESSAGES[1:],
        show_main_keyboard=True,
    )


def _split_command(text: str) -> tuple[str, str]:
    command, _, rest = text.strip().partition(" ")
    return command.split("@", 1)[0], rest.strip()


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
