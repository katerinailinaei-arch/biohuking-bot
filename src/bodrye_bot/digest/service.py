from __future__ import annotations

# ruff: noqa: E501, E701, E702
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from hashlib import sha256
from json import dumps
from types import MappingProxyType
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bodrye_bot.domain.sources import SourceRole

_DIMENSIONS = (
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


class PreliminaryRisk(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


_SAFETY = {PreliminaryRisk.GREEN: 1.0, PreliminaryRisk.YELLOW: 0.5, PreliminaryRisk.RED: 0.0}


@dataclass(frozen=True)
class ScoringSnapshot:
    version: str = "digest-scoring-v1"
    weights: Mapping[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    min_score: float = 0.70
    maximum_cards: int = 5
    aggregation: str = "component_max_v1"

    def __post_init__(self) -> None:
        frozen = MappingProxyType(dict(self.weights))
        if not self.version.strip() or set(frozen) != set(_DIMENSIONS):
            raise ValueError("invalid scoring snapshot")
        if (
            any(not 0 <= value <= 1 for value in frozen.values())
            or abs(sum(frozen.values()) - 1) > 1e-9
        ):
            raise ValueError("Digest weights must sum to one")
        if not 0.70 <= self.min_score <= 1:
            raise ValueError("Digest threshold must be at least 0.70")
        if not 1 <= self.maximum_cards <= 5:
            raise ValueError("Digest maximum cards must be between 1 and 5")
        if self.aggregation != "component_max_v1":
            raise ValueError("unsupported digest aggregation")
        object.__setattr__(self, "weights", frozen)

    @classmethod
    def default(cls) -> ScoringSnapshot:
        return cls()

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "weights": dict(sorted(self.weights.items())),
            "min_score": self.min_score,
            "maximum_cards": self.maximum_cards,
            "aggregation": self.aggregation,
        }

    @property
    def id(self) -> str:
        canonical = dumps(self.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return f"{self.version}:{sha256(canonical.encode()).hexdigest()[:16]}"


DigestWeightConfig = ScoringSnapshot


@dataclass(frozen=True)
class DigestCandidate:
    canonical_url: str = field(repr=False)
    content_hash: str | None = field(repr=False)
    topic_fingerprint: str
    title: str
    summary: str = field(repr=False)
    rubric: str
    published_at: date | datetime
    audience_reason: str
    source_roles: tuple[SourceRole, ...]
    relevance: float
    freshness: float
    source_authority: float
    audience_fit: float
    novelty: float
    preliminary_risk: PreliminaryRisk

    def __post_init__(self) -> None:
        for value in (
            self.canonical_url,
            self.topic_fingerprint,
            self.title,
            self.rubric,
            self.audience_reason,
        ):
            if not value.strip():
                raise ValueError("Digest candidate required field is empty")
        if not self.source_roles:
            raise ValueError("Digest candidates need source roles")
        try:
            risk = PreliminaryRisk(self.preliminary_risk)
        except ValueError:
            raise ValueError("unknown preliminary risk") from None
        object.__setattr__(self, "preliminary_risk", risk)
        if not 2 <= _sentences(self.summary) <= 3:
            raise ValueError("Digest summary must have 2-3 sentences")
        if any(not 0 <= score <= 1 for score in self.signal_values().values()):
            raise ValueError("Digest score components must be between zero and one")
        if isinstance(self.published_at, datetime):
            object.__setattr__(self, "published_at", self.published_at.date())

    def signal_values(self) -> dict[str, float]:
        return {
            "relevance": self.relevance,
            "freshness": self.freshness,
            "source_authority": self.source_authority,
            "audience_fit": self.audience_fit,
            "novelty": self.novelty,
            "preliminary_risk": _SAFETY[self.preliminary_risk],
        }


@dataclass(frozen=True)
class SourceFailure:
    source_name: str = field(repr=False)
    safe_code: str


@dataclass(frozen=True)
class DigestCard:
    title: str
    topic_fingerprint: str
    summary: str = field(repr=False)
    rubric: str
    published_at: date
    audience_reason: str
    provenance_urls: tuple[str, ...] = field(repr=False)
    source_roles: tuple[SourceRole, ...]
    preliminary_risk: PreliminaryRisk
    score: float
    raw_score: float
    score_components: Mapping[str, float]
    scoring_snapshot: Mapping[str, object]
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
        return self.cards


class DigestService:
    def __init__(
        self,
        *,
        weights: ScoringSnapshot | None = None,
        min_score: float | None = None,
        maximum_cards: int | None = None,
    ) -> None:
        snapshot = weights or ScoringSnapshot.default()
        if min_score is not None or maximum_cards is not None:
            snapshot = ScoringSnapshot(
                version=snapshot.version,
                weights=snapshot.weights,
                min_score=snapshot.min_score if min_score is None else min_score,
                maximum_cards=snapshot.maximum_cards if maximum_cards is None else maximum_cards,
                aggregation=snapshot.aggregation,
            )
        self._snapshot = snapshot

    def build(
        self,
        candidates: tuple[DigestCandidate, ...] | list[DigestCandidate],
        *,
        digest_date: date,
        source_failures: tuple[SourceFailure, ...] | list[SourceFailure] = (),
    ) -> Digest:
        selected = [
            card
            for card in (self._card(group) for group in _groups(tuple(candidates)))
            if card.raw_score >= self._snapshot.min_score
        ]
        return Digest(
            digest_date,
            tuple(
                sorted(
                    selected,
                    key=lambda card: (
                        -card.raw_score,
                        card.topic_fingerprint,
                        card.provenance_urls,
                    ),
                )[: self._snapshot.maximum_cards]
            ),
            tuple(source_failures),
        )

    def _card(self, group: tuple[DigestCandidate, ...]) -> DigestCard:
        values = {name: max(item.signal_values()[name] for item in group) for name in _DIMENSIONS}
        raw = sum(self._snapshot.weights[name] * values[name] for name in _DIMENSIONS)
        rep = min(
            group, key=lambda item: (_url(item.canonical_url), item.title, item.topic_fingerprint)
        )
        return DigestCard(
            rep.title,
            rep.topic_fingerprint,
            rep.summary,
            rep.rubric,
            max(item.published_at for item in group),
            rep.audience_reason,
            tuple(sorted({_url(item.canonical_url) for item in group})),
            tuple(sorted({role for item in group for role in item.source_roles}, key=str)),
            min((item.preliminary_risk for item in group), key=lambda risk: _SAFETY[risk]),
            round(raw, 2),
            raw,
            MappingProxyType(values),
            MappingProxyType(self._snapshot.as_dict()),
            self._snapshot.id,
            f"Оценка {raw:.2f} не ниже порога {self._snapshot.min_score:.2f}.",
        )


def _groups(candidates: tuple[DigestCandidate, ...]) -> tuple[tuple[DigestCandidate, ...], ...]:
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index], index = parent[parent[index]], parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[max(left, right)] = min(left, right)

    seen: dict[tuple[str, str], int] = {}
    for index, item in enumerate(candidates):
        for key in _keys(item):
            if key in seen:
                union(index, seen[key])
            else:
                seen[key] = index
    grouped: dict[int, list[DigestCandidate]] = {}
    for index, item in enumerate(candidates):
        grouped.setdefault(find(index), []).append(item)
    return tuple(tuple(items) for _, items in sorted(grouped.items()))


def _keys(item: DigestCandidate) -> tuple[tuple[str, str], ...]:
    result = [("url", _url(item.canonical_url))]
    if (
        item.content_hash
        and len(item.content_hash) == 64
        and all(char in "0123456789abcdefABCDEF" for char in item.content_hash)
    ):
        result.append(("hash", item.content_hash.lower()))
    topic = " ".join(
        "".join(
            char if char.isalnum() else " " for char in item.topic_fingerprint.casefold()
        ).split()
    )
    if topic:
        result.append(("topic", topic))
    return tuple(result)


def _url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return url.split("#", 1)[0].strip()
    port = parsed.port
    normal_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    netloc = host if port in (None, normal_port) else f"{host}:{port}"
    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path.rstrip("/") or "/",
            urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
            "",
        )
    )


def _sentences(text: str) -> int:
    return sum(bool(part.strip()) for part in text.replace("!", ".").replace("?", ".").split("."))


__all__ = [
    "Digest",
    "DigestCandidate",
    "DigestCard",
    "DigestService",
    "DigestWeightConfig",
    "PreliminaryRisk",
    "ScoringSnapshot",
    "SourceFailure",
]
