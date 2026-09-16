from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from bodrye_bot.visual.cover import LOGO_RATIO, LogoPlace


@dataclass
class CoverBadge:
    place: LogoPlace = LogoPlace.BOTTOM_LEFT
    ratio: float = LOGO_RATIO


@dataclass
class CoverSession:
    cover_id: UUID
    original: bytes
    canvas: bytes
    working: bytes
    badge: CoverBadge | None = None
    wait_text: bool = False

    @classmethod
    def from_photo(cls, photo: bytes) -> CoverSession:
        return cls(cover_id=uuid4(), original=photo, canvas=photo, working=photo)


class CoverSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[int, CoverSession] = {}

    def get(self, owner_id: int) -> CoverSession | None:
        return self._sessions.get(owner_id)

    def put(self, owner_id: int, session: CoverSession) -> None:
        self._sessions[owner_id] = session

    def clear(self, owner_id: int) -> None:
        self._sessions.pop(owner_id, None)


class FileCoverSessionStore(CoverSessionStore):
    """Keep the last cover so buttons still work after a bot restart."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._load()

    def put(self, owner_id: int, session: CoverSession) -> None:
        super().put(owner_id, session)
        self._save(owner_id, session)

    def clear(self, owner_id: int) -> None:
        super().clear(owner_id)
        if self._path.is_file():
            try:
                self._path.unlink()
            except OSError:
                pass

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        owner_raw = raw.get("owner_id")
        if not isinstance(owner_raw, int):
            return
        session = _session_from_json(raw)
        if session is not None:
            super().put(owner_raw, session)

    def _save(self, owner_id: int, session: CoverSession) -> None:
        payload = {
            "owner_id": owner_id,
            "cover_id": str(session.cover_id),
            "canvas": base64.b64encode(session.canvas).decode("ascii"),
            "working": base64.b64encode(session.working).decode("ascii"),
            "wait_text": session.wait_text,
            "badge": None
            if session.badge is None
            else {"place": session.badge.place.value, "ratio": session.badge.ratio},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._path)


def _session_from_json(raw: dict[str, object]) -> CoverSession | None:
    try:
        cover_id = UUID(str(raw["cover_id"]))
        canvas = base64.b64decode(str(raw["canvas"]))
        working = base64.b64decode(str(raw["working"]))
    except (KeyError, ValueError, TypeError):
        return None
    if not canvas or not working:
        return None
    badge_raw = raw.get("badge")
    badge: CoverBadge | None = None
    if isinstance(badge_raw, dict):
        place_raw = str(badge_raw.get("place", LogoPlace.BOTTOM_LEFT))
        try:
            place = LogoPlace(place_raw)
        except ValueError:
            place = LogoPlace.BOTTOM_LEFT
        ratio_raw = badge_raw.get("ratio", LOGO_RATIO)
        ratio = float(ratio_raw) if isinstance(ratio_raw, int | float | str) else LOGO_RATIO
        badge = CoverBadge(place=place, ratio=ratio)
    return CoverSession(
        cover_id=cover_id,
        original=canvas,
        canvas=canvas,
        working=working,
        badge=badge,
        wait_text=bool(raw.get("wait_text")),
    )


__all__ = ["CoverBadge", "CoverSession", "CoverSessionStore", "FileCoverSessionStore"]
