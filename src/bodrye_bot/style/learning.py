from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

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

    async def save(self, rule: StyleRule) -> None: ...

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> list[StyleRule]: ...

    async def record_audit(self, *, action: str, rule_id: UUID) -> None: ...


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
            return existing
        proposal = StyleRule(
            id=uuid4(),
            owner_id=owner_id,
            profile_id=edit.profile_id,
            scope=RuleScope.FORMAT,
            text=edit.rule_text,
            origin=("explicit_remember" if edit.explicit_remember else "repeated_edit"),
            pattern_key=edit.pattern_key,
            status=RuleStatus.PROPOSED,
        )
        await self._repository.add(proposal)
        return proposal

    async def active_rules(
        self, *, owner_id: int, profile_id: UUID
    ) -> list[StyleRule]:
        self._owner_guard.authorize(owner_id)
        return await self._repository.active_rules(
            owner_id=owner_id, profile_id=profile_id
        )

    async def confirm_rule(
        self,
        *,
        owner_id: int,
        rule_id: UUID,
        conflict_rule_id: UUID | None = None,
    ) -> StyleRule:
        self._owner_guard.authorize(owner_id)
        proposal = await self._repository.get(owner_id=owner_id, rule_id=rule_id)
        if proposal.status is not RuleStatus.PROPOSED:
            raise ValueError("Only proposed rules can be confirmed")
        if conflict_rule_id is not None:
            conflict = await self._repository.get(
                owner_id=owner_id, rule_id=conflict_rule_id
            )
            if conflict.status is not RuleStatus.ACTIVE:
                raise ValueError("Only active rules can be superseded")
            superseded = replace(conflict, status=RuleStatus.SUPERSEDED)
            await self._repository.save(superseded)
            await self._repository.record_audit(
                action="superseded", rule_id=superseded.id
            )
        confirmed = replace(
            proposal,
            status=RuleStatus.ACTIVE,
            confirmed_at=self._clock(),
        )
        await self._repository.save(confirmed)
        await self._repository.record_audit(action="confirmed", rule_id=confirmed.id)
        return confirmed

    async def reject_rule(self, *, owner_id: int, rule_id: UUID) -> StyleRule:
        self._owner_guard.authorize(owner_id)
        proposal = await self._repository.get(owner_id=owner_id, rule_id=rule_id)
        if proposal.status is not RuleStatus.PROPOSED:
            raise ValueError("Only proposed rules can be rejected")
        rejected = replace(proposal, status=RuleStatus.REJECTED)
        await self._repository.save(rejected)
        await self._repository.record_audit(action="rejected", rule_id=rejected.id)
        return rejected
