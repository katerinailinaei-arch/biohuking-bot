from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_MAX_SAMPLES = 8
_MAX_SAMPLE_CHARS = 4000


class OwnerGuide(Protocol):
    def has_completed_onboarding(self, owner_id: int) -> bool: ...

    def mark_onboarding_complete(self, owner_id: int) -> None: ...

    def tone_samples(self, owner_id: int) -> tuple[str, ...]: ...

    def replace_tone_samples(self, owner_id: int, samples: tuple[str, ...]) -> None: ...

    def add_tone_sample(self, owner_id: int, text: str) -> int: ...


@dataclass
class _Record:
    onboarding_complete: bool = False
    tone_samples: list[str] = field(default_factory=list)


def _clip_sample(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > _MAX_SAMPLE_CHARS:
        return cleaned[:_MAX_SAMPLE_CHARS]
    return cleaned


class InMemoryOwnerGuide:
    def __init__(self) -> None:
        self._records: dict[int, _Record] = {}

    def _record(self, owner_id: int) -> _Record:
        return self._records.setdefault(owner_id, _Record())

    def has_completed_onboarding(self, owner_id: int) -> bool:
        return self._record(owner_id).onboarding_complete

    def mark_onboarding_complete(self, owner_id: int) -> None:
        self._record(owner_id).onboarding_complete = True

    def tone_samples(self, owner_id: int) -> tuple[str, ...]:
        return tuple(self._record(owner_id).tone_samples)

    def replace_tone_samples(self, owner_id: int, samples: tuple[str, ...]) -> None:
        clipped = [_clip_sample(item) for item in samples if item.strip()]
        self._record(owner_id).tone_samples = clipped

    def add_tone_sample(self, owner_id: int, text: str) -> int:
        clipped = _clip_sample(text)
        if not clipped:
            return len(self._record(owner_id).tone_samples)
        samples = self._record(owner_id).tone_samples
        if len(samples) >= _MAX_SAMPLES:
            samples.pop(0)
        samples.append(clipped)
        return len(samples)

    def export(self) -> dict[int, dict[str, object]]:
        return {
            owner_id: {
                "onboarding_complete": record.onboarding_complete,
                "tone_samples": list(record.tone_samples),
            }
            for owner_id, record in self._records.items()
        }


class FileOwnerGuide:
    """JSON file so a restart does not replay first-run onboarding."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._memory = InMemoryOwnerGuide()
        self._load()

    def has_completed_onboarding(self, owner_id: int) -> bool:
        return self._memory.has_completed_onboarding(owner_id)

    def mark_onboarding_complete(self, owner_id: int) -> None:
        self._memory.mark_onboarding_complete(owner_id)
        self._save()

    def tone_samples(self, owner_id: int) -> tuple[str, ...]:
        return self._memory.tone_samples(owner_id)

    def replace_tone_samples(self, owner_id: int, samples: tuple[str, ...]) -> None:
        self._memory.replace_tone_samples(owner_id, samples)
        self._save()

    def add_tone_sample(self, owner_id: int, text: str) -> int:
        count = self._memory.add_tone_sample(owner_id, text)
        self._save()
        return count

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if not str(key).lstrip("-").isdigit() or not isinstance(value, dict):
                continue
            owner_id = int(key)
            if value.get("onboarding_complete"):
                self._memory.mark_onboarding_complete(owner_id)
            samples = value.get("tone_samples", ())
            if isinstance(samples, list):
                texts = tuple(item for item in samples if isinstance(item, str))
                self._memory.replace_tone_samples(owner_id, texts)

    def _save(self) -> None:
        payload = {str(owner_id): body for owner_id, body in self._memory.export().items()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)


__all__ = ["FileOwnerGuide", "InMemoryOwnerGuide", "OwnerGuide"]
