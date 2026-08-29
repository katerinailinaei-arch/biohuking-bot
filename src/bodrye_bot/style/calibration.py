from __future__ import annotations

from bodrye_bot.domain.style import (
    CalibrationFeedback,
    CalibrationSession,
    CalibrationTopic,
    HoldoutPost,
)


class CalibrationService:
    def start(self, topics: tuple[CalibrationTopic, ...]) -> CalibrationSession:
        if not 8 <= len(topics) <= 10:
            raise ValueError("Calibration requires 8 to 10 topics")
        if len({topic.id for topic in topics}) != len(topics):
            raise ValueError("Calibration topics must be unique")
        if len({topic.risk for topic in topics}) < 2:
            raise ValueError("Calibration topics must be risk-diverse")
        for topic in topics:
            if len(topic.short_variants) != 3:
                raise ValueError("Each topic requires exactly three short variants")
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
            raise ValueError("Unknown calibration topic")
        indices = tuple(range(len(topic.short_variants)))
        if selected_variant is not None and selected_variant not in indices:
            raise ValueError("Unknown selected variant")
        if any(index not in indices for index in rejected_variants):
            raise ValueError("Unknown rejected variant")
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
            raise ValueError("Exactly three holdouts are required")
        topic_ids = {holdout.topic_id for holdout in holdouts}
        if len(topic_ids) != 3:
            raise ValueError("Holdout topics must be unique")
        calibrated_topic_ids = {topic.id for topic in calibration.topics}
        if topic_ids & calibrated_topic_ids:
            raise ValueError("Holdout topics must be unseen")
        if any(not holdout.body.strip() for holdout in holdouts):
            raise ValueError("Holdouts must be full posts")
