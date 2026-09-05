from __future__ import annotations

from pathlib import Path

from bodrye_bot.telegram.owner_guide import FileOwnerGuide, InMemoryOwnerGuide


def test_in_memory_guide_starts_unseen() -> None:
    guide = InMemoryOwnerGuide()

    assert guide.has_completed_onboarding(42) is False
    guide.mark_onboarding_complete(42)
    assert guide.has_completed_onboarding(42) is True
    assert guide.has_completed_onboarding(7) is False


def test_file_guide_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "guide.json"
    first = FileOwnerGuide(path)
    first.mark_onboarding_complete(42)
    first.replace_tone_samples(42, ("спокойный тон",))

    second = FileOwnerGuide(path)
    assert second.has_completed_onboarding(42) is True
    assert second.tone_samples(42) == ("спокойный тон",)
