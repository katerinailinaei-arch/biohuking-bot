from __future__ import annotations

from datetime import date

from bodrye_bot.digest.service import Digest
from bodrye_bot.digest.views import render_digest


def test_empty_digest_does_not_repeat_rss_shutdown_line() -> None:
    text = render_digest(Digest(digest_date=date(2026, 9, 15), cards=()))

    assert "Автоленту" not in text
    assert "скучн" not in text.lower()
    assert "канала-ориентира" in text
    assert "/sources" in text
    assert "своими словами" in text
