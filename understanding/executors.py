"""Injected staged executors and the migration-safe legacy analysis adapter."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from typing import Any

from artifacts import ArtifactOwner, ArtifactRepository, InputRefKind
from runtime import ArtifactOutputSpec, RuntimeExecutionContext, RuntimeRef, RuntimeRefKind
from workflow import ExecutorRegistry

from .models import (
    AIBaseline,
    HumanReviewOverlay,
    SemanticSegment,
    TranscriptV1,
    VideoUnderstandingV1,
)
from .workflow import (
    ANALYSIS_EXECUTOR_ID,
    PRIMARY_VIDEO_ROLE,
    TRANSCRIPTION_EXECUTOR_ID,
)


TRANSCRIPTION_PROFILE = "source_asr.v1"
ANALYSIS_PROFILE = "general_video_understanding.v1"
PROMPT_TEMPLATE_VERSION = "legacy_video_steps.v1"
SAMPLING_PROFILE = "legacy_dense_timeline.v1"


def _one_ref(
    context: RuntimeExecutionContext, slot: str, kind: RuntimeRefKind
) -> RuntimeRef:
    value = context.input_bindings.get(slot)
    if not isinstance(value, RuntimeRef) or value.kind is not kind:
        raise ValueError(f"{slot} must resolve to one {kind.value.title()}")
    return value


def transcript_output_adapter(
    result: object, _context: RuntimeExecutionContext
) -> dict[str, ArtifactOutputSpec]:
    if not isinstance(result, ArtifactOutputSpec):
        raise ValueError("transcription executor must return ArtifactOutputSpec")
    return {"transcript": result}


def analysis_output_adapter(
    result: object, _context: RuntimeExecutionContext
) -> dict[str, ArtifactOutputSpec]:
    if not isinstance(result, ArtifactOutputSpec):
        raise ValueError("analysis executor must return ArtifactOutputSpec")
    return {"understanding": result}


def make_transcription_executor(
    transcriber: Callable[[str], TranscriptV1],
    *,
    asset_resolver: Callable[[str], str],
    transcription_profile: str = TRANSCRIPTION_PROFILE,
) -> Callable[[RuntimeExecutionContext], ArtifactOutputSpec]:
    if not callable(transcriber) or not callable(asset_resolver):
        raise ValueError("transcriber and asset_resolver must be callable")

    def execute(context: RuntimeExecutionContext) -> ArtifactOutputSpec:
        source_ref = _one_ref(context, PRIMARY_VIDEO_ROLE, RuntimeRefKind.ASSET)
        payload = transcriber(asset_resolver(source_ref.id))
        if not isinstance(payload, TranscriptV1):
            raise ValueError("transcriber must return TranscriptV1")
        return ArtifactOutputSpec(
            owner=ArtifactOwner.asset(source_ref.id),
            payload_schema_version=TranscriptV1.SCHEMA_VERSION,
            content=payload.to_json_bytes(),
            media_type="application/json",
            display_name="Source Transcript",
            metadata={
                "transcription_profile": transcription_profile,
                "transcription_schema_revision": TranscriptV1.SCHEMA_VERSION,
            },
        )

    return execute


def _legacy_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"legacy {field_name} must be a non-empty string")
    return value.strip()


def _legacy_start(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("legacy step start_sec must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("legacy step start_sec must be a finite non-negative number")
    return result


def adapt_legacy_understanding(
    legacy: object, transcript: TranscriptV1
) -> VideoUnderstandingV1:
    """Convert legacy observations; never expose its URL/path/topic/cache fields."""
    if not isinstance(transcript, TranscriptV1):
        raise ValueError("transcript must be TranscriptV1")
    summary = _legacy_text(getattr(legacy, "summary", None), "summary")
    raw_steps = getattr(legacy, "steps", None)
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        raise ValueError("legacy analysis produced no usable steps")

    grouped: dict[float, dict[str, list[str]]] = {}
    for raw in raw_steps:
        start = _legacy_start(getattr(raw, "start_sec", None))
        if start >= transcript.source_duration_sec:
            raise ValueError("legacy step lies outside transcript duration")
        action = _legacy_text(getattr(raw, "action", None), "step action")
        detail_value = getattr(raw, "detail", None)
        detail = action if not isinstance(detail_value, str) or not detail_value.strip() else detail_value.strip()
        values = grouped.setdefault(start, {"actions": [], "details": []})
        if action not in values["actions"]:
            values["actions"].append(action)
        if detail not in values["details"]:
            values["details"].append(detail)

    starts = sorted(grouped)
    segments: list[SemanticSegment] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else transcript.source_duration_sec
        if end <= start:
            raise ValueError("legacy steps do not form valid semantic ranges")
        values = grouped[start]
        semantic_summary = " ".join(values["actions"])
        overlapping_text = [
            span.text
            for span in transcript.spans
            if span.start_sec < end and span.end_sec > start
        ]
        if overlapping_text:
            semantic_summary = (
                f"{semantic_summary} Spoken context: {' '.join(overlapping_text)}"
            )
        segments.append(
            SemanticSegment(
                segment_id=f"seg_{uuid.uuid4()}",
                start_sec=start,
                end_sec=end,
                semantic_summary=semantic_summary,
                visual_description=" ".join(values["details"]),
                source_salience=None,
                confidence=None,
            )
        )
    if not segments:
        raise ValueError("legacy analysis produced no usable evidence")
    return VideoUnderstandingV1(
        source_duration_sec=transcript.source_duration_sec,
        ai_baseline=AIBaseline(global_summary=summary, segments=tuple(segments)),
        human_review=HumanReviewOverlay(),
    )


def make_transcript_grounded_analysis_executor(
    analyzer: Callable[[str, str], Any],
    *,
    asset_resolver: Callable[[str], str],
    artifacts: ArtifactRepository,
    analysis_profile: str = ANALYSIS_PROFILE,
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
    sampling_profile: str = SAMPLING_PROFILE,
) -> Callable[[RuntimeExecutionContext], ArtifactOutputSpec]:
    if not callable(analyzer) or not callable(asset_resolver):
        raise ValueError("analyzer and asset_resolver must be callable")
    if not isinstance(artifacts, ArtifactRepository):
        raise ValueError("artifacts must be ArtifactRepository")

    def execute(context: RuntimeExecutionContext) -> ArtifactOutputSpec:
        source_ref = _one_ref(context, PRIMARY_VIDEO_ROLE, RuntimeRefKind.ASSET)
        transcript_ref = _one_ref(context, "transcript", RuntimeRefKind.ARTIFACT)
        revision = artifacts.get_revision(transcript_ref.id)
        if revision.deleted_at is not None or revision.purged_at is not None:
            raise ValueError("transcript Artifact must be live")
        if (
            revision.artifact_type != "transcript"
            or revision.payload_schema_version != TranscriptV1.SCHEMA_VERSION
        ):
            raise ValueError("transcript Artifact has the wrong type or schema")
        family = artifacts.get_family(revision.family_id)
        if family.owner != ArtifactOwner.asset(source_ref.id):
            raise ValueError("transcript owner does not match the source Asset")
        expected_ref = (PRIMARY_VIDEO_ROLE, InputRefKind.ASSET, source_ref.id)
        actual_refs = tuple((ref.slot, ref.kind, ref.id) for ref in revision.input_refs)
        if actual_refs != (expected_ref,):
            raise ValueError("transcript source dependency does not match the source Asset")
        transcript_payload = TranscriptV1.from_json_bytes(
            artifacts.resolve_content(transcript_ref.id).read_bytes()
        )
        legacy = analyzer(
            asset_resolver(source_ref.id),
            "general video understanding",
        )
        payload = adapt_legacy_understanding(legacy, transcript_payload)
        return ArtifactOutputSpec(
            owner=ArtifactOwner.asset(source_ref.id),
            payload_schema_version=VideoUnderstandingV1.SCHEMA_VERSION,
            content=payload.to_json_bytes(),
            media_type="application/json",
            display_name="Video Understanding",
            metadata={
                "change_kind": "fresh_analysis",
                "analysis_profile": analysis_profile,
                "analysis_schema_revision": VideoUnderstandingV1.SCHEMA_VERSION,
                "prompt_template_version": prompt_template_version,
                "sampling_profile": sampling_profile,
                "transcript_artifact_id": transcript_ref.id,
            },
        )

    return execute


def register_understanding_executors(
    registry: ExecutorRegistry,
    *,
    transcriber: Callable[[str], TranscriptV1],
    analyzer: Callable[[str, str], Any],
    asset_resolver: Callable[[str], str],
    artifacts: ArtifactRepository,
) -> None:
    registry.register(
        TRANSCRIPTION_EXECUTOR_ID,
        make_transcription_executor(transcriber, asset_resolver=asset_resolver),
    )
    registry.register(
        ANALYSIS_EXECUTOR_ID,
        make_transcript_grounded_analysis_executor(
            analyzer,
            asset_resolver=asset_resolver,
            artifacts=artifacts,
        ),
    )

