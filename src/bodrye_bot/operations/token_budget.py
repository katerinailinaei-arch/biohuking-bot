from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenCall:
    owner_id: int
    operation: str
    provider: str
    model: str
    status: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class OperationUsage:
    operation: str
    calls: int
    input_tokens: int
    output_tokens: int
    unknown_calls: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TokenBudget:
    calls: int
    input_tokens: int
    output_tokens: int
    unknown_calls: int
    by_operation: tuple[OperationUsage, ...]

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_OPERATION_LABELS = {
    "digest_localize": "перевод тем дайджеста",
}


def summarize_token_calls(calls: tuple[TokenCall, ...]) -> TokenBudget:
    grouped: dict[str, list[TokenCall]] = defaultdict(list)
    for call in calls:
        grouped[call.operation].append(call)
    by_operation = tuple(
        _operation_usage(operation, tuple(rows))
        for operation, rows in sorted(grouped.items())
    )
    return TokenBudget(
        calls=sum(item.calls for item in by_operation),
        input_tokens=sum(item.input_tokens for item in by_operation),
        output_tokens=sum(item.output_tokens for item in by_operation),
        unknown_calls=sum(item.unknown_calls for item in by_operation),
        by_operation=by_operation,
    )


def render_token_budget(budget: TokenBudget) -> str:
    if budget.calls == 0:
        return (
            "<b>Бюджет токенов</b>\n\n"
            "Пока нет записанных запросов к модели.\n\n"
            "Цифры появятся после перевода тем дайджеста через Groq "
            "(кнопка «Темы» или /digest). Кнопка «Пост» сейчас пишет без модели — "
            "там 0 токенов.\n\n"
            "Команда /costs делает то же самое."
        )
    lines = [
        "<b>Бюджет токенов</b>",
        "",
        "За время учёта в этом боте:",
        f"• запросов к модели: {_count(budget.calls)}",
        f"• входящих токенов: {_count(budget.input_tokens)}",
        f"• исходящих токенов: {_count(budget.output_tokens)}",
        f"• всего токенов: {_count(budget.total_tokens)}",
    ]
    if budget.unknown_calls:
        lines.append(
            f"• запросов без цифр от Groq: {_count(budget.unknown_calls)} "
            "(их не считаю нулём)"
        )
    lines.append("")
    lines.append("По операциям:")
    for item in budget.by_operation:
        label = _OPERATION_LABELS.get(item.operation, item.operation)
        lines.append(
            f"• {label}: {_count(item.total_tokens)} токенов "
            f"({_count(item.calls)} запрос.)"
        )
    lines.extend(
        (
            "",
            "Сейчас Groq в тарифе Free — за эти токены 0 ₽. "
            "Лимит канала (сервер + API): soft 3 500 ₽ и hard 5 000 ₽ в месяц. "
            "Платный запасной провайдер без вашего решения не включается.",
        )
    )
    return "\n".join(lines)


class InMemoryUsageLedger:
    """Process-local token log for tests and runs without PostgreSQL."""

    def __init__(self) -> None:
        self._calls: list[TokenCall] = []

    async def record(self, call: TokenCall) -> None:
        self._calls.append(call)

    async def for_owner(self, owner_id: int) -> TokenBudget:
        return summarize_token_calls(
            tuple(call for call in self._calls if call.owner_id == owner_id)
        )


def _operation_usage(operation: str, rows: tuple[TokenCall, ...]) -> OperationUsage:
    unknown = 0
    incoming = 0
    outgoing = 0
    for row in rows:
        if row.input_tokens is None or row.output_tokens is None:
            unknown += 1
        if row.input_tokens is not None:
            incoming += row.input_tokens
        if row.output_tokens is not None:
            outgoing += row.output_tokens
    return OperationUsage(
        operation=operation,
        calls=len(rows),
        input_tokens=incoming,
        output_tokens=outgoing,
        unknown_calls=unknown,
    )


def _count(value: int) -> str:
    return f"{value:_}".replace("_", " ")


__all__ = [
    "InMemoryUsageLedger",
    "OperationUsage",
    "TokenBudget",
    "TokenCall",
    "render_token_budget",
    "summarize_token_calls",
]
