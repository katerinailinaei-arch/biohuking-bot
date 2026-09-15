from __future__ import annotations

import re
from datetime import date

from bodrye_bot.digest.service import DigestCard, PreliminaryRisk
from bodrye_bot.digest.views import render_digest_card
from bodrye_bot.domain.headlines import russian_headline, russian_summary
from bodrye_bot.domain.sources import SourceRole


def _latin_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]{3,}", text)


def test_english_pubmed_title_is_fully_russian() -> None:
    title = russian_headline(
        "Effects of sleep restriction on circadian rhythm in older adults",
        "PubMed RSS: сон и восстановление",
    )
    summary = russian_summary(
        "Effects of sleep restriction on circadian rhythm in older adults",
        "PubMed RSS: сон и восстановление",
        "Sleep loss impairs recovery and mood in midlife.",
    )

    assert _latin_words(title) == []
    assert _latin_words(summary) == []
    assert "сон" in title.lower()


def test_digest_card_hides_english_source_title() -> None:
    card = DigestCard(
        title="Physical activity and glucose control in middle-aged adults",
        topic_fingerprint="activity",
        summary="Exercise improved insulin sensitivity without a drug trial.",
        rubric="PubMed RSS: движение и активное долголетие",
        published_at=date(2026, 9, 1),
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

    visible = re.sub(r"<[^>]+>", " ", render_digest_card(card))

    assert _latin_words(visible) == []
    assert "движен" in visible.lower() or "активн" in visible.lower()


def test_scientific_pubmed_title_is_not_shown_in_english() -> None:
    title = (
        "Hepatic cytochrome P450 induction following Kashin-Beck "
        "disease-related selenium deficiency and T-2 toxin exposure in mice."
    )
    card = DigestCard(
        title=title,
        topic_fingerprint="selenium",
        summary="Исследование: " + title + " Кети решает, развивать ли тему в канале.",
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

    visible = re.sub(r"<[^>]+>", " ", render_digest_card(card))
    html = render_digest_card(card)

    assert _latin_words(visible) == []
    assert "Hepatic" not in visible
    assert "cytochrome" not in visible
    assert "PubMed RSS" not in visible
    assert '<a href="https://pubmed.ncbi.nlm.nih.gov/1/">Открыть источник</a>' in html
