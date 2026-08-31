from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from bodrye_bot.domain.sources import SourceRole

_SCORE_DIMENSIONS = (
    "relevance",
    "freshness",
    "source_authority",
    "audience_fit",
    "novelty",
    "preliminary_risk",
)
_DEFAULT_WEIGHTS = {
    "relevance": 0.25,
    "freshness": 0.15,
    "source_authority": 0.20,
    "audience_fit": 0.15,
    "novelty": 0.15,
    "preliminary_risk": 0.10,
}
_ACTIONS = ("Развить", "Сохранить", "Не интересно", "Источник")


@dataclass(frozen=True)
class DigestWeightConfig:
    """Inspectable immutable scoring weights; changing them needs a new version."""

    version: str = "digest-scoring-v1"
    weights: Mapping[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Digest weight version is required")
        frozen = MappingProxyType(dict(self.weights))
        if tuple(sorted(frozen)) != tuple(sorted(_SCORE_DIMENSIONS)):
            raise ValueError("Digest weights must contain every score dimension exactly once")
        if any(not 0 <= weight <= 1 for weight in frozen.values()):
            raise ValueError("Digest weights must be between zero and one")
        if abs(sum(frozen.values()) - 1.0) > 1e-9:
            raise ValueError("Digest weights must sum to one")
        object.__setattr__(self, "weights", frozen)


@dataclass(frozen=True)
class DigestCandidate:
    """A fetched, owner-scoped candidate with already-extracted safe metadata."""

    canonical_url: str
    content_hash: str | None
    topic_fingerprint: str
    title: str
    summary: str
    rubric: str
    published_at: date | datetime
    audience_reason: str
    source_roles: tuple[SourceRole, ...]
    relevance: float
    freshness: float
    source_authority: float
    audience_fit: float
    novelty: float
    preliminary_risk: float
    preliminary_risk_label: str = "green"

    def __post_init__(self) -> None:
        if not self.canonical_url or not self.topic_fingerprint:
            raise ValueError("Digest candidates need canonical URL and topic fingerprint")
        if not self.source_roles:
            raise ValueError("Digest candidates need at least one source role")
        if any(not 0 <= score <= 1 for score in self.score_values().values()):
            raise ValueError("Digest score components must be between zero and one")

    def score_values(self) -> dict[str, float]:
        return {dimension: float(getattr(self, dimension)) for dimension in _SCORE_DIMENSIONS}


@dataclass(frozen=True)
class SourceFailure:
    """Safe source status: the view deliberately does not expose exception details."""

    source_name: str
    safe_code: str


@dataclass(frozen=True)
class DigestCard:
    title: str
    topic_fingerprint: str
    summary: str
    rubric: str
    published_at: date | datetime
    audience_reason: str
    provenance_urls: tuple[str, ...]
    source_roles: tuple[SourceRole, ...]
    preliminary_risk: str
    score: float
    score_components: Mapping[str, float]
    score_version: str
    selection_reason: str
    actions: tuple[str, str, str, str] = _ACTIONS


@dataclass(frozen=True)
class Digest:
    digest_date: date
    cards: tuple[DigestCard, ...]
    source_failures: tuple[SourceFailure, ...] = ()

    @property
    def items(self) -> tuple[DigestCard, ...]:
        """Compatibility name used by the implementation plan examples."""
        return self.cards


class DigestService:
    def __init__(
        self,
        *,
        weights: DigestWeightConfig | None = None,
        min_score: float = 0.70,
        maximum_cards: int = 5,
    ) -> None:
        if not 0 <= min_score <= 1:
            raise ValueError("Digest threshold must be between zero and one")
        if maximum_cards < 1:
            raise ValueError("Digest maximum cards must be positive")
        self._weights = weights or DigestWeightConfig()
        self._min_score = min_score
        self._maximum_cards = maximum_cards

    def build(
        self,
        candidates: tuple[DigestCandidate, ...] | list[DigestCandidate],
        *,
        digest_date: date,
        source_failures: tuple[SourceFailure, ...] | list[SourceFailure] = (),
    ) -> Digest:
        cards = tuple(
            card
            for card in sorted(
                (self._card(group) for group in _transitive_groups(tuple(candidates))),
                key=lambda card: (-card.score, card.topic_fingerprint, card.provenance_urls),
            )
            if card.score >= self._min_score
        )[: self._maximum_cards]
        return Digest(
            digest_date=digest_date,
            cards=cards,
            source_failures=tuple(source_failures),
        )

    def _card(self, candidates: tuple[DigestCandidate, ...]) -> DigestCard:
        values = {
            dimension: max(candidate.score_values()[dimension] for candidate in candidates)
            for dimension in _SCORE_DIMENSIONS
        }
        score = round(
            sum(self._weights.weights[name] * values[name] for name in _SCORE_DIMENSIONS), 2
        )
        representative = min(
            candidates,
            key=lambda candidate: (
                -self._score(candidate.score_values()),
                _normalized_url(candidate.canonical_url),
            ),
        )
        urls = tuple(sorted({_normalized_url(candidate.canonical_url) for candidate in candidates}))
        roles = tuple(
            sorted(
                {role for candidate in candidates for role in candidate.source_roles}, key=str
            )
        )
        risk = _highest_risk(candidate.preliminary_risk_label for candidate in candidates)
        return DigestCard(
            title=representative.title,
            topic_fingerprint=representative.topic_fingerprint,
            summary=representative.summary,
            rubric=representative.rubric,
            published_at=max(candidate.published_at for candidate in candidates),
            audience_reason=representative.audience_reason,
            provenance_urls=urls,
            source_roles=roles,
            preliminary_risk=risk,
            score=score,
            score_components=MappingProxyType(values),
            score_version=self._weights.version,
            selection_reason=(
                f"Оценка {score:.2f} не ниже порога {self._min_score:.2f}."
            ),
        )

    def _score(self, values: Mapping[str, float]) -> float:
        return sum(self._weights.weights[name] * values[name] for name in _SCORE_DIMENSIONS)


def _transitive_groups(
    candidates: tuple[DigestCandidate, ...],
) -> tuple[tuple[DigestCandidate, ...], ...]:
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    keys: dict[tuple[str, str], int] = {}
    for index, candidate in enumerate(candidates):
        for key in _dedupe_keys(candidate):
            previous = keys.get(key)
            if previous is None:
                keys[key] = index
            else:
                union(index, previous)

    groups: dict[int, list[DigestCandidate]] = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(find(index), []).append(candidate)
    return tuple(tuple(group) for _, group in sorted(groups.items()))


def _dedupe_keys(candidate: DigestCandidate) -> tuple[tuple[str, str], ...]:
    keys: list[tuple[str, str]] = [("url", _normalized_url(candidate.canonical_url))]
    if _is_complete_hash(candidate.content_hash):
        assert candidate.content_hash is not None
        keys.append(("hash", candidate.content_hash.lower()))
    topic = _normalized_topic(candidate.topic_fingerprint)
    if topic:
        keys.append(("topic", topic))
    return tuple(keys)


def _normalized_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return url.split("#", 1)[0].strip()
    port = parsed.port
    netloc = host
    if port is not None and (parsed.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
        netloc = f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _normalized_topic(topic: str) -> str:
    return " ".join("".join(char if char.isalnum() else " " for char in topic.casefold()).split())


def _is_complete_hash(value: str | None) -> bool:
    return (
        value is not None
        and len(value) == 64
        and all(char in "0123456789abcdefABCDEF" for char in value)
    )


def _highest_risk(labels: Iterable[str]) -> str:
    order = {"green": 0, "yellow": 1, "red": 2}
    return max((str(label) for label in labels), key=lambda label: (order.get(label, 1), label))


__all__ = [
    "Digest",
    "DigestCandidate",
    "DigestCard",
    "DigestService",
    "DigestWeightConfig",
    "SourceFailure",
]
