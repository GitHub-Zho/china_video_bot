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
    RuntimeConflictError,
    StageFreshness,
    StageReview,
    StageStateResolver,
    TaskOperation,
    TaskStatus,
    WorkflowCatalog,
    WorkflowRuntime,
    adapt_video_analysis_executor,
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

    archived = projects.archive_project(project.project_id)
    assert archived.lifecycle_status.value == "archived"
    restored = projects.restore_project(project.project_id)
    assert restored.lifecycle_status.value == "active"
    assert restored.deleted_at is None


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

    artifacts.restore_revision(understanding.artifact_id)
    projects.delete_project(project.project_id)
    retained = artifacts.get_revision(understanding.artifact_id)
    assert retained.deleted_at is None
    assert retained.purged_at is None
    assert artifacts.resolve_content(understanding.artifact_id).is_file()


def test_runtime_executes_existing_adapter_without_replacing_active_and_can_reuse(
    runtime_stack,
) -> None:
    """Catch executor bypass, silent Active movement, or duplicate reuse revisions."""
    repository, artifacts, catalog, projects = runtime_stack
    calls = []

    asset_urls = {"asset_video_1": "https://example.invalid/video"}

    def legacy_analyzer(url, topic, sample_interval=4.0):
        call = (url, topic, sample_interval)
        calls.append(call)
        return {"summary": topic, "url": url}

    def resolve_topic(artifact_id):
        topic_payload = json.loads(
            artifacts.resolve_content(artifact_id).read_text(encoding="utf-8")
        )
        return topic_payload["topic"]

    registry = ExecutorRegistry()
    register_existing_executors(
        registry,
        analyzer=adapt_video_analysis_executor(
            legacy_analyzer,
            asset_resolver=asset_urls.__getitem__,
            topic_resolver=resolve_topic,
        ),
    )
    registry.register("test.plan", lambda: None)
    registry.register("test.timeline", lambda: None)
    tasks = LocalTaskManager(repository)
    runtime = WorkflowRuntime(
        repository, catalog, artifacts, registry, projects=projects, tasks=tasks
    )
    exact_workflow = WorkflowDefinition(
        "video_grounded.exact-inputs",
        "1",
        "video_grounded",
        (
            StageDefinition(
                "analyze_source",
                "analyze",
                "video_grounded.analyze_source",
                inputs={
                    "topic": ArtifactSlotDefinition(
                        "analysis_prompt", Cardinality.ONE
                    )
                },
                outputs={
                    "understanding": ArtifactSlotDefinition(
                        "video_understanding", Cardinality.ONE
                    )
                },
                approval_required=True,
            ),
        ),
    )
    catalog.register(exact_workflow)
    project = projects.create_project(
        "Execution",
        workflow_id=exact_workflow.workflow_id,
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )
    first_topic = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="analysis_prompt",
        payload={"topic": "first"},
        input_refs=(),
    )
    projects.set_project_input(project.project_id, "topic", first_topic.artifact_id)

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
        output_adapter=output_adapter,
    )
    first_id = first.output_bindings["understanding"]
    projects.approve_stage(project.project_id, "analyze_source")

    second_topic = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="analysis_prompt",
        payload={"topic": "second"},
        input_refs=(),
    )
    projects.set_project_input(project.project_id, "topic", second_topic.artifact_id)
    second = runtime.execute_stage(
        project.project_id,
        "analyze_source",
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
        workflow_id=exact_workflow.workflow_id,
        workflow_version="1",
        source_bindings={"primary_video": "asset_video_1"},
    )
    projects.set_project_input(other.project_id, "topic", first_topic.artifact_id)
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


def test_incomplete_required_outputs_are_stale_and_cannot_be_approved(
    runtime_stack,
) -> None:
    """Catch partial Active output snapshots being treated as complete."""
    repository, artifacts, catalog, projects = runtime_stack
    workflow = WorkflowDefinition(
        "multi_output.default",
        "1",
        "test",
        (
            StageDefinition(
                "analyze",
                "analyze",
                "test.multi",
                outputs={
                    "left": ArtifactSlotDefinition("summary", Cardinality.ONE),
                    "right": ArtifactSlotDefinition("summary", Cardinality.ONE),
                },
                approval_required=True,
            ),
        ),
    )
    catalog.register(workflow)
    project = projects.create_project(
        "Partial outputs",
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.workflow_version,
        source_bindings={"source": "asset_1"},
    )
    left = _save_artifact(
        artifacts,
        owner=ArtifactOwner.project(project.project_id),
        artifact_type="summary",
        payload={"side": "left"},
        input_refs=(ArtifactInputRef("source", InputRefKind.ASSET, "asset_1"),),
    )
    projects.set_active(project.project_id, "analyze", "left", left.artifact_id)

    view = StageStateResolver(repository, catalog, artifacts).get_view(
        project.project_id, "analyze"
    )
    assert view.freshness is StageFreshness.STALE
    assert view.review is StageReview.NEEDS_REVIEW
    with pytest.raises(RuntimeConflictError, match="required output"):
        projects.approve_stage(project.project_id, "analyze")
