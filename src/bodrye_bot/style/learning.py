from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import EditObservation, RuleScope, RuleStatus, StyleRule
from bodrye_bot.identity.service import OwnerGuard


class StyleRuleRepository(Protocol):
    async def record_confirmed_edit(
        self, *, owner_id: int, edit: EditObservation
    ) -> int: ...

    async def find_proposed_rule(
        self, *, owner_id: int, profile_id: UUID, pattern_key: str
    ) -> StyleRule | None: ...

    async def add(self, rule: StyleRule) -> None: ...

    async def get(self, *, owner_id: int, rule_id: UUID) -> StyleRule: ...

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> list[StyleRule]: ...

    async def reject(self, *, owner_id: int, proposal: StyleRule) -> StyleRule: ...

    async def confirm_and_supersede(
        self,
        *,
        owner_id: int,
        proposal: StyleRule,
        conflict: StyleRule | None,
        confirmed_at: datetime,
    ) -> StyleRule:
        """Atomically persist activation, any supersession, and both audit entries."""
        ...


class StyleLearningService:
    def __init__(
        self,
        *,
        owner_guard: OwnerGuard,
        repository: StyleRuleRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._owner_guard = owner_guard
        self._repository = repository
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    async def propose_from_edit(
        self, *, owner_id: int, edit: EditObservation
    ) -> StyleRule | None:
        self._owner_guard.authorize(owner_id)
        confirmed_count = 0
        if edit.confirmed:
            confirmed_count = await self._repository.record_confirmed_edit(
                owner_id=owner_id, edit=edit
            )
        if not edit.explicit_remember and confirmed_count < 3:
            return None
        existing = await self._repository.find_proposed_rule(
            owner_id=owner_id,
            profile_id=edit.profile_id,
            pattern_key=edit.pattern_key,
        )
        if existing is not None:
            _validate_rule(
                existing,
                owner_id=owner_id,
                profile_id=edit.profile_id,
                pattern_key=edit.pattern_key,
                status=RuleStatus.PROPOSED,
            )
            return existing
        proposal = StyleRule(
            id=uuid4(),
            owner_id=owner_id,
            profile_id=edit.profile_id,
            scope=edit.scope,
            text=edit.rule_text,
            origin=("explicit_remember" if edit.explicit_remember else "repeated_edit"),
            pattern_key=edit.pattern_key,
            status=RuleStatus.PROPOSED,
            format=edit.format,
            risks=edit.risks,
            tags=edit.tags,
        )
        await self._repository.add(proposal)
        return proposal

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> list[StyleRule]:
        self._owner_guard.authorize(owner_id)
        rules = await self._repository.active_rules(
            owner_id=owner_id, profile_id=profile_id
        )
        for rule in rules:
            _validate_rule(rule, owner_id=owner_id, profile_id=profile_id)
        return rules

    async def confirm_rule(
        self,
        *,
        owner_id: int,
        rule_id: UUID,
        conflict_rule_id: UUID | None = None,
    ) -> StyleRule:
        self._owner_guard.authorize(owner_id)
        proposal = await self._repository.get(owner_id=owner_id, rule_id=rule_id)
        _validate_rule(proposal, owner_id=owner_id, rule_id=rule_id)
        if proposal.status is not RuleStatus.PROPOSED:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        conflict: StyleRule | None = None
        if conflict_rule_id is not None:
            conflict = await self._repository.get(
                owner_id=owner_id, rule_id=conflict_rule_id
            )
            _validate_rule(
                conflict,
                owner_id=owner_id,
                profile_id=proposal.profile_id,
                rule_id=conflict_rule_id,
            )
            if conflict.status is not RuleStatus.ACTIVE:
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
            if conflict.scope is not proposal.scope:
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
            if (
                proposal.scope is RuleScope.FORMAT
                and conflict.format != proposal.format
            ):
                raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        confirmed = await self._repository.confirm_and_supersede(
            owner_id=owner_id,
            proposal=proposal,
            conflict=conflict,
            confirmed_at=self._clock(),
        )
        _validate_rule(
            confirmed,
            owner_id=owner_id,
            profile_id=proposal.profile_id,
            rule_id=proposal.id,
            pattern_key=proposal.pattern_key,
            scope=proposal.scope,
            status=RuleStatus.ACTIVE,
        )
        if confirmed.confirmed_at is None:
            raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
        return confirmed

    async def reject_rule(self, *, owner_id: int, rule_id: UUID) -> StyleRule:
        self._owner_guard.authorize(owner_id)
        proposal = await self._repository.get(owner_id=owner_id, rule_id=rule_id)
        _validate_rule(proposal, owner_id=owner_id, rule_id=rule_id)
        if proposal.status is not RuleStatus.PROPOSED:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        rejected = await self._repository.reject(owner_id=owner_id, proposal=proposal)
        _validate_rule(
            rejected,
            owner_id=owner_id,
            profile_id=proposal.profile_id,
            rule_id=proposal.id,
            pattern_key=proposal.pattern_key,
            scope=proposal.scope,
            status=RuleStatus.REJECTED,
        )
        return rejected


def _validate_rule(
    rule: StyleRule,
    *,
    owner_id: int,
    profile_id: UUID | None = None,
    rule_id: UUID | None = None,
    pattern_key: str | None = None,
    scope: RuleScope | None = None,
    status: RuleStatus | None = None,
) -> None:
    if rule.owner_id != owner_id or (rule_id is not None and rule.id != rule_id):
        raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
    if profile_id is not None and rule.profile_id != profile_id:
        raise SafeError.for_code(SafeErrorCode.STYLE_PROFILE_NOT_READY)
    if (
        (pattern_key is not None and rule.pattern_key != pattern_key)
        or (scope is not None and rule.scope is not scope)
        or (status is not None and rule.status is not status)
    ):
        raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
