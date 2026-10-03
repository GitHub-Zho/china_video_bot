from types import SimpleNamespace

import pytest

from artifacts import ArtifactOwner, ArtifactRepository, ProducerKind
from runtime import (
    ProjectService,
    RuntimeRepository,
    StageFreshness,
    StageStateResolver,
    TaskOperation,
    TaskStatus,
    WorkflowCatalog,
    WorkflowRuntime,
)
from source_assets import SourceAssetRepository
from understanding import (
    AIBaseline,
    SemanticSegment,
    TranscriptSpan,
    TranscriptV1,
    UnderstandingService,
    VideoUnderstandingV1,
    analysis_output_adapter,
    build_understanding_workflow,
    create_understanding_project,
    effective_global_summary,
    effective_segments,
    register_understanding_executors,
    transcript_output_adapter,
)
from workflow import ExecutorRegistry


def _transcript():
    return TranscriptV1(
        20,
        "zh",
        "speech",
        (
            TranscriptSpan(0, 8, "准备食材", None, 0.9),
            TranscriptSpan(10, 18, "完成烹饪", None, 0.8),
        ),
    )


def _legacy():
    return SimpleNamespace(
        summary="Two cooking steps.",
        steps=[
            SimpleNamespace(start_sec=0, action="Prepare", detail="Ingredients on board"),
            SimpleNamespace(start_sec=10, action="Finish", detail="Dish on plate"),
        ],
    )


def _stack(tmp_path):
    sources = SourceAssetRepository(tmp_path / "sources")
    source = sources.register_remote_video("https://example.com/source")
    repository = RuntimeRepository(tmp_path / "runtime")
    artifacts = ArtifactRepository(tmp_path / "artifacts")
    workflow = build_understanding_workflow()
    catalog = WorkflowCatalog((workflow,))
    projects = ProjectService(repository, catalog, artifacts)
    registry = ExecutorRegistry()
    register_understanding_executors(
        registry,
        transcriber=lambda _source: _transcript(),
        analyzer=lambda _source, _topic: _legacy(),
        asset_resolver=sources.resolve,
        artifacts=artifacts,
    )
    runtime = WorkflowRuntime(
        repository, catalog, artifacts, registry, projects=projects
    )
    service = UnderstandingService(runtime)
    project = create_understanding_project(projects, "Project A", source.asset_id)
    transcript_task = runtime.execute_stage(
        project.project_id, "transcribe_source", output_adapter=transcript_output_adapter
    )
    analysis_task = runtime.execute_stage(
        project.project_id, "analyze_source", output_adapter=analysis_output_adapter
    )
    return SimpleNamespace(
        sources=sources,
        source=source,
        repository=repository,
        artifacts=artifacts,
        catalog=catalog,
        projects=projects,
        runtime=runtime,
        service=service,
        project=project,
        transcript_id=transcript_task.output_bindings["transcript"],
        understanding_id=analysis_task.output_bindings["understanding"],
    )


def _refreshed(segment, *, summary, description):
    return SemanticSegment(
        segment.segment_id,
        segment.start_sec,
        segment.end_sec,
        summary,
        description,
        0.5,
        0.6,
    )


def test_review_save_creates_child_revision_without_moving_runtime_pointers(tmp_path):
    """Catch Save Version mutating U1 or silently selecting/approving its child."""
    stack = _stack(tmp_path)
    before = stack.artifacts.resolve_content(stack.understanding_id).read_bytes()
    draft = stack.service.draft_from(stack.understanding_id)
    draft.set_global_summary("Human summary")

    reviewed = stack.service.save_review_version(stack.understanding_id, draft)

    assert reviewed.parent_artifact_id == stack.understanding_id
    assert reviewed.producer.kind is ProducerKind.HUMAN
    assert stack.artifacts.resolve_content(stack.understanding_id).read_bytes() == before
    project = stack.projects.open_project(stack.project.project_id)
    assert project.artifact_bindings.stage_outputs["analyze_source"]["understanding"] == (
        stack.understanding_id
    )
    stage = stack.repository.get_stage_run(project.stage_runs["analyze_source"])
    assert stage.approval is None

    stack.projects.set_active(
        project.project_id, "analyze_source", "understanding", reviewed.artifact_id
    )
    stack.projects.approve_stage(project.project_id, "analyze_source")
    later = stack.service.save_review_version(
        reviewed.artifact_id,
        stack.service.draft_from(reviewed.artifact_id).set_global_summary("Later"),
    )
    reopened = stack.projects.open_project(project.project_id)
    approval = stack.repository.get_stage_run(
        reopened.stage_runs["analyze_source"]
    ).approval
    assert later.artifact_id != reviewed.artifact_id
    assert reopened.artifact_bindings.stage_outputs["analyze_source"]["understanding"] == reviewed.artifact_id
    assert approval.approved_outputs["understanding"] == reviewed.artifact_id


def test_normal_and_partial_regeneration_preserve_review_and_active_selection(tmp_path):
    """Catch regeneration erasing locks/overrides or storing only a partial patch."""
    stack = _stack(tmp_path)
    draft = stack.service.draft_from(stack.understanding_id)
    base_payload = draft.to_payload()
    first, second = effective_segments(base_payload)
    draft.set_segment_field(first.segment_id, "semantic_summary", "Locked human first")
    draft.lock_segment_field(first.segment_id, "semantic_summary")
    draft.set_segment_field(second.segment_id, "visual_description", "Human second view")
    reviewed = stack.service.save_review_version(stack.understanding_id, draft)
    stack.projects.set_active(
        stack.project.project_id,
        "analyze_source",
        "understanding",
        reviewed.artifact_id,
    )
    stack.projects.approve_stage(stack.project.project_id, "analyze_source")
    reviewed_payload = stack.service.load_payload(reviewed.artifact_id)
    baseline = reviewed_payload.ai_baseline.segments

    normal_task = stack.service.regenerate_analysis(
        stack.project.project_id,
        reviewed.artifact_id,
        global_summary="New AI summary",
        refreshed_segments={
            item.segment_id: _refreshed(
                item,
                summary=f"New AI {index}",
                description=f"New view {index}",
            )
            for index, item in enumerate(baseline)
        },
    )
    assert normal_task.status is TaskStatus.SUCCEEDED
    normal_id = normal_task.output_bindings["understanding"]
    normal = stack.service.load_payload(normal_id)
    assert normal.human_review == reviewed_payload.human_review
    assert effective_global_summary(normal) == "New AI summary"
    effective = effective_segments(normal)
    assert effective[0].semantic_summary == "Locked human first"
    assert effective[1].visual_description == "Human second view"
    assert [item.segment_id for item in normal.ai_baseline.segments] == [
        item.segment_id for item in baseline
    ]

    partial_task = stack.service.regenerate_segments(
        stack.project.project_id,
        normal_id,
        (baseline[0].segment_id,),
        refreshed_segments={
            baseline[0].segment_id: _refreshed(
                baseline[0], summary="Partial AI", description="Partial view"
            )
        },
    )
    assert partial_task.status is TaskStatus.SUCCEEDED
    partial = stack.service.load_payload(
        partial_task.output_bindings["understanding"]
    )
    assert partial.ai_baseline.segments[0].semantic_summary == "Partial AI"
    assert partial.ai_baseline.segments[1] == normal.ai_baseline.segments[1]
    assert partial.human_review == normal.human_review
    active = stack.projects.open_project(stack.project.project_id)
    assert active.artifact_bindings.stage_outputs["analyze_source"]["understanding"] == reviewed.artifact_id


def test_fresh_analysis_is_a_candidate_with_new_topology_and_empty_review(tmp_path):
    """Catch Fresh Analysis transferring old review or replacing approved Active output."""
    stack = _stack(tmp_path)
    reviewed = stack.service.save_review_version(
        stack.understanding_id,
        stack.service.draft_from(stack.understanding_id).set_global_summary("Reviewed"),
    )
    stack.projects.set_active(
        stack.project.project_id,
        "analyze_source",
        "understanding",
        reviewed.artifact_id,
    )
    stack.projects.approve_stage(stack.project.project_id, "analyze_source")
    old_ids = {
        item.segment_id for item in stack.service.load_payload(reviewed.artifact_id).ai_baseline.segments
    }

    fresh_task = stack.service.run_fresh_analysis(stack.project.project_id)

    assert fresh_task.status is TaskStatus.SUCCEEDED
    fresh_id = fresh_task.output_bindings["understanding"]
    fresh = stack.service.load_payload(fresh_id)
    assert fresh.human_review.to_dict() == {
        "global_overrides": {},
        "global_locks": {},
        "removed_segment_ids": [],
        "added_segments": [],
        "segment_overrides": {},
        "segment_locks": {},
    }
    assert old_ids.isdisjoint({item.segment_id for item in fresh.ai_baseline.segments})
    reopened = stack.projects.open_project(stack.project.project_id)
    approval = stack.repository.get_stage_run(
        reopened.stage_runs["analyze_source"]
    ).approval
    assert reopened.artifact_bindings.stage_outputs["analyze_source"]["understanding"] == reviewed.artifact_id
    assert approval.approved_outputs["understanding"] == reviewed.artifact_id


def test_cross_project_reuse_and_reversible_freshness_use_exact_transcript(tmp_path):
    """Catch reuse by latest/preferred instead of exact refs or irreversible stale state."""
    stack = _stack(tmp_path)
    reviewed = stack.service.save_review_version(
        stack.understanding_id,
        stack.service.draft_from(stack.understanding_id).set_global_summary("Reviewed"),
    )
    revision_count = len(
        stack.artifacts.list_revisions(
            stack.artifacts.get_revision(reviewed.artifact_id).family_id
        )
    )
    project_b = create_understanding_project(
        stack.projects, "Project B", stack.source.asset_id
    )

    transcript_reuse = stack.service.reuse_transcript(project_b.project_id)
    understanding_reuse = stack.service.reuse_understanding(project_b.project_id)

    assert transcript_reuse.operation is TaskOperation.REUSE
    assert understanding_reuse.operation is TaskOperation.REUSE
    assert transcript_reuse.output_bindings["transcript"] == stack.transcript_id
    assert understanding_reuse.output_bindings["understanding"] == reviewed.artifact_id
    assert len(
        stack.artifacts.list_revisions(
            stack.artifacts.get_revision(reviewed.artifact_id).family_id
        )
    ) == revision_count
    stage = stack.repository.get_stage_run(
        stack.projects.open_project(project_b.project_id).stage_runs["analyze_source"]
    )
    assert stage.approval is None

    stack.projects.approve_stage(project_b.project_id, "analyze_source")
    views = StageStateResolver(stack.repository, stack.catalog, stack.artifacts)
    assert views.get_view(project_b.project_id, "plan_source").runnable is True

    second_transcript = stack.runtime.execute_stage(
        project_b.project_id,
        "transcribe_source",
        output_adapter=transcript_output_adapter,
    ).output_bindings["transcript"]
    stack.projects.set_active(
        project_b.project_id, "transcribe_source", "transcript", second_transcript
    )
    assert views.get_view(project_b.project_id, "analyze_source").freshness is StageFreshness.STALE
    assert views.get_view(project_b.project_id, "plan_source").runnable is False
    with pytest.raises(LookupError, match="compatible"):
        stack.service.reuse_understanding(project_b.project_id)

    stack.projects.set_active(
        project_b.project_id,
        "transcribe_source",
        "transcript",
        stack.transcript_id,
    )
    assert views.get_view(project_b.project_id, "analyze_source").freshness is StageFreshness.FRESH
    assert views.get_view(project_b.project_id, "plan_source").runnable is True
