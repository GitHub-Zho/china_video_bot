from types import SimpleNamespace

import pytest

from artifacts import ArtifactOwner, ArtifactRepository, InputRefKind
from runtime import (
    ProjectService,
    RuntimeExecutionContext,
    RuntimeRef,
    RuntimeRefKind,
    RuntimeRepository,
    StageReview,
    StageStateResolver,
    TaskStatus,
    WorkflowCatalog,
    WorkflowRuntime,
)
from source_assets import SourceAssetRepository, SourceAssetV1
from understanding import (
    FasterWhisperSourceTranscriber,
    TranscriptSpan,
    TranscriptV1,
    VideoUnderstandingV1,
    adapt_legacy_understanding,
    analysis_output_adapter,
    build_understanding_workflow,
    create_understanding_project,
    make_transcript_grounded_analysis_executor,
    make_transcription_executor,
    register_understanding_executors,
    transcript_output_adapter,
)
from workflow import ExecutorRegistry


def transcript():
    return TranscriptV1(
        source_duration_sec=20,
        language="zh-CN",
        speech_status="speech",
        spans=(
            TranscriptSpan(0, 6, "先处理食材", None, 0.9),
            TranscriptSpan(10, 17, "然后完成烹饪", None, 0.8),
        ),
    )


def legacy_result():
    return SimpleNamespace(
        summary="The source demonstrates two preparation steps.",
        total_duration=99,
        url="https://private.example/source",
        video_path="/private/cache/video.mp4",
        topic="private prompt",
        steps=[
            SimpleNamespace(
                start_sec=0,
                action="The cook prepares the ingredients.",
                detail="Hands arrange the ingredients on a board.",
            ),
            SimpleNamespace(
                start_sec=10,
                action="The cook finishes the dish.",
                detail="The prepared dish is placed on a plate.",
            ),
        ],
        timeline=[],
    )


def test_source_asset_repository_canonicalizes_and_deduplicates_bilibili(tmp_path):
    """Catch URL tracking/noise creating duplicate source identities."""
    repository = SourceAssetRepository(tmp_path)

    first = repository.register_remote_video(
        "https://m.bilibili.com/video/BV1Lfau6iEYQ/?share_source=copy#reply",
        display_name="Duck preparation",
    )
    second = repository.register_remote_video(
        "https://www.bilibili.com/video/BV1Lfau6iEYQ/p1?spm_id_from=333"
    )

    assert first.asset_id == second.asset_id
    assert first.source_uri == "https://www.bilibili.com/video/BV1Lfau6iEYQ/"
    assert repository.get(first.asset_id) == first
    assert repository.resolve(first.asset_id) == first.source_uri
    assert SourceAssetV1.from_dict(first.to_dict()) == first


def test_source_asset_model_rejects_unknown_fields_or_embedded_credentials():
    """Catch Source Asset records accepting schema drift or secret-bearing URLs."""
    values = {
        "schema_version": "source_asset.v1",
        "asset_id": "asset_0123456789abcdef",
        "asset_kind": "remote_video",
        "source_uri": "https://example.com/video",
        "display_name": None,
        "created_at": "2026-09-25T12:00:00+00:00",
        "updated_at": "2026-09-25T12:00:00+00:00",
    }
    with pytest.raises(ValueError, match="unknown fields"):
        SourceAssetV1.from_dict({**values, "cookie": "secret"})
    with pytest.raises(ValueError, match="credentials"):
        SourceAssetV1.from_dict(
            {**values, "source_uri": "https://user:pass@example.com/video"}
        )


def test_understanding_workflow_matches_the_locked_three_stage_contract():
    """Catch stage IDs, approval gates, or Artifact slots drifting from the pinned guide."""
    workflow = build_understanding_workflow()

    assert (workflow.workflow_id, workflow.workflow_version, workflow.mode) == (
        "video_grounded.understand",
        "1.0",
        "video_grounded",
    )
    assert workflow.topological_stage_ids() == (
        "transcribe_source",
        "analyze_source",
        "plan_source",
    )
    assert workflow.stages[0].outputs["transcript"].artifact_type == "transcript"
    assert workflow.stages[1].inputs["transcript"].artifact_type == "transcript"
    assert workflow.stages[1].approval_required is True
    assert workflow.stages[2].outputs["plan"].artifact_type == "edit_plan"


def test_source_transcriber_converts_successful_asr_into_formal_transcript():
    """Catch source ASR leaking provider objects or treating speech as an untyped cache."""
    info = SimpleNamespace(language="zh", language_probability=0.95)
    asr_segments = [
        SimpleNamespace(start=1.0, end=4.0, text=" 第一段 "),
        SimpleNamespace(start=5.0, end=8.0, text="第二段"),
    ]
    model = SimpleNamespace(transcribe=lambda _path, **_kwargs: (asr_segments, info))
    capability = FasterWhisperSourceTranscriber(
        model_loader=lambda: model,
        duration_probe=lambda _path: 10.0,
    )

    result = capability("/safe/media.mp4")

    assert result == TranscriptV1(
        10,
        "zh",
        "speech",
        (
            TranscriptSpan(1, 4, "第一段", None, None),
            TranscriptSpan(5, 8, "第二段", None, None),
        ),
    )


def test_legacy_adapter_builds_formal_segments_without_private_legacy_fields():
    """Catch direct serialization of URL/path/topic or use of the legacy probe duration."""
    payload = adapt_legacy_understanding(legacy_result(), transcript())

    assert payload.source_duration_sec == 20.0
    assert [(item.start_sec, item.end_sec) for item in payload.ai_baseline.segments] == [
        (0.0, 10.0),
        (10.0, 20.0),
    ]
    assert "先处理食材" in payload.ai_baseline.segments[0].semantic_summary
    assert payload.human_review.added_segments == ()
    encoded = payload.to_json_bytes()
    assert b"private.example" not in encoded
    assert b"private/cache" not in encoded
    assert b"private prompt" not in encoded


def test_transcript_grounded_executor_rejects_wrong_asset_owner(tmp_path):
    """Catch an Understanding revision being built from another Asset's transcript."""
    artifacts = ArtifactRepository(tmp_path)
    family = artifacts.get_or_create_family(ArtifactOwner.asset("asset_one"), "transcript")
    draft = artifacts.create_draft(family.family_id)
    from artifacts import ArtifactInputRef, ArtifactProducer, ProducerKind, StorageMode

    draft.payload_schema_version = "transcript.v1"
    draft.producer = ArtifactProducer(ProducerKind.IMPORT)
    draft.input_refs = (
        ArtifactInputRef("primary_video", InputRefKind.ASSET, "asset_one"),
    )
    draft.replace_content(
        transcript().to_json_bytes(),
        media_type="application/json",
        storage=StorageMode.BLOB,
    )
    revision = artifacts.save_revision(draft)
    executor = make_transcript_grounded_analysis_executor(
        lambda _uri, _topic: legacy_result(),
        asset_resolver=lambda asset_id: f"https://example.com/{asset_id}",
        artifacts=artifacts,
    )
    context = RuntimeExecutionContext(
        project=None,
        stage_id="analyze_source",
        stage_run_id="stage_run_test",
        input_bindings={
            "primary_video": RuntimeRef(RuntimeRefKind.ASSET, "asset_two"),
            "transcript": RuntimeRef(RuntimeRefKind.ARTIFACT, revision.artifact_id),
        },
    )

    with pytest.raises(ValueError, match="owner"):
        executor(context)


def test_fake_capabilities_execute_the_two_formal_stages_and_leave_review_gate(tmp_path):
    """Catch a staged run saving wrong owners/dependencies or bypassing review approval."""
    source_assets = SourceAssetRepository(tmp_path / "sources")
    source = source_assets.register_remote_video("https://example.com/video/one")
    runtime_repository = RuntimeRepository(tmp_path / "runtime")
    artifacts = ArtifactRepository(tmp_path / "artifacts")
    workflow = build_understanding_workflow()
    catalog = WorkflowCatalog((workflow,))
    projects = ProjectService(runtime_repository, catalog, artifacts)
    project = create_understanding_project(projects, "Staged", source.asset_id)
    registry = ExecutorRegistry()
    register_understanding_executors(
        registry,
        transcriber=lambda _source: transcript(),
        analyzer=lambda _source, _topic: legacy_result(),
        asset_resolver=source_assets.resolve,
        artifacts=artifacts,
    )
    runtime = WorkflowRuntime(
        runtime_repository,
        catalog,
        artifacts,
        registry,
        projects=projects,
    )

    transcript_task = runtime.execute_stage(
        project.project_id,
        "transcribe_source",
        output_adapter=transcript_output_adapter,
    )
    analysis_task = runtime.execute_stage(
        project.project_id,
        "analyze_source",
        output_adapter=analysis_output_adapter,
    )

    assert transcript_task.status is TaskStatus.SUCCEEDED
    assert analysis_task.status is TaskStatus.SUCCEEDED
    transcript_id = transcript_task.output_bindings["transcript"]
    understanding_id = analysis_task.output_bindings["understanding"]
    transcript_revision = artifacts.get_revision(transcript_id)
    understanding_revision = artifacts.get_revision(understanding_id)
    assert artifacts.get_family(transcript_revision.family_id).owner == ArtifactOwner.asset(
        source.asset_id
    )
    assert artifacts.get_family(understanding_revision.family_id).owner == ArtifactOwner.asset(
        source.asset_id
    )
    assert [(ref.slot, ref.kind, ref.id) for ref in transcript_revision.input_refs] == [
        ("primary_video", InputRefKind.ASSET, source.asset_id)
    ]
    assert [(ref.slot, ref.kind, ref.id) for ref in understanding_revision.input_refs] == [
        ("primary_video", InputRefKind.ASSET, source.asset_id),
        ("transcript", InputRefKind.ARTIFACT, transcript_id),
    ]
    assert VideoUnderstandingV1.from_json_bytes(
        artifacts.resolve_content(understanding_id).read_bytes()
    ).source_duration_sec == 20.0

    views = StageStateResolver(runtime_repository, catalog, artifacts)
    assert views.get_view(project.project_id, "analyze_source").review is StageReview.NEEDS_REVIEW
    assert views.get_view(project.project_id, "plan_source").runnable is False
    projects.approve_stage(project.project_id, "analyze_source")
    assert views.get_view(project.project_id, "plan_source").runnable is True


def test_transcription_failure_records_no_artifact_or_active_pointer(tmp_path):
    """Catch failed ASR attempts leaving a partial Artifact or moving Project Active."""
    source_assets = SourceAssetRepository(tmp_path / "sources")
    source = source_assets.register_remote_video("https://example.com/video/fail")
    runtime_repository = RuntimeRepository(tmp_path / "runtime")
    artifacts = ArtifactRepository(tmp_path / "artifacts")
    workflow = build_understanding_workflow()
    catalog = WorkflowCatalog((workflow,))
    projects = ProjectService(runtime_repository, catalog, artifacts)
    project = create_understanding_project(projects, "Failure", source.asset_id)
    registry = ExecutorRegistry()
    registry.register(
        "video_grounded.transcribe_source",
        make_transcription_executor(
            lambda _source: (_ for _ in ()).throw(RuntimeError("ASR unavailable")),
            asset_resolver=source_assets.resolve,
        ),
    )
    runtime = WorkflowRuntime(
        runtime_repository, catalog, artifacts, registry, projects=projects
    )

    task = runtime.execute_stage(
        project.project_id,
        "transcribe_source",
        output_adapter=transcript_output_adapter,
    )

    assert task.status is TaskStatus.FAILED
    reopened = projects.open_project(project.project_id)
    assert reopened.artifact_bindings.stage_outputs["transcribe_source"] == {}
    assert artifacts.find_family(ArtifactOwner.asset(source.asset_id), "transcript") is None
