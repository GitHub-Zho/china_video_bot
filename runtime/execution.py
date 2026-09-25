"""Synchronous local Runtime execution over the logical ExecutorRegistry seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from artifacts import (
    ArtifactOwner,
    ArtifactProducer,
    ArtifactRepository,
    ProducerKind,
    StorageMode,
)
from workflow import Cardinality, ExecutorRegistry

from .models import (
    ArtifactBinding,
    InputBinding,
    ProjectRuntimeV1,
    RuntimeRef,
    RuntimeRefKind,
    TaskAttemptV1,
    TaskOperation,
    TaskStatus,
)
from .repository import RuntimeRepository
from .services import ProjectService, WorkflowCatalog, artifact_ids
from .tasks import LocalTaskManager
from .views import StageStateResolver


class StageNotRunnableError(RuntimeError):
    """Raised when DAG, approval, input, lifecycle, or activity gates block a Stage."""


@dataclass(frozen=True)
class ArtifactOutputSpec:
    """Executor result material to save through the public Artifact API."""

    owner: ArtifactOwner
    payload_schema_version: str
    content: bytes
    media_type: str
    storage: StorageMode = StorageMode.BLOB
    variant_key: str | None = None
    display_name: str | None = None
    base_artifact_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.owner, ArtifactOwner):
            raise ValueError("owner must be ArtifactOwner")
        if not isinstance(self.payload_schema_version, str) or not self.payload_schema_version.strip():
            raise ValueError("payload_schema_version must not be empty")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must not be empty")
        if not isinstance(self.storage, StorageMode):
            raise ValueError("storage must be StorageMode")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class RuntimeExecutionContext:
    project: ProjectRuntimeV1
    stage_id: str
    stage_run_id: str
    input_bindings: Mapping[str, InputBinding]


OutputValue = ArtifactOutputSpec | Sequence[ArtifactOutputSpec] | None
OutputAdapter = Callable[[Any, RuntimeExecutionContext], Mapping[str, OutputValue]]


def adapt_video_analysis_executor(
    analyzer: Callable[[str, str], Any],
    *,
    asset_resolver: Callable[[str], str],
    topic_resolver: Callable[[str], str],
    source_role: str = "primary_video",
    topic_slot: str = "topic",
) -> Callable[[RuntimeExecutionContext], Any]:
    """Adapt the existing analyzer using only exact Runtime-bound IDs."""
    if not callable(analyzer) or not callable(asset_resolver) or not callable(topic_resolver):
        raise ValueError("analyzer and resolvers must be callable")

    def execute(context: RuntimeExecutionContext) -> Any:
        source = context.input_bindings.get(source_role)
        topic = context.input_bindings.get(topic_slot)
        if not isinstance(source, RuntimeRef) or source.kind is not RuntimeRefKind.ASSET:
            raise ValueError(f"{source_role} must resolve to one Asset")
        if not isinstance(topic, RuntimeRef) or topic.kind is not RuntimeRefKind.ARTIFACT:
            raise ValueError(f"{topic_slot} must resolve to one Artifact")
        return analyzer(asset_resolver(source.id), topic_resolver(topic.id))

    return execute


class WorkflowRuntime:
    """Execute or reuse one Stage without replacing the legacy one-shot pipeline."""

    def __init__(
        self,
        repository: RuntimeRepository,
        workflows: WorkflowCatalog,
        artifacts: ArtifactRepository,
        executors: ExecutorRegistry,
        *,
        projects: ProjectService | None = None,
        tasks: LocalTaskManager | None = None,
    ) -> None:
        self.repository = repository
        self.workflows = workflows
        self.artifacts = artifacts
        self.executors = executors
        self.tasks = tasks or LocalTaskManager(repository)
        self.projects = projects or ProjectService(
            repository, workflows, artifacts, tasks=self.tasks
        )
        self.states = StageStateResolver(repository, workflows, artifacts)

    def execute_stage(
        self,
        project_id: str,
        stage_id: str,
        *,
        output_adapter: OutputAdapter,
    ) -> TaskAttemptV1:
        view = self.states.get_view(project_id, stage_id)
        if not view.runnable:
            raise StageNotRunnableError(
                f"Stage {stage_id} is blocked by: {', '.join(view.blocked_by)}"
            )
        project = self.repository.get_project(project_id)
        workflow = self.projects.workflow_for(project)
        stage = self._stage(workflow, stage_id)
        inputs = self.states.bindings.resolve_stage_inputs(project, workflow, stage_id)
        task = self.tasks.queue(
            project.stage_runs[stage_id],
            operation=TaskOperation.EXECUTE,
            executor_id=stage.executor,
            input_bindings=inputs,
        )
        task = self.tasks.start(task.task_id)
        context = RuntimeExecutionContext(
            project=project,
            stage_id=stage_id,
            stage_run_id=project.stage_runs[stage_id],
            input_bindings=inputs,
        )
        try:
            result = self.executors.execute(stage.executor, context)
            current = self.repository.get_task_attempt(task.task_id)
            if current.cancel_requested_at is not None:
                return self.tasks.cancel(task.task_id)
            specs = output_adapter(result, context)
            outputs = self._save_outputs(stage, context, specs)
            completed = self.tasks.succeed(task.task_id, outputs)
            if completed.status is TaskStatus.SUCCEEDED:
                self._set_initial_active(project_id, stage_id, outputs)
            return completed
        except Exception as error:
            current = self.repository.get_task_attempt(task.task_id)
            if current.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                return self.tasks.fail(
                    task.task_id,
                    code="stage_execution_failed",
                    message=str(error) or type(error).__name__,
                )
            raise

    def reuse_stage_outputs(
        self,
        project_id: str,
        stage_id: str,
        output_bindings: Mapping[str, object],
    ) -> TaskAttemptV1:
        view = self.states.get_view(project_id, stage_id)
        if not view.runnable:
            raise StageNotRunnableError(
                f"Stage {stage_id} is blocked by: {', '.join(view.blocked_by)}"
            )
        project = self.repository.get_project(project_id)
        workflow = self.projects.workflow_for(project)
        stage = self._stage(workflow, stage_id)
        inputs = self.states.bindings.resolve_stage_inputs(project, workflow, stage_id)
        task = self.tasks.queue(
            project.stage_runs[stage_id],
            operation=TaskOperation.REUSE,
            executor_id=stage.executor,
            input_bindings=inputs,
        )
        task = self.tasks.start(task.task_id)
        try:
            normalized = self._validate_existing_outputs(stage, output_bindings)
            expected = sorted(
                (ref.slot, ref.kind.value, ref.id)
                for ref in self.states.bindings.expected_input_refs(
                    project, workflow, stage_id
                )
            )
            for binding in normalized.values():
                for artifact_id in artifact_ids(binding):
                    revision = self.artifacts.get_revision(artifact_id)
                    actual = sorted(
                        (ref.slot, ref.kind.value, ref.id)
                        for ref in revision.input_refs
                    )
                    if actual != expected:
                        raise ValueError(
                            f"Artifact {artifact_id} does not match current Stage inputs"
                        )
            completed = self.tasks.succeed(task.task_id, normalized)
            self._set_initial_active(project_id, stage_id, normalized)
            return completed
        except Exception as error:
            return self.tasks.fail(
                task.task_id,
                code="reuse_failed",
                message=str(error) or type(error).__name__,
                retryable=False,
            )

    def _save_outputs(
        self,
        stage,
        context: RuntimeExecutionContext,
        raw_specs: Mapping[str, OutputValue],
    ) -> dict[str, ArtifactBinding]:
        if not isinstance(raw_specs, Mapping):
            raise ValueError("output_adapter must return a mapping")
        unknown = set(raw_specs) - set(stage.outputs)
        if unknown:
            raise ValueError(f"unknown output slots: {', '.join(sorted(unknown))}")
        input_refs = self.states.bindings.expected_input_refs(
            context.project, self.projects.workflow_for(context.project), context.stage_id
        )
        outputs: dict[str, ArtifactBinding] = {}
        for slot_name, slot in stage.outputs.items():
            value = raw_specs.get(slot_name)
            specs = self._normalize_specs(slot.cardinality, value, slot_name)
            saved_ids: list[str] = []
            for spec in specs:
                family = self.artifacts.get_or_create_family(
                    spec.owner,
                    slot.artifact_type,
                    variant_key=spec.variant_key,
                    display_name=spec.display_name,
                )
                draft = self.artifacts.create_draft(
                    family.family_id, base_artifact_id=spec.base_artifact_id
                )
                draft.payload_schema_version = spec.payload_schema_version
                draft.producer = ArtifactProducer(
                    kind=ProducerKind.EXECUTOR,
                    executor_id=stage.executor,
                    project_id=context.project.project_id,
                    stage_run_id=context.stage_run_id,
                )
                draft.input_refs = input_refs
                draft.metadata = dict(spec.metadata)
                draft.replace_content(
                    spec.content, media_type=spec.media_type, storage=spec.storage
                )
                saved_ids.append(self.artifacts.save_revision(draft).artifact_id)
            if slot.cardinality is Cardinality.ONE:
                outputs[slot_name] = saved_ids[0]
            elif slot.cardinality is Cardinality.OPTIONAL:
                outputs[slot_name] = None if not saved_ids else saved_ids[0]
            else:
                outputs[slot_name] = tuple(saved_ids)
        return outputs

    def _validate_existing_outputs(self, stage, raw) -> dict[str, ArtifactBinding]:
        unknown = set(raw) - set(stage.outputs)
        if unknown:
            raise ValueError(f"unknown output slots: {', '.join(sorted(unknown))}")
        normalized: dict[str, ArtifactBinding] = {}
        for slot_name, slot in stage.outputs.items():
            value = raw.get(slot_name)
            if slot.cardinality is Cardinality.MANY and value is None:
                value = []
            try:
                binding = slot.validate_binding(value)
            except ValueError as error:
                raise ValueError(f"invalid output {slot_name}: {error}") from error
            for artifact_id in artifact_ids(binding):
                revision = self.artifacts.get_revision(artifact_id)
                if revision.deleted_at is not None or revision.purged_at is not None:
                    raise ValueError(f"Artifact {artifact_id} is not live")
                if revision.artifact_type != slot.artifact_type:
                    raise ValueError(
                        f"Artifact {artifact_id} has type {revision.artifact_type}; "
                        f"expected {slot.artifact_type}"
                    )
            normalized[slot_name] = binding
        return normalized

    @staticmethod
    def _normalize_specs(cardinality, value, slot_name) -> tuple[ArtifactOutputSpec, ...]:
        if cardinality is Cardinality.ONE:
            if not isinstance(value, ArtifactOutputSpec):
                raise ValueError(f"output {slot_name} requires one ArtifactOutputSpec")
            return (value,)
        if cardinality is Cardinality.OPTIONAL:
            if value is None:
                return ()
            if not isinstance(value, ArtifactOutputSpec):
                raise ValueError(
                    f"output {slot_name} requires ArtifactOutputSpec or None"
                )
            return (value,)
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, ArtifactOutputSpec) for item in value
        ):
            raise ValueError(f"output {slot_name} requires a list of ArtifactOutputSpec")
        return tuple(value)

    def _set_initial_active(
        self, project_id: str, stage_id: str, outputs: Mapping[str, ArtifactBinding]
    ) -> None:
        self.projects.set_initial_active_outputs(project_id, stage_id, outputs)

    @staticmethod
    def _stage(workflow, stage_id):
        for stage in workflow.stages:
            if stage.stage_id == stage_id:
                return stage
        raise ValueError(f"unknown Stage: {stage_id}")
