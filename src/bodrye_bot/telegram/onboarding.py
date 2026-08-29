from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

ReadinessCheck = Callable[[], Awaitable[bool]]


async def _blocked() -> bool:
    return False


@dataclass(frozen=True)
class OnboardingResult:
    checks: dict[str, bool]

    @property
    def gates(self) -> frozenset[str]:
        return frozenset(self.checks)

    @property
    def ready(self) -> bool:
        return all(self.checks.values())

    @property
    def text(self) -> str:
        labels = {
            "database": "база данных",
            "channel": "канал и право отправки",
            "provider": "провайдер генерации",
            "sources": "разрешённые запросы источников",
            "style": "активный профиль стиля",
        }
        lines = ["Проверка готовности:"]
        for key, label in labels.items():
            state = "готово" if self.checks[key] else "требует настройки"
            lines.append(f"• {label}: {state}")
        return "\n".join(lines)


class OnboardingService:
    """Runs injected readiness checks and never exposes their credentials."""

    def __init__(
        self,
        *,
        database_check: ReadinessCheck = _blocked,
        channel_check: ReadinessCheck = _blocked,
        provider_check: ReadinessCheck | None = None,
        sources_check: ReadinessCheck = _blocked,
        style_check: ReadinessCheck = _blocked,
    ) -> None:
        self._checks: dict[str, ReadinessCheck] = {
            "database": database_check,
            "channel": channel_check,
            "provider": provider_check if provider_check is not None else _blocked,
            "sources": sources_check,
            "style": style_check,
        }

    async def check(self) -> OnboardingResult:
        results: dict[str, bool] = {}
        for name, probe in self._checks.items():
            try:
                results[name] = bool(await probe())
            except Exception:
                results[name] = False
        return OnboardingResult(checks=results)


__all__ = ["OnboardingResult", "OnboardingService", "ReadinessCheck"]
