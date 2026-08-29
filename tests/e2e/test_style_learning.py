from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import EditObservation, RuleScope, RuleStatus, StyleRule
from bodrye_bot.identity.service import OwnerGuard
from bodrye_bot.style.learning import StyleLearningService


class InMemoryStyleRuleRepository:
    def __init__(self) -> None:
        self.rules: dict[UUID, StyleRule] = {}
        self.confirmed_pattern_counts: dict[tuple[int, UUID, str], int] = {}
        self.audit: list[tuple[str, UUID]] = []
        self.calls: list[tuple[str, int]] = []
        self.fail_atomic_confirmation = False

    async def record_confirmed_edit(self, *, owner_id: int, edit: EditObservation) -> int:
        self.calls.append(("record_confirmed_edit", owner_id))
        key = (owner_id, edit.profile_id, edit.pattern_key)
        self.confirmed_pattern_counts[key] = self.confirmed_pattern_counts.get(key, 0) + 1
        return self.confirmed_pattern_counts[key]

    async def find_proposed_rule(
        self, *, owner_id: int, profile_id: UUID, pattern_key: str
    ) -> StyleRule | None:
        self.calls.append(("find_proposed_rule", owner_id))
        return next(
            (
                rule
                for rule in self.rules.values()
                if rule.owner_id == owner_id
                and rule.profile_id == profile_id
                and rule.pattern_key == pattern_key
                and rule.status is RuleStatus.PROPOSED
            ),
            None,
        )

    async def add(self, rule: StyleRule) -> None:
        self.calls.append(("add", rule.owner_id))
        self.rules[rule.id] = rule

    async def get(self, *, owner_id: int, rule_id: UUID) -> StyleRule:
        self.calls.append(("get", owner_id))
        rule = self.rules.get(rule_id)
        if rule is None or rule.owner_id != owner_id:
            raise SafeError.for_code(SafeErrorCode.OWNER_FORBIDDEN)
        return rule

    async def save(self, rule: StyleRule) -> None:
        self.calls.append(("save", rule.owner_id))
        self.rules[rule.id] = rule

    async def active_rules(self, *, owner_id: int, profile_id: UUID) -> list[StyleRule]:
        self.calls.append(("active_rules", owner_id))
        return [
            rule
            for rule in self.rules.values()
            if rule.owner_id == owner_id
            and rule.profile_id == profile_id
            and rule.status is RuleStatus.ACTIVE
        ]

    async def record_audit(self, *, action: str, rule_id: UUID) -> None:
        self.audit.append((action, rule_id))

    async def confirm_and_supersede(
        self,
        *,
        owner_id: int,
        proposal: StyleRule,
        conflict: StyleRule | None,
        confirmed_at: datetime,
    ) -> StyleRule:
        self.calls.append(("confirm_and_supersede", owner_id))
        if self.fail_atomic_confirmation:
            raise RuntimeError("database failure")
        confirmed = replace(
            proposal,
            status=RuleStatus.ACTIVE,
            confirmed_at=confirmed_at,
        )
        if conflict is not None:
            self.rules[conflict.id] = replace(conflict, status=RuleStatus.SUPERSEDED)
            self.audit.append(("superseded", conflict.id))
        self.rules[proposal.id] = confirmed
        self.audit.append(("confirmed", proposal.id))
        return confirmed


PROFILE_ID = uuid4()


def _edit(*, explicit_remember: bool = False) -> EditObservation:
    return EditObservation(
        profile_id=PROFILE_ID,
        rule_text="Начинать с конкретного действия.",
        pattern_key="opening:concrete-action",
        confirmed=True,
        explicit_remember=explicit_remember,
    )


@pytest.mark.asyncio
async def test_edit_never_activates_rule_without_owner_confirmation() -> None:
    repository = InMemoryStyleRuleRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)

    proposal = await service.propose_from_edit(owner_id=42, edit=_edit(explicit_remember=True))

    assert proposal is not None
    assert proposal.status is RuleStatus.PROPOSED
    assert await service.active_rules(owner_id=42, profile_id=PROFILE_ID) == []


@pytest.mark.asyncio
async def test_repeated_confirmed_edits_propose_then_confirmation_activates() -> None:
    repository = InMemoryStyleRuleRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)

    assert await service.propose_from_edit(owner_id=42, edit=_edit()) is None
    assert await service.propose_from_edit(owner_id=42, edit=_edit()) is None
    proposal = await service.propose_from_edit(owner_id=42, edit=_edit())
    assert proposal is not None
    assert proposal.status is RuleStatus.PROPOSED

    confirmed = await service.confirm_rule(owner_id=42, rule_id=proposal.id)

    assert confirmed.status is RuleStatus.ACTIVE
    assert confirmed.confirmed_at is not None
    assert repository.audit == [("confirmed", proposal.id)]


@pytest.mark.asyncio
async def test_confirming_conflict_supersedes_old_rule_and_rejection_is_auditable() -> None:
    repository = InMemoryStyleRuleRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)
    old = StyleRule(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        scope=RuleScope.HARD,
        text="Старое правило.",
        origin="owner_confirmation",
        pattern_key="opening:old",
        status=RuleStatus.ACTIVE,
        confirmed_at=datetime.now(UTC),
    )
    proposed = replace(
        old,
        id=uuid4(),
        text="Новое правило.",
        pattern_key="opening:new",
        status=RuleStatus.PROPOSED,
        confirmed_at=None,
    )
    rejected_proposal = replace(
        proposed,
        id=uuid4(),
        text="Отклоняемое правило.",
        pattern_key="opening:rejected",
    )
    repository.rules[old.id] = old
    repository.rules[proposed.id] = proposed
    repository.rules[rejected_proposal.id] = rejected_proposal

    await service.confirm_rule(owner_id=42, rule_id=proposed.id, conflict_rule_id=old.id)
    rejected = await service.reject_rule(owner_id=42, rule_id=rejected_proposal.id)

    assert repository.rules[old.id].status is RuleStatus.SUPERSEDED
    assert rejected.status is RuleStatus.REJECTED
    assert repository.audit == [
        ("superseded", old.id),
        ("confirmed", proposed.id),
        ("rejected", rejected_proposal.id),
    ]


@pytest.mark.asyncio
async def test_conflict_confirmation_rejects_unrelated_rule_before_atomic_write() -> None:
    repository = InMemoryStyleRuleRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)
    proposal = StyleRule(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        scope=RuleScope.FORMAT,
        format="post",
        text="Новое правило.",
        origin="edit",
        pattern_key="opening:new",
        status=RuleStatus.PROPOSED,
    )
    conflict = replace(
        proposal,
        id=uuid4(),
        profile_id=uuid4(),
        scope=RuleScope.HARD,
        status=RuleStatus.ACTIVE,
        confirmed_at=datetime.now(UTC),
    )
    repository.rules[proposal.id] = proposal
    repository.rules[conflict.id] = conflict

    with pytest.raises(SafeError) as caught:
        await service.confirm_rule(
            owner_id=42,
            rule_id=proposal.id,
            conflict_rule_id=conflict.id,
        )

    assert caught.value.code is SafeErrorCode.STYLE_PROFILE_NOT_READY
    assert ("confirm_and_supersede", 42) not in repository.calls
    assert repository.rules[proposal.id].status is RuleStatus.PROPOSED
    assert repository.rules[conflict.id].status is RuleStatus.ACTIVE
    assert repository.audit == []


@pytest.mark.asyncio
async def test_atomic_confirmation_failure_leaves_rule_states_and_audit_unchanged() -> None:
    repository = InMemoryStyleRuleRepository()
    repository.fail_atomic_confirmation = True
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)
    proposal = StyleRule(
        id=uuid4(),
        owner_id=42,
        profile_id=PROFILE_ID,
        scope=RuleScope.HARD,
        text="Новое правило.",
        origin="edit",
        pattern_key="opening:new",
        status=RuleStatus.PROPOSED,
    )
    conflict = replace(
        proposal,
        id=uuid4(),
        text="Старое правило.",
        status=RuleStatus.ACTIVE,
        confirmed_at=datetime.now(UTC),
    )
    repository.rules[proposal.id] = proposal
    repository.rules[conflict.id] = conflict

    with pytest.raises(RuntimeError, match="database failure"):
        await service.confirm_rule(
            owner_id=42,
            rule_id=proposal.id,
            conflict_rule_id=conflict.id,
        )

    assert repository.rules[proposal.id].status is RuleStatus.PROPOSED
    assert repository.rules[conflict.id].status is RuleStatus.ACTIVE
    assert repository.audit == []


@pytest.mark.asyncio
async def test_learning_fails_closed_before_mutating_a_contaminated_rule() -> None:
    class ContaminatedRepository(InMemoryStyleRuleRepository):
        async def get(self, *, owner_id: int, rule_id: UUID) -> StyleRule:
            self.calls.append(("get", owner_id))
            return self.rules[rule_id]

    repository = ContaminatedRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)
    foreign = StyleRule(
        id=uuid4(),
        owner_id=999,
        profile_id=PROFILE_ID,
        scope=RuleScope.HARD,
        text="Чужое правило.",
        origin="repository",
        pattern_key="opening:foreign",
        status=RuleStatus.PROPOSED,
    )
    repository.rules[foreign.id] = foreign

    with pytest.raises(SafeError) as caught:
        await service.confirm_rule(owner_id=42, rule_id=foreign.id)

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN
    assert ("confirm_and_supersede", 42) not in repository.calls
    assert repository.rules[foreign.id].status is RuleStatus.PROPOSED


@pytest.mark.asyncio
async def test_pattern_key_is_normalized_before_counting_repeated_confirmed_edits() -> None:
    repository = InMemoryStyleRuleRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)
    edits = tuple(
        EditObservation(
            profile_id=PROFILE_ID,
            rule_text="Начинать с действия.",
            pattern_key=key,
            confirmed=True,
        )
        for key in (
            "Opening:Concrete Action",
            "opening:concrete_action",
            " opening:concrete-action ",
        )
    )

    assert [await service.propose_from_edit(owner_id=42, edit=edit) for edit in edits[:-1]] == [
        None,
        None,
    ]
    proposal = await service.propose_from_edit(owner_id=42, edit=edits[-1])

    assert proposal is not None
    assert proposal.pattern_key == "opening:concrete-action"


def test_pattern_key_rejects_empty_or_untyped_values() -> None:
    with pytest.raises(ValueError, match="pattern key"):
        EditObservation(
            profile_id=PROFILE_ID,
            rule_text="Правило.",
            pattern_key="not-a-typed-key",
            confirmed=True,
        )


@pytest.mark.asyncio
async def test_foreign_owner_cannot_disclose_or_mutate_rule_before_repository_access() -> None:
    repository = InMemoryStyleRuleRepository()
    service = StyleLearningService(owner_guard=OwnerGuard(42), repository=repository)

    with pytest.raises(SafeError) as caught:
        await service.confirm_rule(owner_id=999, rule_id=uuid4())

    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN
    assert repository.calls == []
