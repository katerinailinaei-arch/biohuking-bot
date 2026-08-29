from __future__ import annotations

from html import escape

from bodrye_bot.domain.errors import SafeError

NEUTRAL_DENIAL = "Доступ закрыт. Если это ошибка, проверьте Telegram ID владельца."


def render_safe_error(error: SafeError) -> str:
    """Render the complete safe-error contract as Telegram HTML."""
    return (
        f"<b>{escape(error.message_ru)}</b>\n\n"
        f"Сохранено: {escape(error.preserved_ru)}\n"
        f"Что можно сделать: {escape(error.next_action_ru)}\n"
        f"Код обращения: <code>{escape(error.trace_id)}</code>"
    )


__all__ = ["NEUTRAL_DENIAL", "render_safe_error"]
