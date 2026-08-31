from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from bodrye_bot.digest.service import Digest, DigestCard


def render_digest(digest: Digest) -> str:
    """Render a strictly escaped owner-facing Telegram HTML digest."""
    if not digest.cards:
        body = "<b>Утренний дайджест</b>\nСегодня сильных тем не найдено."
    else:
        body = "<b>Утренний дайджест</b>\n\n" + "\n\n".join(
            _render_card(card) for card in digest.cards
        )
    if digest.source_failures:
        body += "\n\n<b>Источники</b>\nНекоторые источники сегодня недоступны."
    return body


def _render_card(card: DigestCard) -> str:
    source_links = " ".join(
        f'<a href="{escape(url, quote=True)}">Источник</a>'
        for url in card.provenance_urls
        if _owner_safe_url(url)
    )
    details = (
        f"<b>{escape(card.title)}</b>\n"
        f"{escape(card.summary)}\n"
        f"Рубрика: {escape(card.rubric)}\n"
        f"Дата: {card.published_at.strftime('%d.%m.%Y')}\n"
        f"Почему важно: {escape(card.audience_reason)}\n"
        f"Риск: {escape(card.preliminary_risk)}. {escape(card.selection_reason)}\n"
        f"Действия: {', '.join(escape(action) for action in card.actions)}"
    )
    return f"{details}\n{source_links}" if source_links else details


def _owner_safe_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


__all__ = ["render_digest"]
