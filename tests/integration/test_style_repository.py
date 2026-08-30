from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bodrye_bot.db.models import StyleProfile as StyleProfileModel
from bodrye_bot.db.repositories.style import SqlAlchemyStyleRepository
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import (
    EditObservation,
    RuleScope,
    RuleStatus,
    StyleExample,
    StyleRule,
)
from bodrye_bot.operations.audit import SqlAlchemyAuditWriter


def _rule(
    *,
    profile_id: UUID,
    status: RuleStatus = RuleStatus.PROPOSED,
    pattern_key: str = "opening:concrete-action",
) -> StyleRule:
    return StyleRule(
        id=uuid4(),
        owner_id=42,
        profile_id=profile_id,
        scope=RuleScope.FORMAT,
        format="post",
        text="Начинать с конкретного действия.",
        origin="edit",
        pattern_key=pattern_key,
        status=status,
        risks=("medium",),
        tags=("sleep",),
        confirmed_at=datetime.now(UTC) if status is RuleStatus.ACTIVE else None,
    )


@pytest.mark.asyncio
async def test_style_repository_round_trips_all_context_and_learning_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    profile_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                StyleProfileModel(
                    id=profile_id,
                    owner_id=42,
                    version=profile_id.int % 2_000_000_000 + 1,
                    status="active",
                    activated_at=datetime.now(UTC),
                )
            )
            repository = SqlAlchemyStyleRepository(
                session,
                SqlAlchemyAuditWriter(session, ensure_active=lambda: None),
                ensure_active=lambda: None,
            )
            rule = _rule(profile_id=profile_id, status=RuleStatus.ACTIVE)
            await repository.add(rule)
            await repository.add_example(
                StyleExample(
                    id=uuid4(),
                    owner_id=42,
                    profile_id=profile_id,
                    text="Пример.",
                    rubric="energy",
                    format="post",
                    tags=("sleep",),
                    risks=("medium",),
                    rating=5,
                )
            )
            fetched = await repository.get(owner_id=42, rule_id=rule.id)
            examples = await repository.approved_examples(
                owner_id=42, profile_id=profile_id
            )

    assert fetched == rule
    assert examples[0].risks == ("medium",)
    assert examples[0].tags == ("sleep",)


@pytest.mark.asyncio
async def test_style_repository_deduplicates_source_edit_and_proposed_rule(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    profile_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                StyleProfileModel(
                    id=profile_id,
                    owner_id=42,
                    version=profile_id.int % 2_000_000_000 + 1,
                    status="calibrating",
                )
            )
            repository = SqlAlchemyStyleRepository(
                session,
                SqlAlchemyAuditWriter(session, ensure_active=lambda: None),
                ensure_active=lambda: None,
            )
            edit = EditObservation(
                profile_id=profile_id,
                source_edit_id=uuid4(),
                rule_text="Правило.",
                pattern_key="opening:action",
                confirmed=True,
            )
            assert await repository.record_confirmed_edit(owner_id=42, edit=edit) == 1
            assert await repository.record_confirmed_edit(owner_id=42, edit=edit) == 1
            rule = _rule(profile_id=profile_id, pattern_key="opening:action")
            assert (await repository.add(rule)).id == rule.id
            assert (await repository.add(replace(rule, id=uuid4()))).id == rule.id


@pytest.mark.asyncio
async def test_style_repository_is_owner_scoped_and_atomic_for_confirmation_and_rejection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    profile_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                StyleProfileModel(
                    id=profile_id,
                    owner_id=42,
                    version=profile_id.int % 2_000_000_000 + 1,
                    status="active",
                    activated_at=datetime.now(UTC),
                )
            )
            audit = SqlAlchemyAuditWriter(session, ensure_active=lambda: None)
            repository = SqlAlchemyStyleRepository(session, audit, ensure_active=lambda: None)
            proposal = _rule(profile_id=profile_id)
            conflict = _rule(
                profile_id=profile_id,
                status=RuleStatus.ACTIVE,
                pattern_key="opening:previous-action",
            )
            await repository.add(proposal)
            await repository.add(conflict)
            with pytest.raises(SafeError) as caught:
                await repository.get(owner_id=999, rule_id=proposal.id)
            assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN

            confirmed = await repository.confirm_and_supersede(
                owner_id=42,
                proposal=proposal,
                conflict=conflict,
                confirmed_at=datetime.now(UTC),
            )
            rejected = _rule(profile_id=profile_id)
            await repository.add(rejected)
            await repository.reject(owner_id=42, proposal=rejected)
            events = await audit.for_object(owner_id=42, object_id=proposal.id)

    assert confirmed.id == proposal.id
    assert confirmed.status is RuleStatus.ACTIVE
    assert [event.event_type.value for event in events] == ["style.rule_decision"]
