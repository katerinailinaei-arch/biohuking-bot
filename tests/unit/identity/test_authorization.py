from __future__ import annotations

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.telegram.router import IncomingMessage, TelegramShell
from bodrye_bot.telegram.views import render_safe_error


class RepositorySpy:
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, owner_id: int, workflow_id: object) -> object:
        self.calls += 1
        raise AssertionError("an unauthorised request must not load an object")


def test_owner_guard_returns_only_the_configured_owner() -> None:
    guard = OwnerGuard(owner_id=42)

    assert guard.authorize(42) == 42
    with pytest.raises(SafeError) as caught:
        guard.authorize(999)
    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_unknown_user_gets_neutral_denial_without_object_lookup() -> None:
    repository = RepositorySpy()
    shell = TelegramShell(owner_guard=OwnerGuard(42), workflow_repository=repository)

    answer = await shell.handle(IncomingMessage(sender_id=999, text="/status"))

    assert answer.text == "Доступ закрыт. Если это ошибка, проверьте Telegram ID владельца."
    assert repository.calls == 0


def test_safe_error_view_escapes_html_and_includes_all_user_safe_fields() -> None:
    error = SafeError(
        code=SafeErrorCode.INTERNAL_ERROR,
        message_ru="<небезопасно>",
        preserved_ru="& сохранено",
        next_action_ru="попробуйте <ещё раз>",
        trace_id="a" * 32,
    )

    rendered = render_safe_error(error)

    assert "&lt;небезопасно&gt;" in rendered
    assert "&amp; сохранено" in rendered
    assert "Код обращения" in rendered
