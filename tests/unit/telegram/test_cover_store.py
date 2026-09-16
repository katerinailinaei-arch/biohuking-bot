from __future__ import annotations

from pathlib import Path

from bodrye_bot.telegram.cover_state import CoverSession, FileCoverSessionStore
from bodrye_bot.visual.cover import LogoPlace


def test_file_cover_store_reloads_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "cover.json"
    first = FileCoverSessionStore(path)
    session = CoverSession.from_photo(b"jpeg-bytes")
    first.put(42, session)

    second = FileCoverSessionStore(path)
    loaded = second.get(42)

    assert loaded is not None
    assert loaded.cover_id == session.cover_id
    assert loaded.canvas == b"jpeg-bytes"
    assert loaded.badge is None


def test_file_cover_store_reloads_badge(tmp_path: Path) -> None:
    from bodrye_bot.telegram.cover_state import CoverBadge

    path = tmp_path / "cover.json"
    store = FileCoverSessionStore(path)
    session = CoverSession.from_photo(b"img")
    session.badge = CoverBadge(place=LogoPlace.TOP_RIGHT, ratio=0.25)
    store.put(7, session)

    loaded = FileCoverSessionStore(path).get(7)

    assert loaded is not None
    assert loaded.badge is not None
    assert loaded.badge.place is LogoPlace.TOP_RIGHT
    assert loaded.badge.ratio == 0.25
