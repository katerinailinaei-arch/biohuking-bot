from __future__ import annotations

from datetime import date

from bodrye_bot.digest.service import DigestCandidate, DigestService, PreliminaryRisk
from bodrye_bot.domain.sources import SourceRole


def _candidate(
    *,
    url: str,
    content_hash: str,
    topic: str,
    role: SourceRole,
) -> DigestCandidate:
    return DigestCandidate(
        canonical_url=url,
        content_hash=content_hash,
        topic_fingerprint=topic,
        title="Сон и восстановление",
        summary="Короткое изложение источника. Второе предложение для карточки.",
        rubric="Сон",
        published_at=date(2026, 9, 1),
        audience_reason="Помогает заметить полезное изменение привычек после 35 лет.",
        source_roles=(role,),
        relevance=0.9,
        freshness=0.8,
        source_authority=0.9,
        audience_fit=0.9,
        novelty=0.8,
        preliminary_risk=PreliminaryRisk.GREEN,
    )


def test_digest_merges_transitive_topic_duplicates_and_all_provenance() -> None:
    """Break caught: adjacent-only dedupe loses the third matching source."""
    service = DigestService()
    first = _candidate(
        url="HTTPS://Example.org/sleep#summary",
        content_hash="a" * 64,
        topic="Sleep recovery",
        role=SourceRole.TOPIC,
    )
    second = _candidate(
        url="https://example.org/another-sleep-item",
        content_hash="a" * 64,
        topic="other topic",
        role=SourceRole.EVIDENCE,
    )
    third = _candidate(
        url="https://example.org/final-sleep-item",
        content_hash="c" * 64,
        topic="OTHER TOPIC",
        role=SourceRole.FORMAT,
    )

    digest = service.build((first, second, third), digest_date=date(2026, 9, 1))

    assert len(digest.cards) == 1
    assert digest.cards[0].provenance_urls == (
        "https://example.org/another-sleep-item",
        "https://example.org/final-sleep-item",
        "https://example.org/sleep",
    )
    assert digest.cards[0].source_roles == (
        SourceRole.EVIDENCE,
        SourceRole.FORMAT,
        SourceRole.TOPIC,
    )


def test_digest_deduplicates_normalized_url_before_nonmatching_hash() -> None:
    """Break caught: URL variants create two cards for one canonical source."""
    service = DigestService()
    first = _candidate(
        url="https://EXAMPLE.org/article/#fragment",
        content_hash="a" * 64,
        topic="one",
        role=SourceRole.TOPIC,
    )
    second = _candidate(
        url="https://example.org/article",
        content_hash="b" * 64,
        topic="two",
        role=SourceRole.EVIDENCE,
    )

    digest = service.build((first, second), digest_date=date(2026, 9, 1))

    assert len(digest.cards) == 1
    assert digest.cards[0].provenance_urls == ("https://example.org/article",)
