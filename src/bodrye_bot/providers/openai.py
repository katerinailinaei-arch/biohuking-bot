from __future__ import annotations

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.ports.llm import Jitter, LLMTransport, Sleep
from bodrye_bot.providers.llm_base import BaseLLMProvider, _default_jitter, _default_sleep


class OpenAIProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        transport: LLMTransport,
        model: str,
        selected_provider: str,
        cost_guard_enabled: bool,
        eval_activated: bool,
        sleep: Sleep = _default_sleep,
        jitter: Jitter = _default_jitter,
    ) -> None:
        if not (selected_provider == "openai" and cost_guard_enabled and eval_activated):
            raise SafeError.for_code(
                SafeErrorCode.LLM_UNAVAILABLE,
                developer_detail="provider_error_class=openai_not_activated",
            )
        super().__init__(
            transport=transport,
            provider_name="openai",
            model=model,
            sleep=sleep,
            jitter=jitter,
        )


__all__ = ["OpenAIProvider"]
