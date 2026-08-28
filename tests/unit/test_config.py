from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from bodrye_bot.config import ProviderName, Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


def base_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "telegram_bot_token": "telegram-secret",
        "telegram_owner_id": 42,
        "telegram_channel_id": -100123,
        "groq_api_key": "groq-secret",
    }
    values.update(overrides)
    return values


def test_settings_use_safe_mvp_defaults() -> None:
    settings = Settings(**base_settings())

    assert settings.llm_provider is ProviderName.GROQ
    assert settings.llm_model == "openai/gpt-oss-120b"
    assert settings.paid_fallback_enabled is False
    assert settings.budget_soft_rub == Decimal("3500")
    assert settings.budget_hard_rub == Decimal("5000")
    assert settings.timezone == "Europe/Moscow"


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"paid_fallback_enabled": True}, "paid_fallback_enabled"),
        ({"budget_soft_rub": "5000"}, "soft budget must be below hard budget"),
        ({"budget_soft_rub": "5100"}, "soft budget must be below hard budget"),
    ],
)
def test_settings_reject_unsafe_cost_configuration(
    overrides: dict[str, object], expected_fragment: str
) -> None:
    with pytest.raises(ValidationError, match=expected_fragment):
        Settings(**base_settings(**overrides))


def test_secrets_are_typed_and_not_exposed_in_repr() -> None:
    settings = Settings(**base_settings())

    assert isinstance(settings.telegram_bot_token, SecretStr)
    assert isinstance(settings.groq_api_key, SecretStr)
    rendered = repr(settings)
    assert "telegram-secret" not in rendered
    assert "groq-secret" not in rendered


def test_openai_requires_key_when_explicitly_selected() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(**base_settings(llm_provider="openai", llm_model="gpt-5.6-sol"))


def test_get_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "-100123")
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")

    settings = get_settings()

    assert settings.telegram_owner_id == 42
    assert settings.telegram_channel_id == -100123

