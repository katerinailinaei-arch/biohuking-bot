from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class SafeErrorCode(StrEnum):
    OWNER_FORBIDDEN = "owner_forbidden"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_BLOCKED = "source_blocked"
    EXTRACTION_FAILED = "extraction_failed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_QUOTA_EXHAUSTED = "llm_quota_exhausted"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_INVALID_OUTPUT = "llm_invalid_output"
    MEDICAL_REVIEW_INCOMPLETE = "medical_review_incomplete"
    STYLE_PROFILE_NOT_READY = "style_profile_not_ready"
    APPROVAL_STALE = "approval_stale"
    PUBLICATION_FAILED = "publication_failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    BACKUP_STALE = "backup_stale"
    INVALID_TRANSITION = "invalid_transition"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class SafeErrorTemplate:
    message_ru: str
    preserved_ru: str
    next_action_ru: str


_TEMPLATES: dict[SafeErrorCode, SafeErrorTemplate] = {
    SafeErrorCode.OWNER_FORBIDDEN: SafeErrorTemplate(
        "Эта команда доступна только владельцу бота.",
        "Данные проекта не изменены.",
        "Продолжите работу из разрешённого аккаунта Кети.",
    ),
    SafeErrorCode.SOURCE_UNAVAILABLE: SafeErrorTemplate(
        "Источник сейчас не отвечает.",
        "Уже полученные материалы сохранены.",
        "Попробуйте обновить источник позже или выберите другой.",
    ),
    SafeErrorCode.SOURCE_BLOCKED: SafeErrorTemplate(
        "Этот источник нельзя безопасно использовать.",
        "Остальные материалы и текущий выбор сохранены.",
        "Выберите источник из разрешённого списка.",
    ),
    SafeErrorCode.EXTRACTION_FAILED: SafeErrorTemplate(
        "Не удалось аккуратно разобрать материал.",
        "Исходный материал сохранён.",
        "Попробуйте ещё раз или добавьте более ясный текст.",
    ),
    SafeErrorCode.LLM_TIMEOUT: SafeErrorTemplate(
        "Модель не успела подготовить ответ.",
        "Текущий материал и все ваши решения сохранены.",
        "Повторите действие немного позже.",
    ),
    SafeErrorCode.LLM_RATE_LIMIT: SafeErrorTemplate(
        "Модель временно принимает слишком много запросов.",
        "Текущий материал и все ваши решения сохранены.",
        "Подождите немного и повторите действие.",
    ),
    SafeErrorCode.LLM_QUOTA_EXHAUSTED: SafeErrorTemplate(
        "Бесплатный лимит модели на этот период закончился.",
        "Данные и уже утверждённые публикации сохранены.",
        "Дождитесь обновления лимита; платный режим сам не включится.",
    ),
    SafeErrorCode.LLM_UNAVAILABLE: SafeErrorTemplate(
        "Модель сейчас недоступна.",
        "Текущий материал и все ваши решения сохранены.",
        "Попробуйте повторить действие позже.",
    ),
    SafeErrorCode.LLM_INVALID_OUTPUT: SafeErrorTemplate(
        "Модель вернула ответ, который нельзя безопасно использовать.",
        "Исходный материал и предыдущая версия сохранены.",
        "Повторите действие или уточните задачу.",
    ),
    SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE: SafeErrorTemplate(
        "Проверка медицинских утверждений ещё не завершена.",
        "Черновик и найденные источники сохранены.",
        "Завершите проверку или исправьте спорные утверждения.",
    ),
    SafeErrorCode.STYLE_PROFILE_NOT_READY: SafeErrorTemplate(
        "Профиль стиля пока не готов.",
        "Примеры и оценки сохранены.",
        "Завершите калибровку стиля.",
    ),
    SafeErrorCode.APPROVAL_STALE: SafeErrorTemplate(
        "После утверждения текст изменился.",
        "Новая версия и прежнее решение сохранены отдельно.",
        "Проверьте и явно утвердите текущую версию заново.",
    ),
    SafeErrorCode.PUBLICATION_FAILED: SafeErrorTemplate(
        "Публикацию не удалось отправить.",
        "Утверждённый текст и расписание сохранены.",
        "Проверьте соединение и повторите подтверждённую отправку.",
    ),
    SafeErrorCode.DELIVERY_UNKNOWN: SafeErrorTemplate(
        "Не удалось точно определить, была ли публикация отправлена.",
        "Утверждённый текст и данные попытки сохранены.",
        "Проверьте канал и вручную отметьте результат или подтвердите повтор.",
    ),
    SafeErrorCode.BACKUP_STALE: SafeErrorTemplate(
        "Свежая резервная копия не подтверждена.",
        "Рабочие данные остаются в основной базе.",
        "Проверьте резервное копирование до следующего релиза.",
    ),
    SafeErrorCode.INVALID_TRANSITION: SafeErrorTemplate(
        "Это действие сейчас недоступно.",
        "Текущее состояние и данные не изменены.",
        "Вернитесь к доступному шагу редакционного процесса.",
    ),
    SafeErrorCode.INTERNAL_ERROR: SafeErrorTemplate(
        "Произошла внутренняя ошибка.",
        "Уже сохранённые данные не изменены.",
        "Повторите действие; если ошибка повторится, сообщите код обращения.",
    ),
}


@dataclass(frozen=True)
class SafeError(Exception):
    code: SafeErrorCode
    message_ru: str
    preserved_ru: str
    next_action_ru: str
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    developer_detail: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message_ru)

    @classmethod
    def for_code(
        cls, code: SafeErrorCode, *, developer_detail: str | None = None
    ) -> SafeError:
        template = _TEMPLATES[code]
        return cls(
            code=code,
            message_ru=template.message_ru,
            preserved_ru=template.preserved_ru,
            next_action_ru=template.next_action_ru,
            developer_detail=developer_detail,
        )

    @property
    def user_message(self) -> str:
        return (
            f"{self.message_ru}\n\n"
            f"Сохранено: {self.preserved_ru}\n"
            f"Что можно сделать: {self.next_action_ru}\n"
            f"Код обращения: {self.trace_id}"
        )

