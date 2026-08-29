from __future__ import annotations

from bodrye_bot.config import Settings
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.onboarding import OnboardingService, ReadinessCheck
from bodrye_bot.telegram.router import CallbackCodec, TelegramShell


def build_telegram_shell(
    settings: Settings,
    *,
    database_check: ReadinessCheck | None = None,
    channel_check: ReadinessCheck | None = None,
    provider_check: ReadinessCheck | None = None,
    sources_check: ReadinessCheck | None = None,
    style_check: ReadinessCheck | None = None,
) -> TelegramShell:
    """Compose the safe shell; the provider remains blocked until Task 6 wires it."""
    onboarding = OnboardingService(
        database_check=database_check if database_check is not None else _blocked,
        channel_check=channel_check if channel_check is not None else _blocked,
        provider_check=provider_check,
        sources_check=sources_check if sources_check is not None else _blocked,
        style_check=style_check if style_check is not None else _blocked,
    )
    signing_secret = settings.telegram_bot_token.get_secret_value().encode("utf-8")
    return TelegramShell(
        owner_guard=OwnerGuard(settings.telegram_owner_id),
        onboarding=onboarding,
        callback_codec=CallbackCodec(signing_secret),
    )


async def _blocked() -> bool:
    return False


__all__ = ["build_telegram_shell"]
