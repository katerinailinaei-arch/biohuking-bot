from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from uuid import UUID

from bodrye_bot.ports.llm import UsageReport


@dataclass(frozen=True, repr=False)
class UsageRecord:
    """Persistence-ready provider metadata that deliberately contains no content."""

    owner_id: int
    workflow_id: UUID | None
    operation: str
    provider: str
    model: str
    status: str
    prompt_version: str
    schema_version: str
    provider_request_id: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    error_class: str | None
    trace_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "operation",
            "provider",
            "model",
            "status",
            "prompt_version",
            "schema_version",
            "error_class",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _sanitize_required_identifier(value))
        object.__setattr__(
            self,
            "provider_request_id",
            _sanitize_optional_identifier(self.provider_request_id),
        )
        if _TRACE_ID.fullmatch(self.trace_id) is None:
            object.__setattr__(self, "trace_id", "[redacted]")

    def __repr__(self) -> str:
        return (
            "UsageRecord("
            f"provider={self.provider!r}, operation={self.operation!r}, "
            f"status={self.status!r}, trace_id={self.trace_id!r})"
        )

    def to_log_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_report(self) -> UsageReport:
        return UsageReport.model_validate(asdict(self))


_CREDENTIAL_URL = re.compile(r"^[a-z][a-z0-9+.-]*://[^/:@\s]+:[^/@\s]+@", re.I)
_SECRET_PREFIX = re.compile(r"^(?:sk|gsk|xox[baprs])[-_]", re.I)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


def _is_sensitive_label(value: str) -> bool:
    return _CREDENTIAL_URL.search(value) is not None or _SECRET_PREFIX.search(value) is not None


def _sanitize_required_identifier(value: str) -> str:
    if _is_sensitive_label(value) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        return "[redacted]"
    return value


def _sanitize_optional_identifier(value: str | None) -> str | None:
    if value is None or _is_sensitive_label(value) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        return None
    return value


__all__ = ["UsageRecord"]
