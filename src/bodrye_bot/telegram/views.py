from __future__ import annotations

from html import escape

from bodrye_bot.domain.errors import SafeError
from bodrye_bot.domain.manual_post import ManualPost

NEUTRAL_DENIAL = "Доступ закрыт. Если это ошибка, проверьте Telegram ID владельца."
DRAFT_NEED_TOPIC = "Напишите тему после команды, например: /draft сон после 35"
PYTHON_IN_CHAT = (
    "Эту строку нужно вставить в PowerShell на компьютере, не в этот чат.\n"
    "Оставьте окно с ботом открытым и напишите сюда /digest."
)
MENU_TOPICS = "📰 Темы"
MENU_POST = "✍️ Пост"
MENU_REVIEWED = "✅ Я проверила"
MENU_PUBLISH = "📤 В канал"
MENU_HELP = "❓ Помощь"
INLINE_REFINE = "✏️ Доработать"
INLINE_REGEN = "🔄 Новый текст"
INLINE_REVIEWED = "✅ Я проверила"
INLINE_PUBLISH = "📤 В канал"
ONBOARDING_WHAT = (
    "<b>Что умеет бот</b>\n\n"
    "Это личный помощник для канала «Бодрые люди». За 20–30 минут можно выбрать "
    "тему, подготовить пост и отправить его в канал — только после вашей проверки.\n\n"
    "Бот умеет:\n"
    f"• собрать темы из разрешённых источников — кнопка «{MENU_TOPICS}» или /digest;\n"
    f"• написать черновик по вашей теме — кнопка «{MENU_POST}» или /draft;\n"
    "• принять голосовое и расшифровать его;\n"
    "• писать ближе к вашему тону, если загрузить примеры командой /settov;\n"
    f"• отправить пост в канал только после «{MENU_REVIEWED}».\n\n"
    "Бот не публикует сам и не ставит диагнозы. В канал выходит только то, что вы утвердили."
)
ONBOARDING_TOV = (
    "<b>Как загрузить свой тон — /settov</b>\n\n"
    "Чтобы посты звучали как ваш канал, а не «нейросеть вообще», пришлите примеры голоса.\n\n"
    "1. Напишите /settov\n"
    "2. Пришлите 2–5 своих постов текстом — или голосовые, как вы обычно рассказываете\n"
    "3. Когда хватит, напишите «готово»\n\n"
    "Обновить тон можно позже: снова /settov, затем новые примеры. Старые заменятся."
)
ONBOARDING_QUICKSTART = (
    "<b>Быстрый старт</b>\n\n"
    "Кнопки внизу экрана:\n"
    f"• {MENU_TOPICS} — свежие идеи из источников. На карточке: «Развить», "
    "«Сохранить», «Не интересно», «Источник». Это не публикация в канал.\n"
    f"• {MENU_POST} — напишите тему одной фразой или пришлите голосовое, получите черновик.\n"
    f"• {MENU_REVIEWED} — вы лично подтвердили факты. Без этого в канал нельзя.\n"
    f"• {MENU_PUBLISH} — отправить уже проверенный текст подписчикам.\n"
    f"• {MENU_HELP} — эта инструкция ещё раз.\n\n"
    f"Обычный путь: {MENU_TOPICS} → «Развить» (или {MENU_POST}) → поправить при "
    f"необходимости → {MENU_REVIEWED} → {MENU_PUBLISH}.\n\n"
    "Команды: /digest, /draft тема, /settov, /reviewed, /publish, /help."
)
ONBOARDING_MESSAGES = (ONBOARDING_WHAT, ONBOARDING_TOV, ONBOARDING_QUICKSTART)
RETURNING_START_TEXT = (
    "Снова главное меню. Если нужна инструкция — кнопка "
    f"«{MENU_HELP}» или команда /help.\n\n"
    "Кнопки внизу экрана:\n"
    f"{MENU_TOPICS} — идеи для поста, не публикация\n"
    f"{MENU_POST} — черновик по вашей теме\n"
    f"{MENU_REVIEWED} — факты проверены вами\n"
    f"{MENU_PUBLISH} — отправить проверенный текст в канал\n"
    f"{MENU_HELP} — как пользоваться ботом"
)
MAIN_MENU_TEXT = RETURNING_START_TEXT
STUDIO_PROMPTS = {
    MENU_POST: (
        "Это черновик, не отправка в канал.\n\n"
        "Напишите тему одной фразой — например «сон после 35» — или пришлите голосовое. "
        "Дальше можно доработать текст, затем «Я проверила» и только после этого «В канал»."
    ),
}
REVISE_PROMPT = (
    "Черновик останется черновиком, в канал ничего не уйдёт.\n"
    "Напишите, что изменить: тон, длина, акцент или что убрать."
)
SETTOV_PROMPT = (
    "Загружаем тон канала.\n\n"
    "Пришлите 2–5 примеров: готовые посты текстом или голосовые, как вы обычно "
    "говорите. Каждый пример — отдельным сообщением.\n\n"
    "Когда хватит, напишите «готово». Старые примеры заменятся на новые."
)
SETTOV_MORE = (
    "Пример принят. Можно прислать ещё один или написать «готово», если этого достаточно."
)
SETTOV_NEED_SAMPLE = "Пока нет ни одного примера. Пришлите текст или голосовое, затем «готово»."
SETTOV_SAVED = (
    "Тон сохранила: {count} пример(ов). "
    "Следующие черновики будут опираться на эти тексты."
)
HELP_TEXT = ONBOARDING_WHAT
TOPICS_WAIT_TEXT = (
    "Сейчас соберу темы из разрешённых источников. Это идеи для выбора, "
    "не посты в канал. Обычно до минуты."
)
CARD_SKIP_TEXT = "Ок, эту тему пропускаем. В канал ничего не ушло."
CARD_KEEP_PREFIX = "Сохранила тему для себя, без публикации:"
MISSING_DEEPGRAM = (
    "Ключа Deepgram пока нет. Откройте файл .env в папке проекта, добавьте строку "
    "DEEPGRAM_API_KEY=ваш_ключ и перезапустите бота. Ключ в этот чат не присылайте."
)


def render_safe_error(error: SafeError) -> str:
    """Render the complete safe-error contract as Telegram HTML."""
    return (
        f"<b>{escape(error.message_ru)}</b>\n\n"
        f"Сохранено: {escape(error.preserved_ru)}\n"
        f"Что можно сделать: {escape(error.next_action_ru)}\n"
        f"Код обращения: <code>{escape(error.trace_id)}</code>"
    )


def render_transcript(text: str) -> str:
    return f"<b>Расшифровка</b>\n{escape(text)}"


def render_studio_text(body: str) -> str:
    return (
        f"{escape(body)}\n\n"
        "Это черновик, не диагноз и не реклама. "
        "«Доработать» — правки, «Новый текст» — другой вариант, "
        "«Я проверила» — вы подтвердили факты, «В канал» — только после проверки."
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
    "CARD_KEEP_PREFIX",
    "CARD_SKIP_TEXT",
    "DRAFT_NEED_TOPIC",
    "HELP_TEXT",
    "INLINE_PUBLISH",
    "INLINE_REFINE",
    "INLINE_REGEN",
    "INLINE_REVIEWED",
    "MAIN_MENU_TEXT",
    "MENU_HELP",
    "MENU_POST",
    "MENU_PUBLISH",
    "MENU_REVIEWED",
    "MENU_TOPICS",
    "MISSING_DEEPGRAM",
    "NEUTRAL_DENIAL",
    "ONBOARDING_MESSAGES",
    "ONBOARDING_QUICKSTART",
    "ONBOARDING_TOV",
    "ONBOARDING_WHAT",
    "PYTHON_IN_CHAT",
    "RETURNING_START_TEXT",
    "REVISE_PROMPT",
    "SETTOV_MORE",
    "SETTOV_NEED_SAMPLE",
    "SETTOV_PROMPT",
    "SETTOV_SAVED",
    "STUDIO_PROMPTS",
    "TOPICS_WAIT_TEXT",
    "render_manual_draft",
    "render_manual_published",
    "render_manual_reviewed",
    "render_safe_error",
    "render_studio_text",
    "render_transcript",
]
