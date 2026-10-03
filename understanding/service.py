"""Application service for immutable review, regeneration, and exact reuse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from artifacts import (
    ArtifactInputRef,
    ArtifactOwner,
    ArtifactProducer,
    ArtifactRepository,
    ArtifactRevision,
    InputRefKind,
    ProducerKind,
    StorageMode,
)
from runtime import (
    StageNotRunnableError,
    TaskOperation,
    TaskStatus,
    WorkflowRuntime,
)

from .executors import (
    ANALYSIS_PROFILE,
    PROMPT_TEMPLATE_VERSION,
    SAMPLING_PROFILE,
    TRANSCRIPTION_PROFILE,
    analysis_output_adapter,
)
from .models import AIBaseline, SemanticSegment, TranscriptV1, VideoUnderstandingV1
from .review import UnderstandingDraft
from .workflow import ANALYSIS_EXECUTOR_ID, PRIMARY_VIDEO_ROLE


class UnderstandingService:
    """Compose public Artifact and Runtime APIs without owning their state."""

    def __init__(self, runtime: WorkflowRuntime) -> None:
        if not isinstance(runtime, WorkflowRuntime):
            raise ValueError("runtime must be WorkflowRuntime")
        self.runtime = runtime
        self.artifacts: ArtifactRepository = runtime.artifacts

    def load_payload(self, artifact_id: str) -> VideoUnderstandingV1:
        revision = self._understanding_revision(artifact_id)
        return VideoUnderstandingV1.from_json_bytes(
            self.artifacts.resolve_content(revision.artifact_id).read_bytes()
        )

    def draft_from(self, artifact_id: str) -> UnderstandingDraft:
        return UnderstandingDraft.from_payload(self.load_payload(artifact_id))

    def save_review_version(
        self, base_artifact_id: str, draft: UnderstandingDraft
    ) -> ArtifactRevision:
        if not isinstance(draft, UnderstandingDraft):
            raise ValueError("draft must be UnderstandingDraft")
        base_revision = self._understanding_revision(base_artifact_id)
        base_payload = self.load_payload(base_artifact_id)
        payload = draft.to_payload()
        if (
            payload.source_duration_sec != base_payload.source_duration_sec
            or payload.ai_baseline != base_payload.ai_baseline
        ):
            raise ValueError("review drafts must preserve the base AI payload")
        working = self.artifacts.create_draft(
            base_revision.family_id, base_artifact_id=base_artifact_id
        )
        working.payload_schema_version = VideoUnderstandingV1.SCHEMA_VERSION
        working.producer = ArtifactProducer(ProducerKind.HUMAN)
        working.input_refs = base_revision.input_refs
        working.metadata = {**dict(base_revision.metadata), "change_kind": "human_edit"}
        working.replace_content(
            payload.to_json_bytes(),
            media_type="application/json",
            storage=StorageMode.BLOB,
        )
        return self.artifacts.save_revision(working)

    def run_fresh_analysis(self, project_id: str):
        return self.runtime.execute_stage(
            project_id,
            "analyze_source",
            output_adapter=analysis_output_adapter,
        )

    def regenerate_analysis(
        self,
        project_id: str,
        base_artifact_id: str,
        *,
        global_summary: str,
        refreshed_segments: Mapping[str, SemanticSegment],
    ):
        def build(base: VideoUnderstandingV1) -> VideoUnderstandingV1:
            removed = set(base.human_review.removed_segment_ids)
            expected = {
                item.segment_id
                for item in base.ai_baseline.segments
                if item.segment_id not in removed
            }
            if set(refreshed_segments) != expected:
                raise ValueError(
                    "normal regeneration requires every effective baseline segment"
                )
            updated: list[SemanticSegment] = []
            for original in base.ai_baseline.segments:
                if original.segment_id in removed:
                    updated.append(original)
                    continue
                refreshed = self._refresh_for_original(
                    original, refreshed_segments[original.segment_id]
                )
                updated.append(refreshed)
            return VideoUnderstandingV1(
                base.source_duration_sec,
                AIBaseline(global_summary, tuple(updated)),
                base.human_review,
            )

        return self._run_regeneration(
            project_id,
            base_artifact_id,
            change_kind="regenerate_analysis",
            build_payload=build,
        )

    def regenerate_segments(
        self,
        project_id: str,
        base_artifact_id: str,
        segment_ids: Sequence[str],
        *,
        refreshed_segments: Mapping[str, SemanticSegment],
    ):
        requested = tuple(segment_ids)

        def build(base: VideoUnderstandingV1) -> VideoUnderstandingV1:
            if not requested or len(set(requested)) != len(requested):
                raise ValueError("partial regeneration requires unique baseline IDs")
            removed = set(base.human_review.removed_segment_ids)
            allowed = {
                item.segment_id
                for item in base.ai_baseline.segments
                if item.segment_id not in removed
            }
            if not set(requested) <= allowed:
                raise ValueError(
                    "partial regeneration accepts effective unremoved baseline IDs only"
                )
            if set(refreshed_segments) != set(requested):
                raise ValueError("refreshed segment IDs must match the requested IDs")
            updated = tuple(
                self._refresh_for_original(
                    original, refreshed_segments[original.segment_id]
                )
                if original.segment_id in refreshed_segments
                else original
                for original in base.ai_baseline.segments
            )
            return VideoUnderstandingV1(
                base.source_duration_sec,
                AIBaseline(base.ai_baseline.global_summary, updated),
                base.human_review,
            )

        return self._run_regeneration(
            project_id,
            base_artifact_id,
            change_kind="partial_regeneration",
            build_payload=build,
        )

    def reuse_transcript(self, project_id: str):
        revision = self._find_compatible(
            project_id,
            stage_id="transcribe_source",
            artifact_type="transcript",
            schema_version=TranscriptV1.SCHEMA_VERSION,
            metadata={
                "transcription_profile": TRANSCRIPTION_PROFILE,
                "transcription_schema_revision": TranscriptV1.SCHEMA_VERSION,
            },
        )
        return self.runtime.reuse_stage_outputs(
            project_id,
            "transcribe_source",
            {"transcript": revision.artifact_id},
        )

    def reuse_understanding(self, project_id: str):
        revision = self._find_compatible(
            project_id,
            stage_id="analyze_source",
            artifact_type="video_understanding",
            schema_version=VideoUnderstandingV1.SCHEMA_VERSION,
            metadata={
                "analysis_profile": ANALYSIS_PROFILE,
                "analysis_schema_revision": VideoUnderstandingV1.SCHEMA_VERSION,
                "prompt_template_version": PROMPT_TEMPLATE_VERSION,
                "sampling_profile": SAMPLING_PROFILE,
            },
        )
        return self.runtime.reuse_stage_outputs(
            project_id,
            "analyze_source",
            {"understanding": revision.artifact_id},
        )

    @staticmethod
    def _refresh_for_original(
        original: SemanticSegment, refreshed: SemanticSegment
    ) -> SemanticSegment:
        if not isinstance(refreshed, SemanticSegment):
            raise ValueError("refreshed_segments must contain SemanticSegment values")
        if refreshed.segment_id != original.segment_id:
            raise ValueError("regeneration must preserve baseline segment IDs")
        return SemanticSegment(
            segment_id=original.segment_id,
            start_sec=original.start_sec,
            end_sec=original.end_sec,
            semantic_summary=refreshed.semantic_summary,
            visual_description=refreshed.visual_description,
            source_salience=refreshed.source_salience,
            confidence=refreshed.confidence,
        )

    def _run_regeneration(
        self,
        project_id: str,
        base_artifact_id: str,
        *,
        change_kind: str,
        build_payload,
    ):
        view = self.runtime.states.get_view(project_id, "analyze_source")
        if not view.runnable:
            raise StageNotRunnableError(
                "Stage analyze_source is blocked by: " + ", ".join(view.blocked_by)
            )
        project = self.runtime.repository.get_project(project_id)
        workflow = self.runtime.projects.workflow_for(project)
        inputs = self.runtime.states.bindings.resolve_stage_inputs(
            project, workflow, "analyze_source"
        )
        task = self.runtime.tasks.queue(
            project.stage_runs["analyze_source"],
            operation=TaskOperation.EXECUTE,
            executor_id=ANALYSIS_EXECUTOR_ID,
            input_bindings=inputs,
        )
        task = self.runtime.tasks.start(task.task_id)
        try:
            base_revision = self._understanding_revision(base_artifact_id)
            base_payload = self.load_payload(base_artifact_id)
            input_refs, source_id, transcript = self._current_analysis_inputs(
                project_id
            )
            family = self.artifacts.get_family(base_revision.family_id)
            if family.owner != ArtifactOwner.asset(source_id):
                raise ValueError("base Understanding owner does not match current source")
            if base_payload.source_duration_sec != transcript.source_duration_sec:
                raise ValueError(
                    "Understanding duration must equal the current transcript duration"
                )
            payload = build_payload(base_payload)
            saved = self._save_regenerated(
                project_id,
                project.stage_runs["analyze_source"],
                base_revision,
                payload,
                input_refs,
                change_kind,
            )
            return self.runtime.tasks.succeed(
                task.task_id, {"understanding": saved.artifact_id}
            )
        except Exception as exc:
            return self.runtime.tasks.fail(
                task.task_id,
                code="understanding_regeneration_failed",
                message=str(exc) or type(exc).__name__,
            )

    def _save_regenerated(
        self,
        project_id: str,
        stage_run_id: str,
        base_revision: ArtifactRevision,
        payload: VideoUnderstandingV1,
        input_refs: tuple[ArtifactInputRef, ...],
        change_kind: str,
    ) -> ArtifactRevision:
        working = self.artifacts.create_draft(
            base_revision.family_id,
            base_artifact_id=base_revision.artifact_id,
        )
        working.payload_schema_version = VideoUnderstandingV1.SCHEMA_VERSION
        working.producer = ArtifactProducer(
            ProducerKind.EXECUTOR,
            executor_id=ANALYSIS_EXECUTOR_ID,
            project_id=project_id,
            stage_run_id=stage_run_id,
        )
        working.input_refs = input_refs
        working.metadata = {
            **dict(base_revision.metadata),
            "change_kind": change_kind,
        }
        working.replace_content(
            payload.to_json_bytes(),
            media_type="application/json",
            storage=StorageMode.BLOB,
        )
        return self.artifacts.save_revision(working)

    def _current_analysis_inputs(
        self, project_id: str
    ) -> tuple[tuple[ArtifactInputRef, ...], str, TranscriptV1]:
        project = self.runtime.repository.get_project(project_id)
        workflow = self.runtime.projects.workflow_for(project)
        refs = self.runtime.states.bindings.expected_input_refs(
            project, workflow, "analyze_source"
        )
        source_refs = [
            ref
            for ref in refs
            if ref.slot == PRIMARY_VIDEO_ROLE and ref.kind is InputRefKind.ASSET
        ]
        transcript_refs = [
            ref
            for ref in refs
            if ref.slot == "transcript" and ref.kind is InputRefKind.ARTIFACT
        ]
        if len(source_refs) != 1 or len(transcript_refs) != 1 or len(refs) != 2:
            raise ValueError("analyze_source requires exact source and transcript inputs")
        source_id = source_refs[0].id
        transcript_revision = self.artifacts.get_revision(transcript_refs[0].id)
        if (
            transcript_revision.deleted_at is not None
            or transcript_revision.purged_at is not None
            or transcript_revision.artifact_type != "transcript"
            or transcript_revision.payload_schema_version != TranscriptV1.SCHEMA_VERSION
        ):
            raise ValueError("current transcript must be a live transcript.v1 Artifact")
        transcript_family = self.artifacts.get_family(transcript_revision.family_id)
        if transcript_family.owner != ArtifactOwner.asset(source_id):
            raise ValueError("current transcript owner does not match current source")
        expected_source = (PRIMARY_VIDEO_ROLE, InputRefKind.ASSET, source_id)
        actual = tuple(
            (ref.slot, ref.kind, ref.id) for ref in transcript_revision.input_refs
        )
        if actual != (expected_source,):
            raise ValueError("current transcript dependency does not match current source")
        transcript = TranscriptV1.from_json_bytes(
            self.artifacts.resolve_content(transcript_revision.artifact_id).read_bytes()
        )
        return refs, source_id, transcript

    def _find_compatible(
        self,
        project_id: str,
        *,
        stage_id: str,
        artifact_type: str,
        schema_version: str,
        metadata: Mapping[str, object],
    ) -> ArtifactRevision:
        project = self.runtime.repository.get_project(project_id)
        workflow = self.runtime.projects.workflow_for(project)
        refs = self.runtime.states.bindings.expected_input_refs(
            project, workflow, stage_id
        )
        source = next(
            (
                ref
                for ref in refs
                if ref.slot == PRIMARY_VIDEO_ROLE and ref.kind is InputRefKind.ASSET
            ),
            None,
        )
        if source is None:
            raise LookupError("no compatible Artifact for current source")
        family = self.artifacts.find_family(
            ArtifactOwner.asset(source.id), artifact_type
        )
        if family is None:
            raise LookupError("no compatible Artifact revision exists")
        expected_refs = sorted((ref.slot, ref.kind.value, ref.id) for ref in refs)
        for revision in reversed(self.artifacts.list_revisions(family.family_id)):
            if revision.payload_schema_version != schema_version:
                continue
            actual_refs = sorted(
                (ref.slot, ref.kind.value, ref.id) for ref in revision.input_refs
            )
            if actual_refs != expected_refs:
                continue
            if any(revision.metadata.get(key) != value for key, value in metadata.items()):
                continue
            content = self.artifacts.resolve_content(revision.artifact_id).read_bytes()
            if artifact_type == "transcript":
                TranscriptV1.from_json_bytes(content)
            else:
                VideoUnderstandingV1.from_json_bytes(content)
            return revision
        raise LookupError("no compatible Artifact revision exists")

    def _understanding_revision(self, artifact_id: str) -> ArtifactRevision:
        revision = self.artifacts.get_revision(artifact_id)
        if revision.deleted_at is not None or revision.purged_at is not None:
            raise ValueError("Understanding revision must be live")
        if (
            revision.artifact_type != "video_understanding"
            or revision.payload_schema_version != VideoUnderstandingV1.SCHEMA_VERSION
        ):
            raise ValueError("Artifact must be video_understanding.v1")
        family = self.artifacts.get_family(revision.family_id)
        if family.owner.kind.value != "asset":
            raise ValueError("Understanding Family must be Asset-owned")
        return revision

