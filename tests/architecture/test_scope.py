import re
from pathlib import Path


def test_mvp_source_has_no_removed_capability_modules() -> None:
    banned = {"voice", "stories", "instagram", "ads", "event_calendar", "userbot"}
    identifiers = {
        token
        for path in Path("src").rglob("*.py")
        for token in re.findall(r"[a-z_]+", path.read_text(encoding="utf-8").lower())
    }

    assert banned.isdisjoint(identifiers)


def test_security_threat_fixtures_exist() -> None:
    fixture_names = {path.name for path in Path("tests/security/fixtures").glob("*")}

    assert {"prompt_injection.txt", "medical_claims.json"} <= fixture_names


def test_install_instructions_upgrade_pip_before_using_dependency_groups() -> None:
    for path in (Path("README.md"), Path(".github/workflows/ci.yml")):
        text = path.read_text(encoding="utf-8")

        assert text.index('pip install --upgrade "pip>=25.1"') < text.index(
            "pip install -e . --group dev"
        )
