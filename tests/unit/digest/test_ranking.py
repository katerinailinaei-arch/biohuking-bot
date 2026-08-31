from __future__ import annotations

from datetime import date

import pytest

from bodrye_bot.digest.service import DigestCandidate, DigestService, DigestWeightConfig
from bodrye_bot.domain.sources import SourceRole


def _candidate(*, url: str, **scores: float) -> DigestCandidate:
    return DigestCandidate(
        canonical_url=url,
        content_hash=(url[-1] * 64),
        topic_fingerprint=url,
        title="Полезная тема",
        summary="Первое предложение. Второе предложение.",
        rubric="Движение",
        published_at=date(2026, 9, 1),
        audience_reason="Практично для аудитории 35–50.",
        source_roles=(SourceRole.TOPIC,),
        relevance=scores["relevance"],
        freshness=scores["freshness"],
        source_authority=scores["source_authority"],
        audience_fit=scores["audience_fit"],
        novelty=scores["novelty"],
        preliminary_risk=scores["preliminary_risk"],
    )


def test_digest_uses_literal_versioned_weights_and_never_pads_with_weak_cards() -> None:
    """Break caught: an opaque score changes threshold selection or fills five slots."""
    weights = DigestWeightConfig(
        version="digest-scoring-test-v1",
        weights={
            "relevance": 0.25,
            "freshness": 0.15,
            "source_authority": 0.20,
            "audience_fit": 0.15,
            "novelty": 0.15,
            "preliminary_risk": 0.10,
        },
    )
    strong = _candidate(
        url="https://example.org/strong-a",
        relevance=1.0,
        freshness=0.8,
        source_authority=0.9,
        audience_fit=0.8,
        novelty=0.7,
        preliminary_risk=1.0,
    )
    threshold = _candidate(
        url="https://example.org/threshold-b",
        relevance=0.8,
        freshness=0.6,
        source_authority=0.8,
        audience_fit=0.6,
        novelty=0.6,
        preliminary_risk=0.8,
    )
    weak = _candidate(
        url="https://example.org/weak-c",
        relevance=0.4,
        freshness=0.4,
        source_authority=0.4,
        audience_fit=0.4,
        novelty=0.4,
        preliminary_risk=0.4,
    )

    digest = DigestService(weights=weights).build(
        (weak, threshold, strong), digest_date=date(2026, 9, 1)
    )

    assert [card.score for card in digest.cards] == [0.88, 0.71]
    assert [card.score_version for card in digest.cards] == [
        "digest-scoring-test-v1",
        "digest-scoring-test-v1",
    ]
    assert digest.cards[1].selection_reason == "Оценка 0.71 не ниже порога 0.70."
    assert digest.cards[0].actions == ("Развить", "Сохранить", "Не интересно", "Источник")


def test_digest_allows_zero_cards_when_every_candidate_is_below_threshold() -> None:
    """Break caught: a quiet source day fabricates a weak digest card."""
    candidate = _candidate(
        url="https://example.org/weak-a",
        relevance=0.69,
        freshness=0.69,
        source_authority=0.69,
        audience_fit=0.69,
        novelty=0.69,
        preliminary_risk=0.69,
    )

    digest = DigestService().build((candidate,), digest_date=date(2026, 9, 1))

    assert digest.cards == ()


def test_weight_configuration_is_immutable_and_must_sum_to_one() -> None:
    """Break caught: pilot tuning mutates or silently invalidates a scoring version."""
    config = DigestWeightConfig()

    with pytest.raises(TypeError):
        config.weights["relevance"] = 0.1  # type: ignore[index]
    with pytest.raises(ValueError, match="sum"):
        DigestWeightConfig(weights={name: 0.1 for name in config.weights})
