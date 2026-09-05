from __future__ import annotations

from enum import StrEnum

from bodrye_bot.domain.headlines import russian_headline


class StudioKind(StrEnum):
    POST = "post"
    SHORT = "short"
    STORIES = "stories"
    HEADLINES = "headlines"
    MY_STORY = "my_story"


class StudioWriter:
    """Deterministic studio texts so the menu works without a live model call."""

    def write(
        self,
        topic: str,
        *,
        kind: StudioKind,
        variant: int = 1,
        note: str = "",
        tone_samples: tuple[str, ...] = (),
    ) -> str:
        title = topic.strip() or "тема дня"
        version = max(1, variant)
        extra = ""
        if note.strip() and kind is not StudioKind.SHORT:
            extra = f"\n\nУточнение: {note.strip()}"
        tone = _tone_block(tone_samples)
        body = {
            StudioKind.POST: _post(title, version),
            StudioKind.SHORT: _short(title, version),
            StudioKind.STORIES: _stories(title, version),
            StudioKind.HEADLINES: _headlines(title, version),
            StudioKind.MY_STORY: _my_story(title, version),
        }[kind]
        return f"{body}{extra}{tone}"


def _tone_block(samples: tuple[str, ...]) -> str:
    cleaned = tuple(item.strip() for item in samples if item.strip())
    if not cleaned:
        return ""
    excerpts = "\n---\n".join(item[:400] for item in cleaned[:3])
    return f"\n\nОриентир тона автора:\n{excerpts}"


def _short(title: str, version: int) -> str:
    topic = russian_headline(title)
    openings = (
        "Коротко. После 35 важнее спокойный ритм, чем подвиг на три дня.",
        "Коротко. Тело лучше слышит привычку, а не обещание за выходные.",
        "Коротко. Маленький шаг на неделю сильнее громкой схемы.",
    )
    return (
        f"{topic}\n\n"
        f"{openings[(max(1, version) - 1) % 3]}\n\n"
        "Один шаг на завтра: заметить, как это выглядит в обычной неделе — "
        "без схемы, банок и обещания «перезапустить организм».\n\n"
        "Это не диагноз, не лечение и не реклама. Перед каналом проверь факты сама."
    )


def _post(title: str, version: int) -> str:
    topic = russian_headline(title)
    openings = (
        "Коротко.",
        "По делу.",
        "Без пафоса.",
    )
    return (
        f"{openings[(version - 1) % 3]} Тема: {topic}\n\n"
        "После 35 телу чаще не хватает не героизма, а спокойного ритма: "
        "сон, еда без войны с собой и немного движения в обычный день.\n\n"
        "Что это значит. Можно выбрать один маленький шаг, который реально влезает "
        "в рабочую неделю — без обещания «перезапустить организм за три дня».\n\n"
        "Маленький шаг. Завтра отметьте один факт: во сколько легли, что съели "
        "без телефона или сколько минут гуляли.\n\n"
        "Важно: это не медицинская рекомендация и не диагноз. Перед публикацией "
        "проверь факты сама."
    )


def _stories(title: str, version: int) -> str:
    topic = russian_headline(title)
    return (
        f"Сценарий сторис · {topic} · вариант {version}\n\n"
        "Кадр 1. Крупный текст: «После 35 это не лень». Фон спокойный, без клиник и банок.\n"
        f"Кадр 2. Одна мысль: {topic.lower()} — не подвиг, а привычка, которая влезает в будни.\n"
        "Кадр 3. Маленький шаг на завтра. Одна кнопка действия, без «схемы на 21 день».\n"
        "Кадр 4. Оговорка: не диагноз, не реклама таблеток. Ты сама решаешь, публиковать ли."
    )


def _headlines(title: str, version: int) -> str:
    topic = russian_headline(title)
    low = topic.lower()
    return (
        f"Заголовки · {topic} · вариант {version}\n\n"
        f"1. {topic}: не героизм, а ритм\n"
        f"2. После 35 про {low} говорят слишком громко\n"
        f"3. Маленький шаг вместо большой перезагрузки\n"
        f"4. Если {low} снова звучит как упрёк\n"
        f"5. Спокойный пост: {low} без морали"
    )


def _my_story(title: str, version: int) -> str:
    topic = russian_headline(title)
    return (
        f"Моя история · {topic} · вариант {version}\n\n"
        "Я не собираюсь никого лечить. Хочу честно рассказать, как это выглядит "
        f"в обычной неделе, когда речь про {topic.lower()}.\n\n"
        "Было неудобно признать, что «надо просто взять себя в руки» не работает. "
        "Сработало другое: один понятный шаг и меньше стыда.\n\n"
        "Если узнаёшь себя — ок. Если нет, можно пройти мимо. Это не инструкция "
        "и не реклама."
    )


__all__ = ["StudioKind", "StudioWriter"]
