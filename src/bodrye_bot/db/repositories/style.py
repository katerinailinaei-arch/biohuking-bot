from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bodrye_bot.db.models import StyleEditObservation
from bodrye_bot.db.models import StyleExample as StyleExampleModel
from bodrye_bot.db.models import StyleProfile as StyleProfileModel
from bodrye_bot.db.models import StyleRule as StyleRuleModel
from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import (
    EditObservation,
    RuleScope,
    RuleStatus,
    StyleExample,
    StyleProfile,
    StyleProfileStatus,
    StyleRule,
)
from bodrye_bot.domain.workflow import Actor
from bodrye_bot.operations.audit import (
    AuditEntry,
    AuditEventType,
    AuditObjectType,
    SqlAlchemyAuditWriter,
)


class SqlAlchemyStyleRepository:
    """Owner-qualified style persistence bound to one active transaction."""

    def __init__(
        self,
        session: AsyncSession,
        audit: SqlAlchemyAuditWriter,
        *,
        ensure_active: Callable[[], None],
    ) -> None:
        self._session = session
        self._audit = audit
        self._ensure_active = ensure_active

    async def get_profile(self, *, owner_id: int, profile_id: UUID) -> StyleProfile:
        self._ensure_active()
        profile = await self._session.scalar(
            select(StyleProfileModel).where(
                StyleProfileModel.id == profile_id,
                StyleProfileModel.owner_id == owner_id,
            )
        )
        if profile is None:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        return StyleProfile(
            id=profile.id,
            owner_id=profile.owner_id,
            version=profile.version,
            status=StyleProfileStatus(profile.status),
            activated_at=profile.activated_at,
        )

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleRule, ...]:
        self._ensure_active()
        result = await self._session.execute(
            select(StyleRuleModel)
            .where(
                StyleRuleModel.owner_id == owner_id,
                StyleRuleModel.profile_id == profile_id,
                StyleRuleModel.status == "active",
            )
            .order_by(StyleRuleModel.rule_text, StyleRuleModel.id)
        )
        return tuple(_to_rule(model) for model in result.scalars())

    async def approved_examples(
        self, *, owner_id: int, profile_id: UUID
    ) -> tuple[StyleExample, ...]:
        self._ensure_active()
        result = await self._session.execute(
            select(StyleExampleModel)
            .where(
                StyleExampleModel.owner_id == owner_id,
                StyleExampleModel.profile_id == profile_id,
                StyleExampleModel.rating.is_not(None),
            )
            .order_by(StyleExampleModel.text, StyleExampleModel.id)
        )
        return tuple(_to_example(model) for model in result.scalars())

    async def record_confirmed_edit(
        self, *, owner_id: int, edit: EditObservation
    ) -> int:
        self._ensure_active()
        await self.ensure_profile(owner_id=owner_id, profile_id=edit.profile_id)
        existing = await self._existing_edit(owner_id=owner_id, edit=edit)
        if existing is not None:
            if existing.pattern_key != edit.pattern_key:
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
            return await self._edit_count(owner_id=owner_id, edit=edit)
        try:
            async with self._session.begin_nested():
                self._session.add(
                    StyleEditObservation(
                        owner_id=owner_id,
                        profile_id=edit.profile_id,
                        pattern_key=edit.pattern_key,
                        source_edit_id=edit.source_edit_id,
                    )
                )
                await self._session.flush()
        except IntegrityError:
            # A concurrent insert can win while this savepoint is waiting on
            # the unique key. Re-query the exact idempotency tuple after the
            # savepoint has rolled back; never expose the driver exception.
            existing = await self._existing_edit(
                owner_id=owner_id, edit=edit, exact_pattern=True
            )
            if existing is not None:
                return await self._edit_count(owner_id=owner_id, edit=edit)
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION) from None
        return await self._edit_count(owner_id=owner_id, edit=edit)

    async def _existing_edit(
        self,
        *,
        owner_id: int,
        edit: EditObservation,
        exact_pattern: bool = False,
    ) -> StyleEditObservation | None:
        query = select(StyleEditObservation).where(
            StyleEditObservation.owner_id == owner_id,
            StyleEditObservation.profile_id == edit.profile_id,
            StyleEditObservation.source_edit_id == edit.source_edit_id,
        )
        if exact_pattern:
            query = query.where(StyleEditObservation.pattern_key == edit.pattern_key)
        return cast(StyleEditObservation | None, await self._session.scalar(query))

    async def _edit_count(self, *, owner_id: int, edit: EditObservation) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(StyleEditObservation).where(
                StyleEditObservation.owner_id == owner_id,
                StyleEditObservation.profile_id == edit.profile_id,
                StyleEditObservation.pattern_key == edit.pattern_key,
            )
        )
        return cast(int, count)

    async def ensure_profile(self, *, owner_id: int, profile_id: UUID) -> None:
        self._ensure_active()
        exists = await self._session.scalar(
            select(StyleProfileModel.id).where(
                StyleProfileModel.id == profile_id, StyleProfileModel.owner_id == owner_id
            )
        )
        if exists is None:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)

    async def find_proposed_rule(
        self, *, owner_id: int, profile_id: UUID, pattern_key: str
    ) -> StyleRule | None:
        self._ensure_active()
        model = await self._session.scalar(
            select(StyleRuleModel).where(
                StyleRuleModel.owner_id == owner_id,
                StyleRuleModel.profile_id == profile_id,
                StyleRuleModel.pattern_key == pattern_key,
                StyleRuleModel.status == "proposed",
            )
        )
        return _to_rule(model) if model is not None else None

    async def add(self, rule: StyleRule) -> StyleRule:
        self._ensure_active()
        await self.ensure_profile(owner_id=rule.owner_id, profile_id=rule.profile_id)
        existing = await self.find_proposed_rule(
            owner_id=rule.owner_id, profile_id=rule.profile_id, pattern_key=rule.pattern_key
        )
        if existing is not None:
            return existing
        try:
            async with self._session.begin_nested():
                self._session.add(_from_rule(rule))
                await self._session.flush()
        except IntegrityError:
            existing = await self.find_proposed_rule(
                owner_id=rule.owner_id,
                profile_id=rule.profile_id,
                pattern_key=rule.pattern_key,
            )
            if existing is None:
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION) from None
            return existing
        return rule

    async def add_example(self, example: StyleExample) -> None:
        self._ensure_active()
        await self.ensure_profile(owner_id=example.owner_id, profile_id=example.profile_id)
        try:
            async with self._session.begin_nested():
                self._session.add(
                    StyleExampleModel(
                        id=example.id,
                        owner_id=example.owner_id,
                        profile_id=example.profile_id,
                        text=example.text,
                        rubric=example.rubric,
                        format=example.format,
                        tags=list(example.tags),
                        risks=list(example.risks),
                        rating=example.rating,
                        is_holdout=False,
                    )
                )
                await self._session.flush()
        except IntegrityError:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION) from None

    async def get(self, *, owner_id: int, rule_id: UUID) -> StyleRule:
        self._ensure_active()
        model = await self._session.scalar(
            select(StyleRuleModel).where(
                StyleRuleModel.id == rule_id, StyleRuleModel.owner_id == owner_id
            )
        )
        if model is None or model.id != rule_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        return _to_rule(model)

    async def active_rules_for_learning(
        self, *, owner_id: int, profile_id: UUID
    ) -> list[StyleRule]:
        return list(await self.active_rules(owner_id=owner_id, profile_id=profile_id))

    async def confirm_and_supersede(
        self,
        *,
        owner_id: int,
        proposal: StyleRule,
        conflict: StyleRule | None,
        confirmed_at: datetime,
    ) -> StyleRule:
        self._ensure_active()
        async with self._session.begin_nested():
            stored = await self._locked_rule(owner_id=owner_id, rule_id=proposal.id)
            _require_same_rule(stored, proposal)
            if conflict is not None:
                stored_conflict = await self._locked_rule(
                    owner_id=owner_id, rule_id=conflict.id
                )
                _require_same_rule(stored_conflict, conflict)
                stored_conflict.status = "superseded"
                await self._audit.record(_audit_entry(owner_id, "superseded", conflict.id))
            stored.status = "active"
            stored.confirmed_at = confirmed_at
            await self._session.flush()
            await self._audit.record(_audit_entry(owner_id, "confirmed", proposal.id))
            result = _to_rule(stored)
        return result

    async def reject(self, *, owner_id: int, proposal: StyleRule) -> StyleRule:
        self._ensure_active()
        async with self._session.begin_nested():
            stored = await self._locked_rule(owner_id=owner_id, rule_id=proposal.id)
            _require_same_rule(stored, proposal)
            stored.status = "rejected"
            await self._session.flush()
            await self._audit.record(_audit_entry(owner_id, "rejected", proposal.id))
            result = _to_rule(stored)
        return result

    async def _locked_rule(self, *, owner_id: int, rule_id: UUID) -> StyleRuleModel:
        model = await self._session.scalar(
            select(StyleRuleModel)
            .where(StyleRuleModel.id == rule_id, StyleRuleModel.owner_id == owner_id)
            .with_for_update()
        )
        if model is None:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        return model


def _from_rule(rule: StyleRule) -> StyleRuleModel:
    return StyleRuleModel(
        id=rule.id,
        owner_id=rule.owner_id,
        profile_id=rule.profile_id,
        scope=rule.scope.value,
        rule_text=rule.text,
        origin=rule.origin,
        status=rule.status.value,
        confirmed_at=rule.confirmed_at,
        format=rule.format,
        risks=list(rule.risks),
        tags=list(rule.tags),
        pattern_key=rule.pattern_key,
    )


def _to_rule(model: StyleRuleModel) -> StyleRule:
    return StyleRule(
        id=model.id,
        owner_id=model.owner_id,
        profile_id=model.profile_id,
        scope=RuleScope(model.scope),
        text=model.rule_text,
        origin=model.origin,
        status=RuleStatus(model.status),
        confirmed_at=model.confirmed_at,
        format=model.format,
        risks=tuple(model.risks),
        tags=tuple(model.tags),
        pattern_key=model.pattern_key,
    )


def _to_example(model: StyleExampleModel) -> StyleExample:
    return StyleExample(
        id=model.id,
        owner_id=model.owner_id,
        profile_id=model.profile_id,
        text=model.text,
        rubric=model.rubric,
        format=model.format,
        tags=tuple(model.tags),
        risks=tuple(model.risks),
        rating=model.rating,
    )


def _require_same_rule(stored: StyleRuleModel, expected: StyleRule) -> None:
    actual = _to_rule(stored)
    if (
        actual.id != expected.id
        or actual.owner_id != expected.owner_id
        or actual.profile_id != expected.profile_id
        or actual.scope is not expected.scope
        or actual.pattern_key != expected.pattern_key
        or actual.status is not expected.status
        or actual.text != expected.text
        or actual.origin != expected.origin
        or actual.format != expected.format
        or actual.risks != expected.risks
        or actual.tags != expected.tags
        or actual.confirmed_at != expected.confirmed_at
    ):
        raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)


def _audit_entry(owner_id: int, action: str, rule_id: UUID) -> AuditEntry:
    return AuditEntry(
        owner_id=owner_id,
        event_type=AuditEventType.RULE_DECISION_RECORDED,
        actor=Actor.OWNER,
        object_type=AuditObjectType.STYLE_RULE,
        object_id=rule_id,
        metadata={"action": action},
    )
