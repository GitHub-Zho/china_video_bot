"""Strict immutable payload models for the locked Understanding v1 schemas."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Mapping


_LANGUAGE_PATTERN = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")
_GLOBAL_REVIEW_FIELDS = frozenset({"global_summary"})
_SEGMENT_REVIEW_FIELDS = frozenset(
    {"start_sec", "end_sec", "semantic_summary", "visual_description"}
)


def _strict_mapping(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    keys = set(value)
    missing = sorted(fields - keys)
    unknown = sorted(keys - fields)
    if missing:
        raise ValueError(f"{path} missing required fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
    return value


def _finite_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{path} must be a finite number")
    return normalized


def _positive_number(value: object, path: str) -> float:
    normalized = _finite_number(value, path)
    if normalized <= 0:
        raise ValueError(f"{path} must be greater than zero")
    return normalized


def _non_negative_number(value: object, path: str) -> float:
    normalized = _finite_number(value, path)
    if normalized < 0:
        raise ValueError(f"{path} must be at least zero")
    return normalized


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, path)


def _optional_score(value: object, path: str) -> float | None:
    if value is None:
        return None
    normalized = _finite_number(value, path)
    if not 0 <= normalized <= 1:
        raise ValueError(f"{path} must be between zero and one")
    return normalized


def _segment_id(value: object, path: str = "segment_id") -> str:
    if not isinstance(value, str) or not value.startswith("seg_"):
        raise ValueError(f"{path} must be seg_<UUIDv4>")
    suffix = value[4:]
    try:
        parsed = uuid.UUID(suffix)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{path} must be seg_<UUIDv4>") from exc
    if parsed.version != 4 or str(parsed) != suffix:
        raise ValueError(f"{path} must contain a canonical UUIDv4")
    return value


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be valid JSON") from exc
    return serialized.encode("utf-8") + b"\n"


def _load_json_mapping(value: bytes | bytearray | memoryview | str) -> Mapping[str, object]:
    try:
        if isinstance(value, str):
            decoded = value
        elif isinstance(value, (bytes, bytearray, memoryview)):
            decoded = bytes(value).decode("utf-8")
        else:
            raise TypeError
        loaded = json.loads(decoded)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("payload must be a UTF-8 JSON object") from exc
    if not isinstance(loaded, Mapping):
        raise ValueError("payload must be a JSON object")
    return loaded


@dataclass(frozen=True)
class TranscriptSpan:
    start_sec: float
    end_sec: float
    text: str
    speaker_label: str | None
    confidence: float | None

    def __post_init__(self) -> None:
        start = _non_negative_number(self.start_sec, "start_sec")
        end = _finite_number(self.end_sec, "end_sec")
        if end <= start:
            raise ValueError("end_sec must be greater than start_sec")
        object.__setattr__(self, "start_sec", start)
        object.__setattr__(self, "end_sec", end)
        object.__setattr__(self, "text", _required_text(self.text, "text"))
        object.__setattr__(
            self,
            "speaker_label",
            _optional_text(self.speaker_label, "speaker_label"),
        )
        object.__setattr__(
            self,
            "confidence",
            _optional_score(self.confidence, "confidence"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "text": self.text,
            "speaker_label": self.speaker_label,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TranscriptSpan":
        data = _strict_mapping(
            value,
            path="transcript span",
            fields=frozenset(
                {"start_sec", "end_sec", "text", "speaker_label", "confidence"}
            ),
        )
        return cls(
            start_sec=data["start_sec"],
            end_sec=data["end_sec"],
            text=data["text"],
            speaker_label=data["speaker_label"],
            confidence=data["confidence"],
        )


@dataclass(frozen=True)
class TranscriptV1:
    source_duration_sec: float
    language: str | None
    speech_status: str
    spans: tuple[TranscriptSpan, ...]

    SCHEMA_VERSION: ClassVar[str] = "transcript.v1"

    def __post_init__(self) -> None:
        duration = _positive_number(self.source_duration_sec, "source_duration_sec")
        object.__setattr__(self, "source_duration_sec", duration)
        language = _optional_text(self.language, "language")
        if language is not None and _LANGUAGE_PATTERN.fullmatch(language) is None:
            raise ValueError("language must be a BCP-47-style tag or null")
        object.__setattr__(self, "language", language)
        if self.speech_status not in {"speech", "no_speech"}:
            raise ValueError("speech_status must be speech or no_speech")
        spans = tuple(self.spans)
        if any(not isinstance(span, TranscriptSpan) for span in spans):
            raise ValueError("spans must contain TranscriptSpan values")
        if list(spans) != sorted(spans, key=lambda span: (span.start_sec, span.end_sec)):
            raise ValueError("spans must be chronological")
        if any(span.end_sec > duration for span in spans):
            raise ValueError("span end_sec must not exceed source_duration_sec")
        if self.speech_status == "speech" and not spans:
            raise ValueError("speech_status speech requires at least one span")
        if self.speech_status == "no_speech" and spans:
            raise ValueError("speech_status no_speech requires empty spans")
        if self.speech_status == "no_speech" and language is not None:
            raise ValueError("speech_status no_speech requires language to be null")
        object.__setattr__(self, "spans", spans)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "source_duration_sec": self.source_duration_sec,
            "language": self.language,
            "speech_status": self.speech_status,
            "spans": [span.to_dict() for span in self.spans],
        }

    def to_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TranscriptV1":
        data = _strict_mapping(
            value,
            path="transcript.v1",
            fields=frozenset(
                {
                    "schema_version",
                    "source_duration_sec",
                    "language",
                    "speech_status",
                    "spans",
                }
            ),
        )
        if data["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {cls.SCHEMA_VERSION}")
        spans = data["spans"]
        if not isinstance(spans, list):
            raise ValueError("spans must be a list")
        return cls(
            source_duration_sec=data["source_duration_sec"],
            language=data["language"],
            speech_status=data["speech_status"],
            spans=tuple(TranscriptSpan.from_dict(span) for span in spans),
        )

    @classmethod
    def from_json_bytes(
        cls, value: bytes | bytearray | memoryview | str
    ) -> "TranscriptV1":
        return cls.from_dict(_load_json_mapping(value))


@dataclass(frozen=True)
class SemanticSegment:
    segment_id: str
    start_sec: float
    end_sec: float
    semantic_summary: str
    visual_description: str
    source_salience: float | None
    confidence: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_id", _segment_id(self.segment_id))
        start = _non_negative_number(self.start_sec, "start_sec")
        end = _finite_number(self.end_sec, "end_sec")
        if end <= start:
            raise ValueError("end_sec must be greater than start_sec")
        object.__setattr__(self, "start_sec", start)
        object.__setattr__(self, "end_sec", end)
        object.__setattr__(
            self,
            "semantic_summary",
            _required_text(self.semantic_summary, "semantic_summary"),
        )
        object.__setattr__(
            self,
            "visual_description",
            _required_text(self.visual_description, "visual_description"),
        )
        object.__setattr__(
            self,
            "source_salience",
            _optional_score(self.source_salience, "source_salience"),
        )
        object.__setattr__(
            self,
            "confidence",
            _optional_score(self.confidence, "confidence"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "semantic_summary": self.semantic_summary,
            "visual_description": self.visual_description,
            "source_salience": self.source_salience,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SemanticSegment":
        data = _strict_mapping(
            value,
            path="semantic segment",
            fields=frozenset(
                {
                    "segment_id",
                    "start_sec",
                    "end_sec",
                    "semantic_summary",
                    "visual_description",
                    "source_salience",
                    "confidence",
                }
            ),
        )
        return cls(
            segment_id=data["segment_id"],
            start_sec=data["start_sec"],
            end_sec=data["end_sec"],
            semantic_summary=data["semantic_summary"],
            visual_description=data["visual_description"],
            source_salience=data["source_salience"],
            confidence=data["confidence"],
        )


@dataclass(frozen=True)
class AIBaseline:
    global_summary: str
    segments: tuple[SemanticSegment, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "global_summary",
            _required_text(self.global_summary, "global_summary"),
        )
        segments = tuple(self.segments)
        if not segments or any(not isinstance(item, SemanticSegment) for item in segments):
            raise ValueError("ai_baseline.segments must contain at least one SemanticSegment")
        object.__setattr__(self, "segments", segments)

    def to_dict(self) -> dict[str, object]:
        return {
            "global_summary": self.global_summary,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AIBaseline":
        data = _strict_mapping(
            value,
            path="ai_baseline",
            fields=frozenset({"global_summary", "segments"}),
        )
        segments = data["segments"]
        if not isinstance(segments, list):
            raise ValueError("ai_baseline.segments must be a list")
        return cls(
            global_summary=data["global_summary"],
            segments=tuple(SemanticSegment.from_dict(item) for item in segments),
        )


def _normalize_global_review(
    value: object, path: str
) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    unknown = sorted(set(value) - _GLOBAL_REVIEW_FIELDS)
    if unknown:
        raise ValueError(f"{path} contains non-reviewable fields: {', '.join(unknown)}")
    return MappingProxyType(
        {key: _required_text(item, f"{path}.{key}") for key, item in value.items()}
    )


def _normalize_segment_field(field_name: str, value: object, path: str) -> object:
    if field_name == "start_sec":
        return _non_negative_number(value, path)
    if field_name == "end_sec":
        return _positive_number(value, path)
    return _required_text(value, path)


def _normalize_segment_review_map(
    value: object, path: str
) -> Mapping[str, Mapping[str, object]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    normalized: dict[str, Mapping[str, object]] = {}
    for raw_segment_id, raw_fields in value.items():
        segment_id = _segment_id(raw_segment_id, f"{path} key")
        if not isinstance(raw_fields, Mapping):
            raise ValueError(f"{path}.{segment_id} must be a mapping")
        unknown = sorted(set(raw_fields) - _SEGMENT_REVIEW_FIELDS)
        if unknown:
            raise ValueError(
                f"{path}.{segment_id} contains non-reviewable fields: "
                f"{', '.join(unknown)}"
            )
        normalized[segment_id] = MappingProxyType(
            {
                key: _normalize_segment_field(
                    key, item, f"{path}.{segment_id}.{key}"
                )
                for key, item in raw_fields.items()
            }
        )
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class HumanReviewOverlay:
    global_overrides: Mapping[str, str] = field(default_factory=dict)
    global_locks: Mapping[str, str] = field(default_factory=dict)
    removed_segment_ids: tuple[str, ...] = ()
    added_segments: tuple[SemanticSegment, ...] = ()
    segment_overrides: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    segment_locks: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        global_overrides = _normalize_global_review(
            self.global_overrides, "global_overrides"
        )
        global_locks = _normalize_global_review(self.global_locks, "global_locks")
        for field_name in set(global_overrides) & set(global_locks):
            if global_overrides[field_name] != global_locks[field_name]:
                raise ValueError("global override and lock values must agree")
        removed = tuple(
            _segment_id(value, "removed_segment_ids")
            for value in self.removed_segment_ids
        )
        if len(set(removed)) != len(removed):
            raise ValueError("removed_segment_ids must be unique")
        added = tuple(self.added_segments)
        if any(not isinstance(item, SemanticSegment) for item in added):
            raise ValueError("added_segments must contain SemanticSegment values")
        if any(
            item.source_salience is not None or item.confidence is not None
            for item in added
        ):
            raise ValueError(
                "added_segments source_salience and confidence must both be null"
            )
        segment_overrides = _normalize_segment_review_map(
            self.segment_overrides, "segment_overrides"
        )
        segment_locks = _normalize_segment_review_map(
            self.segment_locks, "segment_locks"
        )
        for segment_id in set(segment_overrides) & set(segment_locks):
            overrides = segment_overrides[segment_id]
            locks = segment_locks[segment_id]
            for field_name in set(overrides) & set(locks):
                if overrides[field_name] != locks[field_name]:
                    raise ValueError("segment override and lock values must agree")
        object.__setattr__(self, "global_overrides", global_overrides)
        object.__setattr__(self, "global_locks", global_locks)
        object.__setattr__(self, "removed_segment_ids", removed)
        object.__setattr__(self, "added_segments", added)
        object.__setattr__(self, "segment_overrides", segment_overrides)
        object.__setattr__(self, "segment_locks", segment_locks)

    def to_dict(self) -> dict[str, object]:
        return {
            "global_overrides": dict(self.global_overrides),
            "global_locks": dict(self.global_locks),
            "removed_segment_ids": list(self.removed_segment_ids),
            "added_segments": [segment.to_dict() for segment in self.added_segments],
            "segment_overrides": {
                segment_id: dict(values)
                for segment_id, values in self.segment_overrides.items()
            },
            "segment_locks": {
                segment_id: dict(values)
                for segment_id, values in self.segment_locks.items()
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HumanReviewOverlay":
        data = _strict_mapping(
            value,
            path="human_review",
            fields=frozenset(
                {
                    "global_overrides",
                    "global_locks",
                    "removed_segment_ids",
                    "added_segments",
                    "segment_overrides",
                    "segment_locks",
                }
            ),
        )
        removed = data["removed_segment_ids"]
        added = data["added_segments"]
        if not isinstance(removed, list):
            raise ValueError("removed_segment_ids must be a list")
        if not isinstance(added, list):
            raise ValueError("added_segments must be a list")
        return cls(
            global_overrides=data["global_overrides"],
            global_locks=data["global_locks"],
            removed_segment_ids=tuple(removed),
            added_segments=tuple(SemanticSegment.from_dict(item) for item in added),
            segment_overrides=data["segment_overrides"],
            segment_locks=data["segment_locks"],
        )


def _validate_segment_range(
    segment: SemanticSegment, source_duration_sec: float, path: str
) -> None:
    if segment.end_sec > source_duration_sec:
        raise ValueError(f"{path}.end_sec must not exceed source_duration_sec")


def _validate_non_overlapping(
    segments: list[tuple[float, float, str]], path: str
) -> None:
    ordered = sorted(segments)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ValueError(f"{path} overlap")


@dataclass(frozen=True)
class VideoUnderstandingV1:
    source_duration_sec: float
    ai_baseline: AIBaseline
    human_review: HumanReviewOverlay

    SCHEMA_VERSION: ClassVar[str] = "video_understanding.v1"

    def __post_init__(self) -> None:
        duration = _positive_number(self.source_duration_sec, "source_duration_sec")
        object.__setattr__(self, "source_duration_sec", duration)
        if not isinstance(self.ai_baseline, AIBaseline):
            raise ValueError("ai_baseline must be an AIBaseline")
        if not isinstance(self.human_review, HumanReviewOverlay):
            raise ValueError("human_review must be a HumanReviewOverlay")

        baseline_ids = [segment.segment_id for segment in self.ai_baseline.segments]
        added_ids = [segment.segment_id for segment in self.human_review.added_segments]
        if len(set(baseline_ids)) != len(baseline_ids):
            raise ValueError("ai_baseline segment IDs must be unique")
        if len(set(added_ids)) != len(added_ids):
            raise ValueError("added segment IDs must be unique")
        if set(baseline_ids) & set(added_ids):
            raise ValueError("baseline and added segment IDs must be disjoint")

        baseline_ranges: list[tuple[float, float, str]] = []
        for index, segment in enumerate(self.ai_baseline.segments):
            _validate_segment_range(segment, duration, f"ai_baseline.segments[{index}]")
            baseline_ranges.append(
                (segment.start_sec, segment.end_sec, segment.segment_id)
            )
        if baseline_ranges != sorted(baseline_ranges):
            raise ValueError("ai_baseline segments must be chronological")
        _validate_non_overlapping(baseline_ranges, "baseline segments")

        for index, segment in enumerate(self.human_review.added_segments):
            _validate_segment_range(segment, duration, f"added_segments[{index}]")

        baseline_id_set = set(baseline_ids)
        if not set(self.human_review.removed_segment_ids) <= baseline_id_set:
            raise ValueError("removed_segment_ids must reference baseline segments only")
        all_ids = baseline_id_set | set(added_ids)
        referenced_ids = set(self.human_review.segment_overrides) | set(
            self.human_review.segment_locks
        )
        if not referenced_ids <= all_ids:
            raise ValueError("review maps must reference baseline or added segments")

        effective_ranges: list[tuple[float, float, str]] = []
        removed_ids = set(self.human_review.removed_segment_ids)
        candidates = [
            segment
            for segment in self.ai_baseline.segments
            if segment.segment_id not in removed_ids
        ] + list(self.human_review.added_segments)
        for segment in candidates:
            overrides = self.human_review.segment_overrides.get(segment.segment_id, {})
            locks = self.human_review.segment_locks.get(segment.segment_id, {})
            start = locks.get("start_sec", overrides.get("start_sec", segment.start_sec))
            end = locks.get("end_sec", overrides.get("end_sec", segment.end_sec))
            if end <= start:
                raise ValueError(
                    f"effective segment {segment.segment_id} end_sec must exceed start_sec"
                )
            if end > duration:
                raise ValueError(
                    f"effective segment {segment.segment_id} exceeds source_duration_sec"
                )
            effective_ranges.append((start, end, segment.segment_id))
        _validate_non_overlapping(effective_ranges, "effective segments")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "source_duration_sec": self.source_duration_sec,
            "ai_baseline": self.ai_baseline.to_dict(),
            "human_review": self.human_review.to_dict(),
        }

    def to_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VideoUnderstandingV1":
        data = _strict_mapping(
            value,
            path="video_understanding.v1",
            fields=frozenset(
                {"schema_version", "source_duration_sec", "ai_baseline", "human_review"}
            ),
        )
        if data["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {cls.SCHEMA_VERSION}")
        return cls(
            source_duration_sec=data["source_duration_sec"],
            ai_baseline=AIBaseline.from_dict(data["ai_baseline"]),
            human_review=HumanReviewOverlay.from_dict(data["human_review"]),
        )

    @classmethod
    def from_json_bytes(
        cls, value: bytes | bytearray | memoryview | str
    ) -> "VideoUnderstandingV1":
        return cls.from_dict(_load_json_mapping(value))

