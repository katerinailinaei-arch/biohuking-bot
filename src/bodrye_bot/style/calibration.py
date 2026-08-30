from __future__ import annotations

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.domain.style import (
    CalibrationFeedback,
    CalibrationSession,
    CalibrationTopic,
    HoldoutPost,
)


def _invalid() -> SafeError:
    return SafeError.for_code(SafeErrorCode.INVALID_TRANSITION)


class CalibrationService:
    def start(self, topics: tuple[CalibrationTopic, ...]) -> CalibrationSession:
        if not 8 <= len(topics) <= 10:
            raise _invalid()
        if len({topic.id for topic in topics}) != len(topics):
            raise _invalid()
        if len({topic.risk for topic in topics}) < 2:
            raise _invalid()
        for topic in topics:
            if len(topic.short_variants) != 3:
                raise _invalid()
        return CalibrationSession(topics=topics)

    def record_feedback(
        self,
        calibration: CalibrationSession,
        *,
        topic_id: str,
        selected_variant: int | None,
        rejected_variants: tuple[int, ...],
        edit: str | None,
    ) -> CalibrationFeedback:
        topic = next((item for item in calibration.topics if item.id == topic_id), None)
        if topic is None:
            raise _invalid()
        indices = tuple(range(len(topic.short_variants)))
        if selected_variant is not None and selected_variant not in indices:
            raise _invalid()
        if any(index not in indices for index in rejected_variants):
            raise _invalid()
        if len(set(rejected_variants)) != len(rejected_variants):
            raise _invalid()
        if selected_variant is not None and selected_variant in rejected_variants:
            raise _invalid()
        if selected_variant is None and not rejected_variants and not (edit or "").strip():
            raise _invalid()
        return CalibrationFeedback(
            topic_id=topic_id,
            selected_variant=selected_variant,
            rejected_variants=rejected_variants,
            edit=edit,
        )

    def register_holdouts(
        self,
        calibration: CalibrationSession,
        holdouts: tuple[HoldoutPost, ...],
    ) -> None:
        if len(holdouts) != 3:
            raise _invalid()
        topic_ids = {holdout.topic_id for holdout in holdouts}
        if len(topic_ids) != 3:
            raise _invalid()
        calibrated_topic_ids = {topic.id for topic in calibration.topics}
        if topic_ids & calibrated_topic_ids:
            raise _invalid()
        if any(not holdout.body.strip() for holdout in holdouts):
            raise _invalid()
