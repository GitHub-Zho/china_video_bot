"""Effective Understanding derivation and typed human-review drafts."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Iterable

from .models import (
    HumanReviewOverlay,
    SemanticSegment,
    VideoUnderstandingV1,
)


_SEGMENT_FIELDS = (
    "start_sec",
    "end_sec",
    "semantic_summary",
    "visual_description",
)


def effective_global_summary(payload: VideoUnderstandingV1) -> str:
    """Return the reviewed summary using lock, override, baseline precedence."""
    if not isinstance(payload, VideoUnderstandingV1):
        raise ValueError("payload must be a VideoUnderstandingV1")
    review = payload.human_review
    return review.global_locks.get(
        "global_summary",
        review.global_overrides.get(
            "global_summary", payload.ai_baseline.global_summary
        ),
    )


def effective_segments(
    payload: VideoUnderstandingV1,
) -> tuple[SemanticSegment, ...]:
    """Derive the effective segment tree without persisting a duplicate copy."""
    if not isinstance(payload, VideoUnderstandingV1):
        raise ValueError("payload must be a VideoUnderstandingV1")
    review = payload.human_review
    removed = set(review.removed_segment_ids)
    stored = [
        item for item in payload.ai_baseline.segments if item.segment_id not in removed
    ] + list(review.added_segments)
    derived: list[SemanticSegment] = []
    for item in stored:
        overrides = review.segment_overrides.get(item.segment_id, {})
        locks = review.segment_locks.get(item.segment_id, {})
        values = {
            field_name: locks.get(
                field_name, overrides.get(field_name, getattr(item, field_name))
            )
            for field_name in _SEGMENT_FIELDS
        }
        derived.append(
            SemanticSegment(
                segment_id=item.segment_id,
                start_sec=values["start_sec"],
                end_sec=values["end_sec"],
                semantic_summary=values["semantic_summary"],
                visual_description=values["visual_description"],
                source_salience=item.source_salience,
                confidence=item.confidence,
            )
        )
    return tuple(
        sorted(
            derived,
            key=lambda item: (item.start_sec, item.end_sec, item.segment_id),
        )
    )


class UnderstandingDraft:
    """Mutable review overlay that produces a new complete immutable payload."""

    def __init__(self, base: VideoUnderstandingV1) -> None:
        if not isinstance(base, VideoUnderstandingV1):
            raise ValueError("base must be a VideoUnderstandingV1")
        self._base = base
        review = base.human_review
        self._global_overrides = dict(review.global_overrides)
        self._global_locks = dict(review.global_locks)
        self._removed_segment_ids = list(review.removed_segment_ids)
        self._added_segments = list(review.added_segments)
        self._segment_overrides = {
            segment_id: dict(values)
            for segment_id, values in review.segment_overrides.items()
        }
        self._segment_locks = {
            segment_id: dict(values)
            for segment_id, values in review.segment_locks.items()
        }

    @classmethod
    def from_payload(cls, payload: VideoUnderstandingV1) -> "UnderstandingDraft":
        return cls(payload)

    def _overlay(self) -> HumanReviewOverlay:
        return HumanReviewOverlay(
            global_overrides=self._global_overrides,
            global_locks=self._global_locks,
            removed_segment_ids=tuple(self._removed_segment_ids),
            added_segments=tuple(self._added_segments),
            segment_overrides=self._segment_overrides,
            segment_locks=self._segment_locks,
        )

    def to_payload(self) -> VideoUnderstandingV1:
        return VideoUnderstandingV1(
            source_duration_sec=self._base.source_duration_sec,
            ai_baseline=self._base.ai_baseline,
            human_review=self._overlay(),
        )

    def validate(self) -> None:
        self.to_payload()

    def _effective_segment(self, segment_id: str) -> SemanticSegment:
        for item in effective_segments(self.to_payload()):
            if item.segment_id == segment_id:
                return item
        raise ValueError(f"segment is not effective: {segment_id}")

    def set_global_summary(self, value: str) -> "UnderstandingDraft":
        candidate = replace(
            self._base.ai_baseline,
            global_summary=value,
        ).global_summary
        self._global_overrides["global_summary"] = candidate
        if "global_summary" in self._global_locks:
            self._global_locks["global_summary"] = candidate
        return self

    def clear_global_summary_override(self) -> "UnderstandingDraft":
        self._global_overrides.pop("global_summary", None)
        return self

    def lock_global_summary(self) -> "UnderstandingDraft":
        self._global_locks["global_summary"] = effective_global_summary(
            self.to_payload()
        )
        return self

    def unlock_global_summary(self) -> "UnderstandingDraft":
        self._global_locks.pop("global_summary", None)
        return self

    @staticmethod
    def _require_segment_field(field_name: str) -> None:
        if field_name not in _SEGMENT_FIELDS:
            raise ValueError(f"field is not reviewable: {field_name}")

    def set_segment_field(
        self, segment_id: str, field_name: str, value: object
    ) -> "UnderstandingDraft":
        self._require_segment_field(field_name)
        current = self._effective_segment(segment_id)
        normalized = getattr(replace(current, **{field_name: value}), field_name)
        self._segment_overrides.setdefault(segment_id, {})[field_name] = normalized
        if field_name in self._segment_locks.get(segment_id, {}):
            self._segment_locks[segment_id][field_name] = normalized
        return self

    def clear_segment_override(
        self, segment_id: str, field_name: str
    ) -> "UnderstandingDraft":
        self._require_segment_field(field_name)
        values = self._segment_overrides.get(segment_id)
        if values is not None:
            values.pop(field_name, None)
            if not values:
                self._segment_overrides.pop(segment_id, None)
        return self

    def lock_segment_field(
        self, segment_id: str, field_name: str
    ) -> "UnderstandingDraft":
        self._require_segment_field(field_name)
        value = getattr(self._effective_segment(segment_id), field_name)
        self._segment_locks.setdefault(segment_id, {})[field_name] = value
        if field_name in self._segment_overrides.get(segment_id, {}):
            self._segment_overrides[segment_id][field_name] = value
        return self

    def unlock_segment_field(
        self, segment_id: str, field_name: str
    ) -> "UnderstandingDraft":
        self._require_segment_field(field_name)
        values = self._segment_locks.get(segment_id)
        if values is not None:
            values.pop(field_name, None)
            if not values:
                self._segment_locks.pop(segment_id, None)
        return self

    def lock_segment(self, segment_id: str) -> "UnderstandingDraft":
        current = self._effective_segment(segment_id)
        locks = self._segment_locks.setdefault(segment_id, {})
        overrides = self._segment_overrides.get(segment_id, {})
        for field_name in _SEGMENT_FIELDS:
            value = getattr(current, field_name)
            locks[field_name] = value
            if field_name in overrides:
                overrides[field_name] = value
        return self

    def unlock_segment(self, segment_id: str) -> "UnderstandingDraft":
        self._segment_locks.pop(segment_id, None)
        return self

    @staticmethod
    def _new_segment_id() -> str:
        return f"seg_{uuid.uuid4()}"

    def add_segment(
        self,
        start_sec: float,
        end_sec: float,
        semantic_summary: str,
        visual_description: str,
    ) -> str:
        segment_id = self._new_segment_id()
        candidate = SemanticSegment(
            segment_id=segment_id,
            start_sec=start_sec,
            end_sec=end_sec,
            semantic_summary=semantic_summary,
            visual_description=visual_description,
            source_salience=None,
            confidence=None,
        )
        self._added_segments.append(candidate)
        try:
            self.validate()
        except Exception:
            self._added_segments.pop()
            raise
        return segment_id

    def remove_segment(self, segment_id: str) -> "UnderstandingDraft":
        baseline_ids = {item.segment_id for item in self._base.ai_baseline.segments}
        if segment_id in baseline_ids:
            if segment_id not in self._removed_segment_ids:
                self._removed_segment_ids.append(segment_id)
            return self
        for index, item in enumerate(self._added_segments):
            if item.segment_id == segment_id:
                self._added_segments.pop(index)
                self._segment_overrides.pop(segment_id, None)
                self._segment_locks.pop(segment_id, None)
                return self
        raise ValueError(f"unknown segment_id: {segment_id}")

    def restore_baseline_segment(self, segment_id: str) -> "UnderstandingDraft":
        baseline_ids = {item.segment_id for item in self._base.ai_baseline.segments}
        if segment_id not in baseline_ids:
            raise ValueError("only baseline segments can be restored")
        self._removed_segment_ids = [
            value for value in self._removed_segment_ids if value != segment_id
        ]
        self.validate()
        return self

    def split_segment(self, segment_id: str, split_sec: float) -> tuple[str, str]:
        current = self._effective_segment(segment_id)
        if isinstance(split_sec, bool) or not current.start_sec < split_sec < current.end_sec:
            raise ValueError("split_sec must lie strictly inside the segment")
        self.remove_segment(segment_id)
        left_id = self.add_segment(
            current.start_sec,
            split_sec,
            current.semantic_summary,
            current.visual_description,
        )
        right_id = self.add_segment(
            split_sec,
            current.end_sec,
            current.semantic_summary,
            current.visual_description,
        )
        return left_id, right_id

    def merge_segments(
        self,
        segment_ids: Iterable[str],
        *,
        semantic_summary: str,
        visual_description: str,
    ) -> str:
        requested = tuple(segment_ids)
        if len(requested) < 2 or len(set(requested)) != len(requested):
            raise ValueError("merge requires two or more distinct segments")
        current = effective_segments(self.to_payload())
        index = {item.segment_id: position for position, item in enumerate(current)}
        if any(segment_id not in index for segment_id in requested):
            raise ValueError("merge requires effective segments")
        positions = sorted(index[segment_id] for segment_id in requested)
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("merge requires consecutive segments")
        selected = [current[position] for position in positions]
        for item in selected:
            self.remove_segment(item.segment_id)
        return self.add_segment(
            min(item.start_sec for item in selected),
            max(item.end_sec for item in selected),
            semantic_summary,
            visual_description,
        )

