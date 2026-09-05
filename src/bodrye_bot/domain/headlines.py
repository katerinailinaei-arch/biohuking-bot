from __future__ import annotations

import re

_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
_TOKEN = re.compile(r"[a-zа-яё]+|\d+", re.IGNORECASE)
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("mattress", "bedding", "pillow", "duvet", "постель"), "Постель и тепло для сна"),
    (("insomnia", "circadian", "sleep", "сон", "бессонниц"), "Сон и восстановление"),
    (("menopause", "hormone", "гормон", "менопауз"), "Гормоны и самочувствие после 35"),
    (("diet", "nutrition", "calorie", "obesity", "питание", "еда"), "Еда без войны с собой"),
    (
        ("exercise", "activity", "physical", "training", "athlete", "football", "движен"),
        "Движение в обычный день",
    ),
    (("stress", "anxiety", "mood", "стресс"), "Стресс и спокойный ритм"),
    (("heart", "cardio", "blood pressure", "сердц", "давлен"), "Сердце и привычки"),
    (("glucose", "diabetes", "insulin", "сахар"), "Сахар и ритм еды"),
)
_PHRASES: tuple[tuple[str, str], ...] = (
    ("systematic review", "систематический обзор"),
    ("meta analysis", "обзор исследований"),
    ("meta-analysis", "обзор исследований"),
    ("randomized controlled", "сравнение в исследовании"),
    ("physical activity", "движение"),
    ("healthy aging", "активное долголетие"),
    ("healthy ageing", "активное долголетие"),
    ("older adults", "после 35 лет"),
    ("middle aged", "после 35 лет"),
    ("middle-aged", "после 35 лет"),
    ("blood pressure", "давление"),
    ("heart rate", "пульс"),
    ("body weight", "вес"),
    ("weight loss", "снижение веса"),
    ("mental health", "настроение и нервы"),
    ("quality of life", "самочувствие в быту"),
    ("shift work", "сменный график"),
    ("screen time", "экран вечером"),
    ("effects of", "влияние"),
    ("effect of", "влияние"),
    ("impact of", "влияние"),
    ("association of", "связь"),
    ("association between", "связь"),
)
_WORDS: dict[str, str] = {
    "sleep": "сон",
    "insomnia": "бессонница",
    "circadian": "режим дня",
    "recovery": "восстановление",
    "rest": "отдых",
    "night": "ночь",
    "morning": "утро",
    "diet": "питание",
    "nutrition": "питание",
    "calorie": "калории",
    "calories": "калории",
    "obesity": "лишний вес",
    "overweight": "лишний вес",
    "glucose": "сахар",
    "diabetes": "сахар в крови",
    "insulin": "инсулин",
    "metabolic": "обмен веществ",
    "metabolism": "обмен веществ",
    "exercise": "движение",
    "training": "тренировки",
    "walking": "ходьба",
    "sedentary": "сидячий день",
    "sitting": "сидение",
    "activity": "активность",
    "physical": "физическая",
    "fitness": "форма",
    "muscle": "мышцы",
    "strength": "сила",
    "bone": "кости",
    "joint": "суставы",
    "pain": "боль",
    "fatigue": "усталость",
    "stress": "стресс",
    "anxiety": "тревога",
    "depression": "плохое настроение",
    "mood": "настроение",
    "heart": "сердце",
    "cardiac": "сердце",
    "cardiovascular": "сердце и сосуды",
    "hypertension": "давление",
    "pressure": "давление",
    "hormone": "гормоны",
    "hormones": "гормоны",
    "menopause": "менопауза",
    "aging": "возраст",
    "ageing": "возраст",
    "midlife": "после 35 лет",
    "adults": "взрослые",
    "women": "женщины",
    "men": "мужчины",
    "patients": "люди",
    "health": "здоровье",
    "healthy": "здоровый",
    "risk": "риск",
    "benefits": "польза",
    "benefit": "польза",
    "improvement": "улучшение",
    "improved": "улучшает",
    "impairs": "мешает",
    "impair": "мешает",
    "loss": "нехватка",
    "restriction": "ограничение",
    "duration": "длительность",
    "quality": "качество",
    "trial": "исследование",
    "study": "исследование",
    "review": "обзор",
    "analysis": "разбор",
    "intervention": "привычка",
    "lifestyle": "образ жизни",
    "work": "работа",
    "workplace": "работа",
    "coffee": "кофе",
    "alcohol": "алкоголь",
    "vitamin": "витамин",
    "supplement": "добавка",
    "supplements": "добавки",
    "drug": "лекарство",
    "drugs": "лекарства",
    "medication": "лекарство",
    "without": "без",
    "control": "контроль",
    "sensitivity": "чувствительность",
}


def russian_headline(title: str, rubric: str = "") -> str:
    cleaned = title.strip()
    if cleaned and _mostly_cyrillic(cleaned) and not _LATIN_WORD.search(cleaned):
        return _short_line(cleaned)
    translated = _translate_line(cleaned)
    if _usable_russian(translated):
        return _short_line(_cap(translated))
    blob = f"{title} {rubric}".lower()
    for keys, label in _RULES:
        if any(key in blob for key in keys):
            return label
    rubric_ru = russian_rubric(rubric)
    if rubric_ru != "Тема дня":
        return rubric_ru
    return "Тема из исследования"


def russian_rubric(rubric: str) -> str:
    text = rubric.strip()
    named = _SOURCE_NAMES.get(text.lower())
    if named is not None:
        return named
    for prefix in ("PubMed RSS:", "PubMed:", "RSS:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()
    if text and _mostly_cyrillic(text) and not _LATIN_WORD.search(text):
        return _short_line(text)
    translated = _translate_line(text)
    if _usable_russian(translated):
        return _short_line(_cap(translated))
    return "Тема дня"


def russian_summary(title: str, rubric: str, summary: str) -> str:
    if summary.strip():
        if not _LATIN_WORD.search(summary) and _mostly_cyrillic(summary):
            return summary.strip()
        translated = _translate_line(summary)
        if _usable_russian(translated):
            return _cap(translated) + "."
    topic = russian_headline(title, rubric)
    return (
        f"О чём речь: {topic}. "
        "Это идея для канала, не диагноз и не реклама лекарства."
    )


def russian_source_name(name: str) -> str:
    return russian_rubric(name)


_SOURCE_NAMES = {
    "who": "ВОЗ",
    "who fact sheets": "справки ВОЗ",
    "who news": "новости ВОЗ",
    "uspstf": "профилактические рекомендации",
    "nice": "британские рекомендации",
    "cochrane": "обзоры исследований",
    "cochrane reviews": "обзоры исследований",
}


def _translate_line(text: str) -> str:
    work = f" {text.lower()} "
    work = work.replace("—", " ").replace("–", " ").replace("-", " ")
    for src, dst in sorted(_PHRASES, key=lambda item: len(item[0]), reverse=True):
        work = work.replace(src, f" {dst} ")
    out: list[str] = []
    for token in _TOKEN.findall(work):
        if token.isdigit():
            out.append(token)
            continue
        low = token.lower()
        if _cyrillic_word(low):
            if not out or out[-1] != low:
                out.append(low)
            continue
        mapped = _WORDS.get(low)
        if mapped and (not out or out[-1] != mapped):
            out.append(mapped)
    return " ".join(out).strip()


def _usable_russian(text: str) -> bool:
    if not text or _LATIN_WORD.search(text):
        return False
    return len(text.split()) >= 2


def _cyrillic_word(word: str) -> bool:
    letters = [char for char in word if char.isalpha()]
    return bool(letters) and all(
        "а" <= char <= "я" or char == "ё" for char in letters
    )


def _mostly_cyrillic(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    cyrillic = sum(1 for char in letters if "а" <= char.lower() <= "я" or char.lower() == "ё")
    return cyrillic / len(letters) >= 0.6


def _cap(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _short_line(text: str) -> str:
    line = re.sub(r"\s+", " ", text).strip()
    if len(line) <= 90:
        return line
    return f"{line[:87].rsplit(' ', 1)[0]}…"


__all__ = [
    "russian_headline",
    "russian_rubric",
    "russian_source_name",
    "russian_summary",
]
