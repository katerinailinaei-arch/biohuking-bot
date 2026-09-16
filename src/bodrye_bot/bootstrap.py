from __future__ import annotations

from pathlib import Path

from bodrye_bot.config import Settings
from bodrye_bot.db.base import async_session_factory
from bodrye_bot.db.repositories.usage import SqlAlchemyUsageLedger
from bodrye_bot.digest.worker import DigestWorker
from bodrye_bot.editorial.memory import InMemoryManualPostStore
from bodrye_bot.editorial.ports import ChannelPublisher
from bodrye_bot.editorial.template_draft import TemplateDraftWriter
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.operations.token_budget import InMemoryUsageLedger
from bodrye_bot.ports.usage_ledger import UsageLedger
from bodrye_bot.telegram.cover_state import FileCoverSessionStore
from bodrye_bot.telegram.onboarding import OnboardingService, ReadinessCheck
from bodrye_bot.telegram.owner_guide import FileOwnerGuide
from bodrye_bot.telegram.router import CallbackCodec, TelegramShell

_MANUAL_POSTS = InMemoryManualPostStore()


def build_telegram_shell(
    settings: Settings,
    *,
    database_check: ReadinessCheck | None = None,
    channel_check: ReadinessCheck | None = None,
    provider_check: ReadinessCheck | None = None,
    sources_check: ReadinessCheck | None = None,
    style_check: ReadinessCheck | None = None,
    channel_publisher: ChannelPublisher | None = None,
    digest_worker: DigestWorker | None = None,
    usage_ledger: UsageLedger | None = None,
) -> TelegramShell:
    """Compose the owner shell and the short manual-publish path."""
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
        manual_post_store=_MANUAL_POSTS,
        draft_writer=TemplateDraftWriter(),
        channel_publisher=channel_publisher,
        digest_worker=digest_worker,
        owner_guide=FileOwnerGuide(Path("data") / "owner_guide.json"),
        cover_sessions=FileCoverSessionStore(Path("data") / "cover_session.json"),
        usage_ledger=usage_ledger,
    )


def build_usage_ledger(settings: Settings) -> UsageLedger:
    url = settings.database_url
    if url is None or not url.get_secret_value().strip():
        return InMemoryUsageLedger()
    try:
        return SqlAlchemyUsageLedger(async_session_factory(settings))
    except Exception:
        return InMemoryUsageLedger()


async def _blocked() -> bool:
    return False


__all__ = ["build_telegram_shell", "build_usage_ledger"]
