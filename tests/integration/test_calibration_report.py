from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import AuditEvent, StyleExample, StyleProfile, StyleRule
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import AngleBrief
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.style.context import StyleContextBuilder
from bodrye_bot.style.report import (
    CalibrationReportApplier,
    ValidatedCalibrationReport,
    canonical_report_hash,
    load_calibration_report,
)

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "evals" / "style" / "keti-calibration-v1.json"


@pytest.mark.asyncio
async def test_apply_report_is_transactional_idempotent_and_builds_bounded_context(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    report = _fresh_report(tmp_path)
    applier = CalibrationReportApplier(
        session_factory=session_factory, owner_guard=OwnerGuard(owner_id=42)
    )
    first = await applier.apply(owner_id=42, report=report)
    second = await applier.apply(owner_id=42, report=report)

    assert first == second
    async with session_factory() as session:
        profile = await session.get(StyleProfile, report.profile_id)
        assert profile is not None
        assert profile.status == "active"
        assert profile.calibration_report_hash == report.content_hash
        rule_count = await session.scalar(
            select(func.count()).select_from(StyleRule).where(
                StyleRule.profile_id == report.profile_id,
                StyleRule.owner_id == 42,
            )
        )
        example_count = await session.scalar(
            select(func.count()).select_from(StyleExample).where(
                StyleExample.profile_id == report.profile_id,
                StyleExample.owner_id == 42,
            )
        )
        audit_count = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.object_id == report.profile_id,
                AuditEvent.owner_id == 42,
            )
        )
        assert rule_count == 5
        assert example_count == 3
        assert audit_count == 1

        repository = __import__(
            "bodrye_bot.db.repositories.style", fromlist=["SqlAlchemyStyleRepository"]
        ).SqlAlchemyStyleRepository(
            session,
            __import__(
                "bodrye_bot.operations.audit", fromlist=["SqlAlchemyAuditWriter"]
            ).SqlAlchemyAuditWriter(session, ensure_active=lambda: None),
            ensure_active=lambda: None,
        )
        context = await StyleContextBuilder(owner_id=42, repository=repository).build(
            report.profile_id,
            rubric="health_habits",
            format="post",
            risk="mixed",
            tags=("practical",),
            selected_angle=AngleBrief(id="test", name="Тест"),
            medical_constraints=("Не ставить диагноз.",),
        )
        assert len(context.hard_rules) + len(context.format_rules) == 5
        assert 3 <= len(context.positive_examples) <= 5


@pytest.mark.asyncio
async def test_apply_report_rolls_back_everything_when_audit_fails(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    report = _fresh_report(tmp_path)

    class FailingAudit:
        async def record(self, event: object) -> None:
            del event
            raise RuntimeError("audit unavailable")

    def audit_factory(session: AsyncSession, ensure_active: Callable[[], None]) -> FailingAudit:
        del session, ensure_active
        return FailingAudit()

    applier = CalibrationReportApplier(
        session_factory=session_factory,
        owner_guard=OwnerGuard(owner_id=42),
        audit_factory=audit_factory,
    )
    with pytest.raises(SafeError) as caught:
        await applier.apply(owner_id=42, report=report)
    assert caught.value.code is SafeErrorCode.INTERNAL_ERROR

    async with session_factory() as session:
        assert await session.get(StyleProfile, report.profile_id) is None
        assert await session.scalar(
            select(func.count()).select_from(StyleRule).where(
                StyleRule.profile_id == report.profile_id
            )
        ) == 0


@pytest.mark.asyncio
async def test_apply_report_rejects_foreign_owner_without_disclosing_profile(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    report = _fresh_report(tmp_path)
    applier = CalibrationReportApplier(
        session_factory=session_factory, owner_guard=OwnerGuard(owner_id=42)
    )

    with pytest.raises(SafeError) as caught:
        await applier.apply(owner_id=999, report=report)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN


@pytest.mark.asyncio
async def test_reapplying_same_report_identity_with_different_hash_fails_closed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    report = _fresh_report(tmp_path)
    payload = _payload_for_report(report)
    topics = payload["topics"]
    assert isinstance(topics, list) and isinstance(topics[0], dict)
    topics[0]["selected_variant"] = 2
    payload["content_hash"] = canonical_report_hash(payload)
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    changed = load_calibration_report(changed_path)
    applier = CalibrationReportApplier(
        session_factory=session_factory, owner_guard=OwnerGuard(owner_id=42)
    )
    await applier.apply(owner_id=42, report=report)
    with pytest.raises(SafeError) as caught:
        await applier.apply(owner_id=42, report=changed)
    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY


@pytest.mark.asyncio
async def test_apply_report_refuses_nonempty_calibrating_profile_without_partial_activation(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    report = _fresh_report(tmp_path)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                StyleProfile(
                    id=report.profile_id,
                    owner_id=42,
                    version=report.profile_version,
                    status="calibrating",
                )
            )
            session.add(
                StyleRule(
                    id=uuid4(),
                    owner_id=42,
                    profile_id=report.profile_id,
                    scope="format",
                    rule_text="Неподтверждённое правило.",
                    origin="legacy",
                    status="proposed",
                    format="post",
                    risks=[],
                    tags=[],
                    pattern_key="legacy:proposal",
                )
            )
    applier = CalibrationReportApplier(
        session_factory=session_factory, owner_guard=OwnerGuard(owner_id=42)
    )

    with pytest.raises(SafeError) as caught:
        await applier.apply(owner_id=42, report=report)

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY
    async with session_factory() as session:
        profile = await session.get(StyleProfile, report.profile_id)
        assert profile is not None
        assert profile.status == "calibrating"
        assert profile.calibration_report_id is None
        assert await session.scalar(
            select(func.count()).select_from(StyleRule).where(
                StyleRule.profile_id == report.profile_id
            )
        ) == 1


def _fresh_report(tmp_path: Path) -> ValidatedCalibrationReport:
    payload: dict[str, Any] = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    payload["report_id"] = str(uuid4())
    payload["profile_id"] = str(uuid4())
    payload["profile_version"] = uuid4().int % 2_000_000_000 + 1
    payload["content_hash"] = canonical_report_hash(payload)
    path = tmp_path / f"{payload['report_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_calibration_report(path)


def _payload_for_report(report: ValidatedCalibrationReport) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    payload["report_id"] = str(report.report_id)
    payload["profile_id"] = str(report.profile_id)
    payload["profile_version"] = report.profile_version
    payload["content_hash"] = canonical_report_hash(payload)
    return payload
