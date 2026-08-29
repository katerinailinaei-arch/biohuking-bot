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
            if value is not None and _is_sensitive_label(value):
                object.__setattr__(self, field_name, "[redacted]")
        if self.provider_request_id is not None and _is_sensitive_label(self.provider_request_id):
            object.__setattr__(self, "provider_request_id", None)

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


def _is_sensitive_label(value: str) -> bool:
    return _CREDENTIAL_URL.search(value) is not None or _SECRET_PREFIX.search(value) is not None


__all__ = ["UsageRecord"]
