from __future__ import annotations

import pytest

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.manual_post import ManualPost, ManualPostPolicy, ManualPostStatus
from bodrye_bot.editorial.template_draft import TemplateDraftWriter


def _draft(*, body: str = "Черновик про сон.") -> ManualPost:
    return ManualPost.create(owner_id=42, topic="сон", body=body)


def test_template_draft_is_bounded_and_asks_keti_to_check_facts() -> None:
    body = TemplateDraftWriter().write("Бессонница после 35")

    assert "Бессонница после 35" in body
    assert "не медицинская рекомендация" in body.lower()
    assert len(body) <= 3_800
    assert "я доказала" not in body.lower()


def test_cannot_approve_or_publish_before_owner_marks_reviewed() -> None:
    policy = ManualPostPolicy()
    draft = _draft()

    with pytest.raises(SafeError) as blocked_approve:
        policy.approve(draft)
    with pytest.raises(SafeError) as blocked_publish:
        policy.publish(draft)

    assert blocked_approve.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE
    assert blocked_publish.value.code is SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE
    assert draft.status is ManualPostStatus.DRAFT


def test_review_then_approve_then_publish_binds_the_same_hash() -> None:
    policy = ManualPostPolicy()
    draft = _draft()

    reviewed = policy.mark_reviewed(draft)
    approved = policy.approve(reviewed)
    published = policy.publish(approved)

    assert reviewed.status is ManualPostStatus.OWNER_REVIEWED
    assert approved.status is ManualPostStatus.APPROVED
    assert published.status is ManualPostStatus.PUBLISHED
    assert published.content_hash == draft.content_hash
    assert published.approval_hash == draft.content_hash


def test_changed_body_invalidates_stale_review() -> None:
    policy = ManualPostPolicy()
    reviewed = policy.mark_reviewed(_draft())
    edited = reviewed.with_body("Другой текст после правки.")

    with pytest.raises(SafeError) as caught:
        policy.approve(edited)

    assert caught.value.code is SafeErrorCode.APPROVAL_STALE
    assert edited.status is ManualPostStatus.DRAFT
    assert edited.id == reviewed.id
