from __future__ import annotations

from bodrye_bot.ports.llm import AvailableModel, Jitter, LLMTransport, Sleep
from bodrye_bot.providers.llm_base import BaseLLMProvider, _default_jitter, _default_sleep

_EVAL_CANDIDATES = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}


class GroqProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        transport: LLMTransport,
        model: str,
        sleep: Sleep = _default_sleep,
        jitter: Jitter = _default_jitter,
    ) -> None:
        super().__init__(
            transport=transport,
            provider_name="groq",
            model=model,
            sleep=sleep,
            jitter=jitter,
        )

    async def list_models(self) -> tuple[AvailableModel, ...]:
        models = await self._transport.list_models()
        return tuple(
            AvailableModel(id=str(item["id"]), provider="groq")
            for item in models
            if item.get("id") in _EVAL_CANDIDATES
            and item.get("active") is True
            and item.get("production") is True
            and item.get("strict_output") is True
        )


__all__ = ["GroqProvider"]
