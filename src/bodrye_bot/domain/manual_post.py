from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID, uuid4

from bodrye_bot.domain.common import content_hash
from bodrye_bot.domain.errors import SafeError, SafeErrorCode


class ManualPostStatus(StrEnum):
    DRAFT = "draft"
    OWNER_REVIEWED = "owner_reviewed"
    APPROVED = "approved"
    PUBLISHED = "published"


@dataclass(frozen=True)
class ManualPost:
    id: UUID
    owner_id: int
    topic: str
    body: str
    content_hash: str
    status: ManualPostStatus
    version: int
    review_hash: str | None = None
    approval_hash: str | None = None

    @classmethod
    def create(cls, *, owner_id: int, topic: str, body: str) -> ManualPost:
        cleaned = body.strip()
        if not cleaned:
            raise ValueError("Draft body must not be empty")
        return cls(
            id=uuid4(),
            owner_id=owner_id,
            topic=topic.strip(),
            body=cleaned,
            content_hash=content_hash(cleaned),
            status=ManualPostStatus.DRAFT,
            version=1,
        )

    def with_body(self, body: str) -> ManualPost:
        cleaned = body.strip()
        digest = content_hash(cleaned)
        if digest == self.content_hash and self.body == cleaned:
            return self
        return replace(
            self,
            body=cleaned,
            content_hash=digest,
            status=ManualPostStatus.DRAFT,
            approval_hash=None,
            version=self.version + 1,
        )


class ManualPostPolicy:
    """Owner-reviewed posts skip automated medical classification."""

    def mark_reviewed(self, post: ManualPost) -> ManualPost:
        if post.status is ManualPostStatus.PUBLISHED:
            raise SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)
        return replace(
            post,
            status=ManualPostStatus.OWNER_REVIEWED,
            review_hash=post.content_hash,
            approval_hash=None,
            version=post.version + 1,
        )

    def approve(self, post: ManualPost) -> ManualPost:
        if post.review_hash is not None and post.review_hash != post.content_hash:
            raise SafeError.for_code(SafeErrorCode.APPROVAL_STALE)
        if post.status is not ManualPostStatus.OWNER_REVIEWED:
            raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)
        return replace(
            post,
            status=ManualPostStatus.APPROVED,
            approval_hash=post.content_hash,
            version=post.version + 1,
        )

    def publish(self, post: ManualPost) -> ManualPost:
        if post.status is ManualPostStatus.APPROVED:
            if post.approval_hash != post.content_hash:
                raise SafeError.for_code(SafeErrorCode.APPROVAL_STALE)
            return replace(post, status=ManualPostStatus.PUBLISHED, version=post.version + 1)
        if post.status is ManualPostStatus.OWNER_REVIEWED:
            approved = self.approve(post)
            return self.publish(approved)
        raise SafeError.for_code(SafeErrorCode.MEDICAL_REVIEW_INCOMPLETE)
