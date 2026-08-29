from __future__ import annotations

from pydantic import SecretStr

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
        api_key: SecretStr | None = None,
        sleep: Sleep = _default_sleep,
        jitter: Jitter = _default_jitter,
    ) -> None:
        has_nonempty_secret = isinstance(api_key, SecretStr) and bool(
            api_key.get_secret_value().strip()
        )
        if not (
            selected_provider == "openai"
            and cost_guard_enabled is True
            and eval_activated is True
            and has_nonempty_secret
        ):
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
