from __future__ import annotations

from html import escape

from bodrye_bot.domain.errors import SafeError
from bodrye_bot.domain.manual_post import ManualPost

NEUTRAL_DENIAL = "Доступ закрыт. Если это ошибка, проверьте Telegram ID владельца."
DRAFT_NEED_TOPIC = "Напишите тему после команды, например: /draft сон после 35"
HELP_TEXT = (
    "Команды короткого контура:\n"
    "/start — проверка готовности\n"
    "/draft тема — черновик поста\n"
    "/reviewed — я проверила факты этого черновика\n"
    "/publish — опубликовать проверенный текст в канал\n"
    "/help — эта подсказка"
)


def render_safe_error(error: SafeError) -> str:
    """Render the complete safe-error contract as Telegram HTML."""
    return (
        f"<b>{escape(error.message_ru)}</b>\n\n"
        f"Сохранено: {escape(error.preserved_ru)}\n"
        f"Что можно сделать: {escape(error.next_action_ru)}\n"
        f"Код обращения: <code>{escape(error.trace_id)}</code>"
    )


def render_manual_draft(post: ManualPost) -> str:
    return (
        "<b>Черновик для твоей проверки</b>\n\n"
        f"{escape(post.body)}\n\n"
        "Это не диагноз. Если факты ок — нажми «Я проверила» или отправь /reviewed."
    )


def render_manual_reviewed(post: ManualPost) -> str:
    del post
    return (
        "Факты отмечены как проверенные тобой.\n"
        "Если текст тот же — можно публиковать: кнопка ниже или /publish."
    )


def render_manual_published() -> str:
    return "Пост опубликован в канал. Если в канале пусто — проверь, что бот админ канала."


__all__ = [
    "DRAFT_NEED_TOPIC",
    "HELP_TEXT",
    "NEUTRAL_DENIAL",
    "render_manual_draft",
    "render_manual_published",
    "render_manual_reviewed",
    "render_safe_error",
]
