from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import StyleExample as StyleExampleModel
from bodrye_bot.db.models import StyleProfile as StyleProfileModel
from bodrye_bot.db.models import StyleRule as StyleRuleModel
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import HoldoutResult, StyleGate, StyleGateDecision
from bodrye_bot.domain.workflow import Actor
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.operations.audit import (
    AuditEntry,
    AuditEventType,
    AuditObjectType,
    SqlAlchemyAuditWriter,
)

# This is a release-controlled digest, distinct from the untrusted hash carried
# by the JSON itself. Updating the artifact requires an explicit code review.
TRUSTED_CALIBRATION_REPORT_HASHES: Mapping[str, str] = MappingProxyType(
    {
        "style-calibration-v1": "10719718c2118ed1a3d0823051c9f6bcb949adc876fc3d48e9a3f46a1aea4dc1"
    }
)
_SHA256_LOWERHEX = re.compile(r"^[0-9a-f]{64}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CalibrationTopicRecord(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    risk: str = Field(min_length=1, max_length=32)
    variants: tuple[str, str, str]
    selected_variant: StrictInt | None = None
    custom_edit: str | None = None
    medical_limitation: str | None = None


class ConfirmedRuleRecord(_StrictModel):
    id: str = Field(pattern=r"^rule-[1-5]$")
    text: str = Field(min_length=1, max_length=2_000)
    scope: str = Field(pattern=r"^(hard|format)$")
    format: str | None = None
    confirmed: StrictBool
    confirmation: str = Field(min_length=1, max_length=64)


class HoldoutRecord(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=3_800)
    rating: StrictInt
    accepted_without_rewrite: StrictBool
    hard_rule_violations: StrictInt
    edit_note: str | None = None
    rubric: str = Field(min_length=1, max_length=128)
    format: str = Field(min_length=1, max_length=16)
    risk: str = Field(min_length=1, max_length=32)
    tags: tuple[str, ...]


class ReportedGate(_StrictModel):
    passed: StrictBool


class _CalibrationArtifact(_StrictModel):
    schema_version: str = Field(pattern=r"^style-calibration-v1$")
    report_id: UUID
    profile_id: UUID
    profile_version: StrictInt
    owner_alias: str = Field(pattern=r"^keti$")
    topics: tuple[CalibrationTopicRecord, ...]
    confirmed_rules: tuple[ConfirmedRuleRecord, ...]
    holdouts: tuple[HoldoutRecord, ...]
    positive_example_ids: tuple[str, ...]
    reported_gate: ReportedGate
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ValidatedCalibrationReport:
    schema_version: str
    report_id: UUID
    profile_id: UUID
    profile_version: int
    owner_alias: str
    topics: tuple[CalibrationTopicRecord, ...]
    confirmed_rules: tuple[ConfirmedRuleRecord, ...]
    holdouts: tuple[HoldoutRecord, ...]
    positive_example_ids: tuple[str, ...]
    reported_gate: ReportedGate
    content_hash: str
    gate: StyleGateDecision


def _not_ready(detail: str) -> SafeError:
    return SafeError.for_code(
        SafeErrorCode.STYLE_PROFILE_NOT_READY, developer_detail=detail
    )


def canonical_report_hash(payload: Mapping[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("content_hash", None)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_calibration_report(path: Path) -> ValidatedCalibrationReport:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_pairs_without_duplicates
        )
        if not isinstance(raw, dict):
            raise ValueError("report root must be an object")
        stored_hash = raw.get("content_hash")
        if not isinstance(stored_hash, str) or stored_hash != canonical_report_hash(raw):
            raise ValueError("calibration report hash mismatch")
        artifact = _CalibrationArtifact.model_validate(raw)
        gate = _validate_artifact(artifact)
    except SafeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise _not_ready(type(exc).__name__) from None
    return ValidatedCalibrationReport(
        schema_version=artifact.schema_version,
        report_id=artifact.report_id,
        profile_id=artifact.profile_id,
        profile_version=artifact.profile_version,
        owner_alias=artifact.owner_alias,
        topics=artifact.topics,
        confirmed_rules=artifact.confirmed_rules,
        holdouts=artifact.holdouts,
        positive_example_ids=artifact.positive_example_ids,
        reported_gate=artifact.reported_gate,
        content_hash=artifact.content_hash,
        gate=gate,
    )


def _validate_artifact(artifact: _CalibrationArtifact) -> StyleGateDecision:
    if artifact.profile_version < 1:
        raise _not_ready("profile version must be positive")
    if not 8 <= len(artifact.topics) <= 10:
        raise _not_ready("8-10 topics required")
    if len({topic.id for topic in artifact.topics}) != len(artifact.topics):
        raise _not_ready("topic ids must be unique")
    if len({topic.risk for topic in artifact.topics}) < 2:
        raise _not_ready("risk-diverse topics required")
    for topic in artifact.topics:
        if any(not variant.strip() for variant in topic.variants):
            raise _not_ready("three non-empty variants required")
        selected = topic.selected_variant
        if selected is not None and not 1 <= selected <= 3:
            raise _not_ready("selected variant is outside 1..3")
        if selected is None and not (topic.custom_edit or "").strip():
            raise _not_ready("explicit choice or custom edit required")
    medical_topic = next(
        (topic for topic in artifact.topics if topic.id == "full-checkup-mri"), None
    )
    if medical_topic is None or not (medical_topic.medical_limitation or "").strip():
        raise _not_ready("full-checkup/MRI limitation must be explicit")

    if len(artifact.confirmed_rules) != 5:
        raise _not_ready("exactly five confirmed rules required")
    if {rule.id for rule in artifact.confirmed_rules} != {
        f"rule-{index}" for index in range(1, 6)
    }:
        raise _not_ready("five distinct rule decisions required")
    for rule in artifact.confirmed_rules:
        if not rule.confirmed or rule.confirmation != f"Запомнить правило {rule.id[-1]}":
            raise _not_ready("each rule requires explicit owner confirmation")
        if rule.scope == "format" and rule.format != "post":
            raise _not_ready("format rule must name its format")
        if rule.scope == "hard" and rule.format is not None:
            raise _not_ready("hard rule cannot be format-scoped")

    topic_ids = {topic.id for topic in artifact.topics}
    if len(artifact.holdouts) != 3:
        raise _not_ready("three holdouts required")
    holdout_ids = {item.id for item in artifact.holdouts}
    if len(holdout_ids) != 3 or holdout_ids & topic_ids:
        raise _not_ready("holdouts must be unique and unseen")
    for item in artifact.holdouts:
        if not 1 <= item.rating <= 5 or item.hard_rule_violations < 0:
            raise _not_ready("holdout values outside gate domain")
        if not item.tags:
            raise _not_ready("holdout tags required")
    positive_ids = artifact.positive_example_ids
    if not 3 <= len(positive_ids) <= 5 or len(set(positive_ids)) != len(positive_ids):
        raise _not_ready("3-5 distinct positive examples required")
    if not set(positive_ids) <= holdout_ids:
        raise _not_ready("positive examples must reference holdouts")
    accepted = {
        item.id
        for item in artifact.holdouts
        if item.rating >= 4 and item.accepted_without_rewrite
    }
    if not set(positive_ids) <= accepted:
        raise _not_ready("positive examples must be owner-approved")

    results = tuple(
        HoldoutResult(
            topic_id=item.id,
            rating=item.rating,
            accepted_without_rewrite=item.accepted_without_rewrite,
            hard_rule_violations=item.hard_rule_violations,
        )
        for item in artifact.holdouts
    )
    gate = StyleGate().evaluate(results)
    if not gate.passed:
        raise _not_ready(f"style gate failed: {gate.reason}")
    return gate


class _AuditWriter(Protocol):
    async def record(self, event: AuditEntry) -> None: ...


AuditFactory = Callable[[AsyncSession, Callable[[], None]], _AuditWriter]


class CalibrationReportApplier:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        owner_guard: OwnerGuard,
        audit_factory: AuditFactory | None = None,
        clock: Callable[[], datetime] | None = None,
        trusted_report_hashes: Mapping[str, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._owner_guard = owner_guard
        self._audit_factory = audit_factory or (
            lambda session, ensure: SqlAlchemyAuditWriter(
                session, ensure_active=ensure
            )
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._trusted_report_hashes = (
            TRUSTED_CALIBRATION_REPORT_HASHES
            if trusted_report_hashes is None
            else dict(trusted_report_hashes)
        )

    async def apply(
        self, *, owner_id: int, report: ValidatedCalibrationReport
    ) -> UUID:
        self._owner_guard.authorize(owner_id)
        _require_trusted_digest(report, self._trusted_report_hashes)
        _revalidate_report(report)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    try:
                        async with session.begin_nested():
                            return await self._apply_in_transaction(
                                session=session, owner_id=owner_id, report=report
                            )
                    except IntegrityError:
                        return await self._recover_concurrent_apply(
                            session=session, owner_id=owner_id, report=report
                        )
        except SafeError:
            raise
        except Exception as exc:
            raise SafeError.for_code(
                SafeErrorCode.INTERNAL_ERROR,
                developer_detail=f"calibration apply failed: {type(exc).__name__}",
            ) from None

    async def _apply_in_transaction(
        self,
        *,
        session: AsyncSession,
        owner_id: int,
        report: ValidatedCalibrationReport,
    ) -> UUID:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {
                "lock_key": (
                    f"style-calibration:{owner_id}:{report.profile_id}:{report.report_id}"
                )
            },
        )
        existing = await session.scalar(
            select(StyleProfileModel)
            .where(StyleProfileModel.id == report.profile_id)
            .with_for_update()
        )
        if existing is not None:
            if existing.owner_id != owner_id:
                raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
            if (
                existing.calibration_report_id == report.report_id
                and existing.calibration_report_hash == report.content_hash
                and existing.status == "active"
                and existing.activated_at is not None
            ):
                return existing.id
            if existing.calibration_report_id is not None:
                raise _not_ready("report identity/hash conflict")
            if (
                existing.status != "calibrating"
                or existing.version != report.profile_version
            ):
                raise _not_ready("profile is not calibrating")
            stored_records = await session.scalar(
                select(
                    select(func.count())
                    .select_from(StyleRuleModel)
                    .where(
                        StyleRuleModel.owner_id == owner_id,
                        StyleRuleModel.profile_id == report.profile_id,
                    )
                    .scalar_subquery()
                    + select(func.count())
                    .select_from(StyleExampleModel)
                    .where(
                        StyleExampleModel.owner_id == owner_id,
                        StyleExampleModel.profile_id == report.profile_id,
                    )
                    .scalar_subquery()
                )
            )
            if stored_records:
                raise _not_ready("calibrating profile is not empty")
            profile = existing
            profile.calibration_report_id = report.report_id
            profile.calibration_report_hash = report.content_hash
        else:
            profile = StyleProfileModel(
                id=report.profile_id,
                owner_id=owner_id,
                version=report.profile_version,
                status="calibrating",
                calibration_report_id=report.report_id,
                calibration_report_hash=report.content_hash,
            )
            session.add(profile)
        await session.flush()

        confirmed_at = self._clock()
        for rule in report.confirmed_rules:
            session.add(
                StyleRuleModel(
                    id=uuid5(report.report_id, rule.id),
                    owner_id=owner_id,
                    profile_id=report.profile_id,
                    scope=rule.scope,
                    rule_text=rule.text,
                    origin="owner_calibration",
                    status="active",
                    confirmed_at=confirmed_at,
                    format=rule.format,
                    risks=[],
                    tags=[],
                    pattern_key="",
                )
            )
        positive_ids = set(report.positive_example_ids)
        for holdout in report.holdouts:
            if holdout.id not in positive_ids:
                continue
            session.add(
                StyleExampleModel(
                    id=uuid5(report.report_id, holdout.id),
                    owner_id=owner_id,
                    profile_id=report.profile_id,
                    text=holdout.body,
                    rubric=holdout.rubric,
                    format=holdout.format,
                    tags=list(holdout.tags),
                    risks=[holdout.risk],
                    rating=holdout.rating,
                    is_holdout=True,
                )
            )
        await session.flush()
        audit = self._audit_factory(session, lambda: None)
        await audit.record(
            AuditEntry(
                owner_id=owner_id,
                event_type=AuditEventType.CONFIGURATION_CHANGED,
                actor=Actor.OWNER,
                object_type=AuditObjectType.CONFIGURATION,
                object_id=report.profile_id,
                metadata={
                    "action": "style_profile_activated",
                    "report_id": str(report.report_id),
                    "report_hash": report.content_hash,
                    "rules": len(report.confirmed_rules),
                    "examples": len(report.positive_example_ids),
                },
            )
        )
        profile.status = "active"
        profile.activated_at = confirmed_at
        await session.flush()
        return profile.id

    async def _recover_concurrent_apply(
        self,
        *,
        session: AsyncSession,
        owner_id: int,
        report: ValidatedCalibrationReport,
    ) -> UUID:
        existing = await session.scalar(
            select(StyleProfileModel).where(StyleProfileModel.id == report.profile_id)
        )
        if existing is not None:
            if existing.owner_id != owner_id:
                raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
            if (
                existing.calibration_report_id == report.report_id
                and existing.calibration_report_hash == report.content_hash
                and existing.status == "active"
                and existing.activated_at is not None
            ):
                return existing.id
        raise _not_ready("concurrent calibration profile/report conflict")


def _revalidate_report(report: ValidatedCalibrationReport) -> None:
    try:
        artifact = _CalibrationArtifact(
            schema_version=report.schema_version,
            report_id=report.report_id,
            profile_id=report.profile_id,
            profile_version=report.profile_version,
            owner_alias=report.owner_alias,
            topics=report.topics,
            confirmed_rules=report.confirmed_rules,
            holdouts=report.holdouts,
            positive_example_ids=report.positive_example_ids,
            reported_gate=report.reported_gate,
            content_hash=report.content_hash,
        )
        payload = artifact.model_dump(mode="json")
        if canonical_report_hash(payload) != report.content_hash:
            raise ValueError("validated report was changed")
        _validate_artifact(artifact)
    except SafeError:
        raise
    except (ValidationError, ValueError) as exc:
        raise _not_ready(type(exc).__name__) from None


def _require_trusted_digest(
    report: ValidatedCalibrationReport, trusted_report_hashes: Mapping[str, str]
) -> None:
    expected_digest = trusted_report_hashes.get(report.schema_version)
    if (
        expected_digest is None
        or _SHA256_LOWERHEX.fullmatch(expected_digest) is None
        or expected_digest != report.content_hash
    ):
        raise _not_ready("calibration artifact digest is not trusted")


__all__ = [
    "CalibrationReportApplier",
    "ValidatedCalibrationReport",
    "canonical_report_hash",
    "load_calibration_report",
]
