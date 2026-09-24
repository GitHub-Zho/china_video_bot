import json

import pytest

from artifacts import (
    ArtifactInputRef,
    ArtifactOwner,
    ArtifactProducer,
    ArtifactRepository,
    InputRefKind,
    ProducerKind,
    PurgeBlockedError,
    StorageMode,
)
from runtime import (
    ArtifactOutputSpec,
    BindingResolutionError,
    LocalTaskManager,
    ProjectService,
    RuntimeArtifactReferenceChecker,
    RuntimeRepository,
    StageFreshness,
    StageReview,
    StageStateResolver,
    TaskOperation,
    TaskStatus,
    WorkflowCatalog,
    WorkflowRuntime,
)
from workflow import (
    ArtifactSlotDefinition,
    Cardinality,
    ExecutorRegistry,
    StageDefinition,
    WorkflowDefinition,
    register_existing_executors,
)


def _workflow(version: str = "1") -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="video_grounded.default",
        workflow_version=version,
        mode="video_grounded",
        stages=(
            StageDefinition(
                "analyze_source",
                "analyze",
                "video_grounded.analyze_source",
                outputs={
                    "understanding": ArtifactSlotDefinition(
                        "video_understanding", Cardinality.ONE
                    )
                },
                approval_required=True,
            ),
            StageDefinition(
                "plan",
                "plan",
                "test.plan",
                inputs={
                    "understanding": ArtifactSlotDefinition(
                        "video_understanding", Cardinality.ONE
                    )
                },
                outputs={"plan": ArtifactSlotDefinition("edit_plan", Cardinality.ONE)},
                depends_on=("analyze_source",),
                approval_required=True,
            ),
            StageDefinition(
                "timeline",
                "timeline",
                "test.timeline",
                inputs={"plan": ArtifactSlotDefinition("edit_plan", Cardinality.ONE)},
                outputs={
                    "timeline": ArtifactSlotDefinition("timeline", Cardinality.ONE)
                },
                depends_on=("plan",),
            ),
        ),
    )


@pytest.fixture
def runtime_stack(tmp_path):
    runtime_repository = RuntimeRepository(tmp_path / "runtime")
    artifact_repository = ArtifactRepository(
        tmp_path / "artifacts",
        external_reference_checker=RuntimeArtifactReferenceChecker(runtime_repository),
    )
    catalog = WorkflowCatalog((_workflow("1"),))
    projects = ProjectService(runtime_repository, catalog, artifact_repository)
    return runtime_repository, artifact_repository, catalog, projects


def _save_artifact(
    artifacts: ArtifactRepository,
    *,
    owner: ArtifactOwner,
    artifact_type: str,
    payload: dict,
    input_refs: tuple[ArtifactInputRef, ...],
):
    family = artifacts.get_or_create_family(owner, artifact_type)
    draft = artifacts.create_draft(family.family_id)
    draft.payload_schema_version = f"legacy.{artifact_type}.v1"
    draft.producer = ArtifactProducer(ProducerKind.IMPORT)
    draft.input_refs = input_refs
    draft.replace_content(
        json.dumps(payload, sort_keys=True).encode(),
        media_type="application/json",
        storage=StorageMode.BLOB,
    )
    return artifacts.save_revision(draft)


def test_project_reopen_keeps_pinned_workflow_and_exact_state(runtime_stack) -> None:
    """Catch resume silently selecting a newer workflow or losing source/stage identity."""
    repository, artifacts, catalog, projects = runtime_stack
    project = projects.create_project(
        "Pinned project",
        workflow_id="video_grounded.default",
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )
    catalog.register(_workflow("2"))

    reopened = projects.open_project(project.project_id)

    assert reopened.project_id == project.project_id
    assert reopened.workflow.workflow_version == "1"
    assert reopened.source_bindings == {"primary_video": "asset_video_1"}
    assert reopened.stage_runs == project.stage_runs
    assert projects.workflow_for(reopened).workflow_version == "1"


def test_active_approval_and_freshness_are_separate_and_reversible(runtime_stack) -> None:
    """Catch save/preferred/active/approval collapse and irreversible dirty flags."""
    repository, artifacts, catalog, projects = runtime_stack
    project = projects.create_project(
        "Bindings",
        workflow_id="video_grounded.default",
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )
    source_ref = ArtifactInputRef("primary_video", InputRefKind.ASSET, "asset_video_1")
    u3 = _save_artifact(
        artifacts,
        owner=ArtifactOwner.asset("asset_video_1"),
        artifact_type="video_understanding",
        payload={"version": 3},
        input_refs=(source_ref,),
    )
    u7 = _save_artifact(
        artifacts,
        owner=ArtifactOwner.asset("asset_video_1"),
        artifact_type="video_understanding",
        payload={"version": 7},
        input_refs=(source_ref,),
    )
    p4 = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="edit_plan",
        payload={"version": 4},
        input_refs=(
            source_ref,
            ArtifactInputRef("understanding", InputRefKind.ARTIFACT, u3.artifact_id),
        ),
    )
    t2 = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="timeline",
        payload={"version": 2},
        input_refs=(
            source_ref,
            ArtifactInputRef("plan", InputRefKind.ARTIFACT, p4.artifact_id),
        ),
    )

    projects.set_active(project.project_id, "analyze_source", "understanding", u3.artifact_id)
    projects.approve_stage(project.project_id, "analyze_source")
    projects.set_active(project.project_id, "plan", "plan", p4.artifact_id)
    projects.approve_stage(project.project_id, "plan")
    projects.set_active(project.project_id, "timeline", "timeline", t2.artifact_id)
    views = StageStateResolver(repository, catalog, artifacts)

    assert views.get_view(project.project_id, "timeline").freshness is StageFreshness.FRESH

    projects.set_active(project.project_id, "analyze_source", "understanding", u7.artifact_id)
    analyze_view = views.get_view(project.project_id, "analyze_source")
    assert analyze_view.review is StageReview.NEEDS_REVIEW
    assert views.get_view(project.project_id, "plan").freshness is StageFreshness.STALE
    assert views.get_view(project.project_id, "timeline").freshness is StageFreshness.STALE

    projects.set_active(project.project_id, "analyze_source", "understanding", u3.artifact_id)
    assert views.get_view(project.project_id, "plan").freshness is StageFreshness.FRESH
    assert views.get_view(project.project_id, "timeline").freshness is StageFreshness.FRESH


def test_runtime_references_block_purge_and_project_delete_keeps_source_artifact(
    runtime_stack,
) -> None:
    """Catch Runtime references being bypassed or Project deletion cascading to source data."""
    repository, artifacts, catalog, projects = runtime_stack
    project = projects.create_project(
        "Purge safety",
        workflow_id="video_grounded.default",
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )
    understanding = _save_artifact(
        artifacts,
        owner=ArtifactOwner.asset("asset_video_1"),
        artifact_type="video_understanding",
        payload={"summary": "reusable"},
        input_refs=(
            ArtifactInputRef("primary_video", InputRefKind.ASSET, "asset_video_1"),
        ),
    )
    projects.set_active(
        project.project_id, "analyze_source", "understanding", understanding.artifact_id
    )
    projects.approve_stage(project.project_id, "analyze_source")
    artifacts.soft_delete_revision(understanding.artifact_id)

    with pytest.raises(PurgeBlockedError, match="external reference"):
        artifacts.purge_revision(understanding.artifact_id)

    projects.delete_project(project.project_id)
    retained = artifacts.get_revision(understanding.artifact_id)
    assert retained.purged_at is None


def test_runtime_executes_existing_adapter_without_replacing_active_and_can_reuse(
    runtime_stack,
) -> None:
    """Catch executor bypass, silent Active movement, or duplicate reuse revisions."""
    repository, artifacts, catalog, projects = runtime_stack
    calls = []

    def analyzer(url, topic, sample_interval=4.0):
        calls.append((url, topic, sample_interval))
        return {"summary": topic, "url": url}

    registry = ExecutorRegistry()
    register_existing_executors(registry, analyzer=analyzer)
    registry.register("test.plan", lambda: None)
    registry.register("test.timeline", lambda: None)
    tasks = LocalTaskManager(repository)
    runtime = WorkflowRuntime(
        repository, catalog, artifacts, registry, projects=projects, tasks=tasks
    )
    project = projects.create_project(
        "Execution",
        workflow_id="video_grounded.default",
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )

    def output_adapter(result, context):
        return {
            "understanding": ArtifactOutputSpec(
                owner=ArtifactOwner.asset("asset_video_1"),
                payload_schema_version="legacy.video_understanding.v1",
                content=json.dumps(result, sort_keys=True).encode(),
                media_type="application/json",
            )
        }

    first = runtime.execute_stage(
        project.project_id,
        "analyze_source",
        executor_args=("https://example.invalid/video", "first"),
        output_adapter=output_adapter,
    )
    first_id = first.output_bindings["understanding"]
    projects.approve_stage(project.project_id, "analyze_source")

    second = runtime.execute_stage(
        project.project_id,
        "analyze_source",
        executor_args=("https://example.invalid/video", "second"),
        output_adapter=output_adapter,
    )
    assert second.status is TaskStatus.SUCCEEDED
    assert second.output_bindings["understanding"] != first_id
    reopened = projects.open_project(project.project_id)
    assert reopened.artifact_bindings.stage_outputs["analyze_source"]["understanding"] == first_id
    assert repository.get_stage_run(
        reopened.stage_runs["analyze_source"]
    ).approval.approved_outputs["understanding"] == first_id
    assert calls == [
        ("https://example.invalid/video", "first", 4.0),
        ("https://example.invalid/video", "second", 4.0),
    ]

    other = projects.create_project(
        "Reuse",
        workflow_id="video_grounded.default",
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )
    before = len(artifacts.list_revisions(artifacts.get_revision(first_id).family_id))
    reused = runtime.reuse_stage_outputs(
        other.project_id, "analyze_source", {"understanding": first_id}
    )
    after = len(artifacts.list_revisions(artifacts.get_revision(first_id).family_id))

    assert reused.operation is TaskOperation.REUSE
    assert reused.output_bindings == {"understanding": first_id}
    assert before == after


def test_binding_resolution_rejects_ambiguous_upstream_slots(runtime_stack) -> None:
    """Catch a Runtime silently choosing one of two matching upstream outputs."""
    repository, artifacts, catalog, projects = runtime_stack
    ambiguous = WorkflowDefinition(
        "ambiguous.default",
        "1",
        "test",
        (
            StageDefinition(
                "left",
                "analyze",
                "test.left",
                outputs={"shared": ArtifactSlotDefinition("summary", Cardinality.ONE)},
            ),
            StageDefinition(
                "right",
                "analyze",
                "test.right",
                outputs={"shared": ArtifactSlotDefinition("summary", Cardinality.ONE)},
            ),
            StageDefinition(
                "join",
                "plan",
                "test.join",
                inputs={"shared": ArtifactSlotDefinition("summary", Cardinality.ONE)},
                depends_on=("left", "right"),
            ),
        ),
    )
    catalog.register(ambiguous)
    project = projects.create_project(
        "Ambiguous",
        workflow_id=ambiguous.workflow_id,
        workflow_version=ambiguous.workflow_version,
        source_bindings={"source": "asset_1"},
    )
    left = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="summary",
        payload={"side": "left"},
        input_refs=(ArtifactInputRef("source", InputRefKind.ASSET, "asset_1"),),
    )
    right = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="summary",
        payload={"side": "right"},
        input_refs=(ArtifactInputRef("source", InputRefKind.ASSET, "asset_1"),),
    )
    projects.set_active(project.project_id, "left", "shared", left.artifact_id)
    projects.set_active(project.project_id, "right", "shared", right.artifact_id)

    with pytest.raises(BindingResolutionError, match="ambiguous"):
        StageStateResolver(repository, catalog, artifacts).bindings.resolve_stage_inputs(
            projects.get_project(project.project_id), ambiguous, "join"
        )
