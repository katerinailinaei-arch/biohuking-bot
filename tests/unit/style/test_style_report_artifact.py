from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.style.report import canonical_report_hash, load_calibration_report

ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = ROOT / "evals" / "style" / "keti-calibration-v1.json"


def _artifact() -> dict[str, object]:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _rehash(payload: dict[str, object]) -> None:
    payload["content_hash"] = canonical_report_hash(payload)


def test_owner_calibration_artifact_is_exact_and_recomputes_gate() -> None:
    report = load_calibration_report(REPORT_PATH)

    assert report.owner_alias == "keti"
    assert len(report.topics) == 8
    assert [topic.selected_variant for topic in report.topics] == [1, 1, 3, 3, None, None, 3, 3]
    assert report.topics[4].custom_edit == (
        'Постоянная усталость имеет разные причины. Конечно ИИ вам "всё" расскажет, '
        "но всё таки дополнительно нужна консультация врача."
    )
    assert report.topics[5].medical_limitation
    assert len(report.confirmed_rules) == 5
    assert all(rule.confirmed for rule in report.confirmed_rules)
    assert len(report.holdouts) == 3
    assert [item.rating for item in report.holdouts] == [5, 5, 5]
    assert all(item.accepted_without_rewrite for item in report.holdouts)
    assert report.gate.passed is True
    assert report.gate.median_rating == 5
    assert len(report.positive_example_ids) == 3


def test_loader_rejects_hash_tampering_with_safe_russian_error(tmp_path: Path) -> None:
    payload = _artifact()
    topics = payload["topics"]
    assert isinstance(topics, list)
    assert isinstance(topics[0], dict)
    topics[0]["risk"] = "tampered"

    with pytest.raises(SafeError) as caught:
        load_calibration_report(_write(tmp_path, payload))

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY
    assert "Профиль стиля пока не готов" in caught.value.user_message


def test_loader_rejects_unconfirmed_rule_even_with_valid_hash(tmp_path: Path) -> None:
    payload = _artifact()
    rules = payload["confirmed_rules"]
    assert isinstance(rules, list)
    assert isinstance(rules[0], dict)
    rules[0]["confirmed"] = False
    _rehash(payload)

    with pytest.raises(SafeError) as caught:
        load_calibration_report(_write(tmp_path, payload))

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY


def test_loader_recomputes_gate_and_rejects_failed_gate(tmp_path: Path) -> None:
    payload = _artifact()
    holdouts = payload["holdouts"]
    assert isinstance(holdouts, list)
    for item in holdouts:
        assert isinstance(item, dict)
        item["rating"] = 1
        item["accepted_without_rewrite"] = False
    reported = payload["reported_gate"]
    assert isinstance(reported, dict)
    reported["passed"] = True
    _rehash(payload)

    with pytest.raises(SafeError) as caught:
        load_calibration_report(_write(tmp_path, payload))

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY


def test_loader_ignores_stored_passed_flag_and_uses_recomputed_result(tmp_path: Path) -> None:
    payload = _artifact()
    reported = payload["reported_gate"]
    assert isinstance(reported, dict)
    reported["passed"] = False
    _rehash(payload)

    report = load_calibration_report(_write(tmp_path, payload))

    assert report.reported_gate.passed is False
    assert report.gate.passed is True


@pytest.mark.parametrize("rating", [True, 5.0, "5"])
def test_loader_requires_integer_holdout_ratings(
    tmp_path: Path, rating: object
) -> None:
    payload = _artifact()
    holdouts = payload["holdouts"]
    assert isinstance(holdouts, list)
    assert isinstance(holdouts[0], dict)
    holdouts[0]["rating"] = rating
    _rehash(payload)

    with pytest.raises(SafeError):
        load_calibration_report(_write(tmp_path, payload))
