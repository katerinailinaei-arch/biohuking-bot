from __future__ import annotations

# ruff: noqa: E501
from html import escape
from urllib.parse import quote, urlsplit

from bodrye_bot.digest.service import Digest, DigestCard
from bodrye_bot.domain.headlines import (
    russian_headline,
    russian_rubric,
    russian_source_name,
    russian_summary,
)


def digest_cover_url(title: str) -> str:
    prompt = (
        "soft editorial still life, morning light, no people, no text, "
        "no pills, no hospital, wellness atmosphere, "
        + title.replace("\n", " ")[:70]
    )
    return (
        "https://image.pollinations.ai/prompt/"
        + quote(prompt, safe="")
        + "?nologo=true&width=1024&height=1024"
    )


def first_source_url(card: DigestCard) -> str | None:
    return next((url for url in card.provenance_urls if _owner_safe_url(url)), None)


def render_digest(digest: Digest) -> str:
    """Render a strictly escaped owner-facing Telegram HTML digest."""
    if not digest.cards:
        body = "<b>Утренний дайджест</b>\nСегодня сильных тем не найдено."
    else:
        body = "<b>Утренний дайджест</b>\n\n" + "\n\n".join(
            render_digest_card(card) for card in digest.cards
        )
    return body + _failures_html(digest)


def render_digest_intro(digest: Digest) -> str:
    if not digest.cards:
        return render_digest(digest)
    return (
        "<b>Утренний дайджест</b>\n"
        "Короткие карточки ниже. Это идеи, не публикация.\n"
        "«Развить» — короткий черновик и обложка. «Сохранить» — отложить тему. "
        "«Не интересно» — пропустить. «Источник» — открыть ссылку."
        + _failures_html(digest)
    )


def render_digest_card(card: DigestCard) -> str:
    title = russian_headline(card.title, card.rubric)
    summary = russian_summary(card.title, card.rubric, card.summary)
    if len(summary) > 280:
        clipped = summary[:277].rsplit(" ", 1)[0]
        summary = f"{clipped}…"
    return (
        f"<b>{escape(title)}</b>\n"
        f"{escape(summary)}\n"
        f"{escape(russian_rubric(card.rubric))} · {card.published_at.strftime('%d.%m.%Y')}"
    )


def _failures_html(digest: Digest) -> str:
    if not digest.source_failures:
        return ""
    return "\n\n<b>Источники</b>\n" + "\n".join(
        f"{escape(russian_source_name(failure.source_name))}: {_failure_status(failure.safe_code)}"
        for failure in digest.source_failures
    )


def _owner_safe_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


def _failure_status(code: str) -> str:
    return {
        "unavailable": "временно недоступен",
        "source_unavailable": "временно недоступен",
        "blocked": "недоступен по правилам безопасности",
    }.get(code, "временно недоступен")


__all__ = [
    "digest_cover_url",
    "first_source_url",
    "render_digest",
    "render_digest_card",
    "render_digest_intro",
]
