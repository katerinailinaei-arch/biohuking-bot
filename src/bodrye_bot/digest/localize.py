from __future__ import annotations

import re
from dataclasses import replace

from bodrye_bot.digest.service import Digest, DigestCard
from bodrye_bot.domain.headlines import russian_headline, russian_summary
from bodrye_bot.ports.localization import LocalizedCopy, TopicLocalizer

_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")


class CardLocalizer:
    """Rewrite digest cards into owner-facing Russian before Telegram send."""

    def __init__(self, translator: TopicLocalizer | None = None) -> None:
        self._translator = translator

    async def localize(self, digest: Digest) -> Digest:
        if not digest.cards:
            return digest
        copies: tuple[LocalizedCopy | None, ...] = (None,) * len(digest.cards)
        needs_model = any(
            _LATIN_WORD.search(f"{card.title} {card.summary}") is not None
            for card in digest.cards
        )
        if needs_model and self._translator is not None:
            try:
                copies = await self._translator.localize_batch(
                    tuple((card.title, card.summary) for card in digest.cards)
                )
            except Exception:
                copies = (None,) * len(digest.cards)
            if len(copies) != len(digest.cards):
                copies = (None,) * len(digest.cards)
        cards = tuple(
            _apply(card, copy) for card, copy in zip(digest.cards, copies, strict=True)
        )
        return replace(digest, cards=cards)


def _apply(card: DigestCard, copy: LocalizedCopy | None) -> DigestCard:
    headline = russian_headline(card.title, card.rubric)
    summary = russian_summary(card.title, card.rubric, card.summary)
    if copy is not None:
        if _owner_ok(copy.headline):
            headline = copy.headline.strip()
        if _owner_ok(copy.summary):
            summary = copy.summary.strip()
    return replace(card, title=headline, summary=summary)


def _owner_ok(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < 8:
        return False
    letters = [char for char in cleaned if char.isalpha()]
    if not letters:
        return False
    cyrillic = sum(
        1 for char in letters if "а" <= char.lower() <= "я" or char.lower() == "ё"
    )
    if cyrillic / len(letters) < 0.85:
        return False
    return _LATIN_WORD.search(cleaned) is None


__all__ = ["CardLocalizer"]
