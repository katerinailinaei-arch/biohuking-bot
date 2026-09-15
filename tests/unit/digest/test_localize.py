from __future__ import annotations

from datetime import date

import pytest

from bodrye_bot.digest.localize import CardLocalizer
from bodrye_bot.digest.service import Digest, DigestCard, PreliminaryRisk
from bodrye_bot.domain.sources import SourceRole
from bodrye_bot.ports.localization import LocalizedCopy

_TITLE = (
    "Hepatic cytochrome P450 induction following Kashin-Beck "
    "disease-related selenium deficiency and T-2 toxin exposure in mice."
)


def _card() -> DigestCard:
    return DigestCard(
        title=_TITLE,
        topic_fingerprint="selenium",
        summary=f"{_TITLE} Тема для канала, не диагноз.",
        rubric="PubMed RSS: питание и метаболическое здоровье",
        published_at=date(2026, 1, 1),
        audience_reason="x",
        provenance_urls=("https://pubmed.ncbi.nlm.nih.gov/1/",),
        source_roles=(SourceRole.TOPIC,),
        preliminary_risk=PreliminaryRisk.GREEN,
        score=0.9,
        raw_score=0.9,
        score_components={},
        scoring_snapshot={},
        score_version="test-v1",
        selection_reason="выбрано",
    )


class _Translator:
    def __init__(self, copy: LocalizedCopy | None) -> None:
        self.copy = copy
        self.seen: list[tuple[str, str]] = []

    async def localize_batch(
        self, items: tuple[tuple[str, str], ...]
    ) -> tuple[LocalizedCopy | None, ...]:
        self.seen.extend(items)
        return tuple(self.copy for _ in items)


@pytest.mark.asyncio
async def test_localizer_uses_russian_model_copy() -> None:
    translator = _Translator(
        LocalizedCopy(
            headline="Нехватка селена и токсин: как меняется печень у мышей",
            summary=(
                "На мышах смотрели, как нехватка селена и токсин влияют на печень. "
                "Это идея для канала, не совет людям и не реклама добавок."
            ),
        )
    )
    digest = await CardLocalizer(translator).localize(
        Digest(digest_date=date(2026, 1, 1), cards=(_card(),))
    )

    assert digest.cards[0].title.startswith("Нехватка селена")
    assert "мышах" in digest.cards[0].summary
    assert "Hepatic" not in digest.cards[0].title
    assert translator.seen[0][0] == _TITLE


@pytest.mark.asyncio
async def test_localizer_rejects_english_model_copy() -> None:
    translator = _Translator(LocalizedCopy(headline=_TITLE, summary="Still English abstract here."))
    digest = await CardLocalizer(translator).localize(
        Digest(digest_date=date(2026, 1, 1), cards=(_card(),))
    )

    assert "Hepatic" not in digest.cards[0].title
    assert "cytochrome" not in digest.cards[0].summary


@pytest.mark.asyncio
async def test_localizer_falls_back_when_translator_fails() -> None:
    class Boom:
        async def localize_batch(self, items: tuple[tuple[str, str], ...]):
            del items
            raise RuntimeError("groq down")

    digest = await CardLocalizer(Boom()).localize(
        Digest(digest_date=date(2026, 1, 1), cards=(_card(),))
    )

    assert "Hepatic" not in digest.cards[0].title
    assert "cytochrome" not in digest.cards[0].summary
