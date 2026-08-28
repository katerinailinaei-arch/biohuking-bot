import re

from bodrye_bot.domain.errors import SafeError, SafeErrorCode


def test_safe_error_has_russian_user_fields_and_trace_id() -> None:
    error = SafeError.for_code(SafeErrorCode.LLM_TIMEOUT)

    assert re.search(r"[А-Яа-яЁё]", error.message_ru)
    assert re.search(r"[А-Яа-яЁё]", error.preserved_ru)
    assert re.search(r"[А-Яа-яЁё]", error.next_action_ru)
    assert re.fullmatch(r"[0-9a-f]{32}", error.trace_id)
    assert error.trace_id in error.user_message
    assert "Traceback" not in error.user_message


def test_safe_error_trace_ids_are_unique() -> None:
    first = SafeError.for_code(SafeErrorCode.INTERNAL_ERROR)
    second = SafeError.for_code(SafeErrorCode.INTERNAL_ERROR)

    assert first.trace_id != second.trace_id


def test_llm_errors_never_show_technical_provider_text() -> None:
    technical = "HTTP 429 Authorization: Bearer secret-token stack trace"
    error = SafeError.for_code(SafeErrorCode.LLM_RATE_LIMIT, developer_detail=technical)

    assert technical not in error.user_message
    assert "secret-token" not in repr(error)
    assert error.developer_detail == technical

