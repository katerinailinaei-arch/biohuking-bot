from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bodrye_bot.db.models import AuditEvent as AuditEventModel
from bodrye_bot.domain.workflow import Actor

_MAX_METADATA_BYTES = 65_536
_MAX_STRING_LENGTH = 1_024
_MAX_COLLECTION_ITEMS = 64
_MAX_DEPTH = 4
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "header",
    "medical",
    "password",
    "patient",
    "prompt",
    "raw_source",
    "secret",
    "source_content",
    "token",
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(
        r"(?:bearer\s+|basic\s+[a-z0-9+/=]{8,}|api[_ -]?key|authorization\s*[:=]|"
        r"token\s*[:=]|password\s*[:=]|secret\s*[:=])",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:sk|gsk|xox[baprs])[-_][a-z0-9_-]{8,}\b|"
        r"\bAKIA[A-Z0-9]{16}\b|"
        r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b|"
        r"\b\d{8,10}:[a-zA-Z0-9_-]{30,}\b|"
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    re.compile(
        r"(?:system\s+prompt|developer\s+message|full\s+prompt|raw\s+source|"
        r"source\s+(?:body|content|text)|raw\s+(?:payload|response)|"
        r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:medical\s+record|patient\s+(?:data|record)|diagnos(?:is|tic)|"
        r"subscriber\s+(?:data|details)|\bdiabetes\b|\bдиагноз\w*|"
        r"\bанализ\w*|\bмедицин\w*|\bдиабет\w*|"
        r"дата\s+рождения|\bфио\b|\bпаспорт\w*|\bснилс\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
)
_DROP = object()
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class AuditEventType(StrEnum):
    WORKFLOW_STATE_CHANGED = "workflow.state_changed"
    CONFIGURATION_CHANGED = "configuration.changed"
    RULE_DECISION_RECORDED = "style.rule_decision"
    APPROVAL_RECORDED = "publication.approval_recorded"
    SCHEDULE_CHANGED = "publication.schedule_changed"
    DELETION_RECORDED = "memory.deletion_recorded"
    MANUAL_DELIVERY_RESOLVED = "publication.delivery_resolved_manually"
    BACKUP_RESULT_RECORDED = "operations.backup_result_recorded"


class AuditObjectType(StrEnum):
    WORKFLOW = "workflow"
    CONFIGURATION = "configuration"
    STYLE_RULE = "style_rule"
    APPROVAL = "approval"
    SCHEDULE = "schedule"
    DELETION = "deletion"
    DELIVERY = "delivery"
    BACKUP = "backup"


@dataclass(frozen=True)
class AuditEntry:
    owner_id: int
    event_type: AuditEventType
    actor: Actor
    object_type: AuditObjectType
    id: UUID = field(default_factory=uuid4)
    workflow_id: UUID | None = None
    object_id: UUID | None = None
    trace_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, AuditEventType):
            raise ValueError("Unsupported audit event type")
        try:
            object_type = AuditObjectType(self.object_type)
        except (TypeError, ValueError) as exc:
            raise ValueError("Unsupported audit envelope") from exc
        object.__setattr__(self, "object_type", object_type)
        if self.trace_id is not None and (
            _TRACE_ID_PATTERN.fullmatch(self.trace_id) is None
            or _contains_sensitive_value(self.trace_id)
        ):
            raise ValueError("Unsupported audit envelope")


def redact_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    """Return a JSON-safe, bounded audit payload without sensitive content."""
    cleaned = _redact_mapping(metadata, depth=0)
    serialized = json.dumps(cleaned, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > _MAX_METADATA_BYTES:
        return {"metadata_status": "omitted_due_to_size"}
    return cleaned


def _redact_mapping(metadata: Mapping[str, object], *, depth: int) -> dict[str, object]:
    if depth >= _MAX_DEPTH:
        return {}

    cleaned: dict[str, object] = {}
    for key, value in islice(metadata.items(), _MAX_COLLECTION_ITEMS):
        if (
            not isinstance(key, str)
            or _is_sensitive_key(key)
            or _contains_sensitive_value(key)
        ):
            continue
        safe_value = _redact_value(value, depth=depth + 1)
        if safe_value is not _DROP:
            cleaned[key[:128]] = safe_value
    return cleaned


def _redact_value(value: object, *, depth: int) -> object:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, str):
        candidate = value[:_MAX_STRING_LENGTH]
        if _contains_sensitive_value(candidate):
            return _DROP
        return candidate
    if isinstance(value, Mapping):
        cleaned_mapping = _redact_mapping(value, depth=depth)
        return cleaned_mapping if cleaned_mapping else _DROP
    if isinstance(value, list | tuple):
        if depth >= _MAX_DEPTH:
            return []
        cleaned_items: list[object] = []
        for item in value[:_MAX_COLLECTION_ITEMS]:
            safe_item = _redact_value(item, depth=depth + 1)
            if safe_item is not _DROP:
                cleaned_items.append(safe_item)
        return cleaned_items
    return _DROP


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    compact = normalized.replace("_", "")
    return any(
        part in normalized or part.replace("_", "") in compact
        for part in _SENSITIVE_KEY_PARTS
    )


def _normalize_key(key: str) -> str:
    with_word_boundaries = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", with_word_boundaries.lower()).strip("_")


def _contains_sensitive_value(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SENSITIVE_VALUE_PATTERNS)


class SqlAlchemyAuditWriter:
    """Append-only audit writer bound to an active SQLAlchemy transaction."""

    def __init__(
        self, session: AsyncSession, *, ensure_active: Callable[[], None]
    ) -> None:
        self._session = session
        self._ensure_active = ensure_active

    async def record(self, event: AuditEntry) -> None:
        self._ensure_active()
        self._session.add(
            AuditEventModel(
                id=event.id,
                owner_id=event.owner_id,
                workflow_id=event.workflow_id,
                event_type=event.event_type.value,
                actor=event.actor,
                object_type=event.object_type.value,
                object_id=event.object_id,
                trace_id=event.trace_id,
                metadata_json=redact_metadata(event.metadata),
            )
        )
        await self._session.flush()

    async def for_object(self, *, owner_id: int, object_id: UUID) -> list[AuditEntry]:
        self._ensure_active()
        result = await self._session.execute(
            select(AuditEventModel)
            .where(AuditEventModel.owner_id == owner_id, AuditEventModel.object_id == object_id)
            .order_by(AuditEventModel.created_at, AuditEventModel.id)
        )
        return [
            AuditEntry(
                id=event.id,
                owner_id=event.owner_id,
                workflow_id=event.workflow_id,
                event_type=AuditEventType(event.event_type),
                actor=event.actor,
                object_type=AuditObjectType(event.object_type),
                object_id=event.object_id,
                trace_id=event.trace_id,
                metadata=event.metadata_json,
            )
            for event in result.scalars()
        ]


__all__ = [
    "AuditEntry",
    "AuditEventType",
    "AuditObjectType",
    "SqlAlchemyAuditWriter",
    "redact_metadata",
]
