from __future__ import annotations

# ruff: noqa: E501
from datetime import date

import pytest

from bodrye_bot.digest.service import (
    DigestCandidate,
    DigestService,
    PreliminaryRisk,
    ScoringSnapshot,
)
from bodrye_bot.domain.sources import SourceRole


def _candidate(**changes: object) -> DigestCandidate:
    values: dict[str, object] = {
        "canonical_url": "https://example.org/a?z=1&a=2",
        "content_hash": "a" * 64,
        "topic_fingerprint": "Качественный сон",
        "title": "Сон",
        "summary": "Первое предложение. Второе предложение.",
        "rubric": "Сон",
        "published_at": date(2026, 9, 1),
        "audience_reason": "Важно после 35.",
        "source_roles": (SourceRole.TOPIC,),
        "relevance": 0.9,
        "freshness": 0.9,
        "source_authority": 0.9,
        "audience_fit": 0.9,
        "novelty": 0.9,
        "preliminary_risk": PreliminaryRisk.GREEN,
    }
    values.update(changes)
    return DigestCandidate(**values)  # type: ignore[arg-type]


def test_raw_score_below_threshold_is_not_selected_even_when_display_rounds_up() -> None:
    candidate = _candidate(
        relevance=0.6622222222,
        freshness=0.6622222222,
        source_authority=0.6622222222,
        audience_fit=0.6622222222,
        novelty=0.6622222222,
        preliminary_risk=PreliminaryRisk.GREEN,
    )

    assert DigestService().build((candidate,), digest_date=date(2026, 9, 1)).cards == ()


def test_red_risk_cannot_improve_ranking_and_unknown_risk_is_rejected() -> None:
    green = _candidate(
        canonical_url="https://example.org/green",
        topic_fingerprint="green",
        preliminary_risk=PreliminaryRisk.GREEN,
    )
    red = _candidate(
        canonical_url="https://example.org/red",
        content_hash="b" * 64,
        topic_fingerprint="red",
        preliminary_risk=PreliminaryRisk.RED,
    )

    cards = DigestService().build((red, green), digest_date=date(2026, 9, 1)).cards

    assert cards[0].title == "Сон"
    assert cards[0].provenance_urls == ("https://example.org/green",)
    with pytest.raises(ValueError, match="risk"):
        _candidate(preliminary_risk="unknown")


def test_scoring_snapshot_identity_binds_weights_threshold_and_aggregation() -> None:
    first = ScoringSnapshot.default()
    changed = ScoringSnapshot(
        version="digest-scoring-v1",
        weights=first.weights,
        min_score=0.71,
        maximum_cards=5,
        aggregation="component_max_v1",
    )

    assert first.id != changed.id
    assert first.as_dict()["weights"] == dict(first.weights)


@pytest.mark.parametrize(
    "changes",
    (
        {"title": ""},
        {"rubric": ""},
        {"audience_reason": ""},
        {"summary": "Only one sentence."},
        {"summary": "One. Two. Three. Four."},
    ),
)
def test_candidate_rejects_missing_required_fields_or_not_two_to_three_sentences(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _candidate(**changes)


def test_representative_and_date_are_deterministic_for_permutations() -> None:
    first = _candidate(canonical_url="https://example.org/b", published_at=date(2026, 9, 1))
    second = _candidate(canonical_url="https://example.org/a", published_at=date(2026, 9, 2))

    one = DigestService().build((first, second), digest_date=date(2026, 9, 2)).cards
    two = DigestService().build((second, first), digest_date=date(2026, 9, 2)).cards

    assert one == two
    assert one[0].provenance_urls == ("https://example.org/a", "https://example.org/b")


def test_candidate_repr_redacts_source_content_and_url() -> None:
    candidate = _candidate(
        canonical_url="https://secret.example.org/token", summary="Secret one. Secret two."
    )

    assert "secret.example" not in repr(candidate)
    assert "Secret one" not in repr(candidate)


def test_service_rejects_out_of_policy_selection_limits() -> None:
    with pytest.raises(ValueError, match="threshold"):
        DigestService(min_score=0.69)
    with pytest.raises(ValueError, match="maximum"):
        DigestService(maximum_cards=6)
