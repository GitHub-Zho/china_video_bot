"""Project lifecycle services and Runtime-to-Artifact reference integration."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping

from artifacts import ArtifactRepository
from workflow import WorkflowDefinition

from .models import (
    ApprovalSnapshot,
    ArtifactBinding,
    ProjectArtifactBindings,
    ProjectLifecycleStatus,
    ProjectRuntimeV1,
    StageRunV1,
    WorkflowPin,
    normalize_artifact_binding,
)
from .repository import RuntimeConflictError, RuntimeNotFoundError, RuntimeRepository, now_utc
from .tasks import LocalTaskManager


class WorkflowNotFoundError(LookupError):
    """Raised when an exact pinned Workflow definition is unavailable."""


class WorkflowCatalog:
    """Resolve immutable Workflow definitions by exact ID and version."""

    def __init__(self, workflows: Iterable[WorkflowDefinition] = ()) -> None:
        self._workflows: dict[tuple[str, str], WorkflowDefinition] = {}
        for workflow in workflows:
            self.register(workflow)

    def register(self, workflow: WorkflowDefinition) -> None:
        if not isinstance(workflow, WorkflowDefinition):
            raise ValueError("workflow must be WorkflowDefinition")
        key = (workflow.workflow_id, workflow.workflow_version)
        if key in self._workflows:
            raise ValueError(f"duplicate Workflow definition: {key[0]}@{key[1]}")
        self._workflows[key] = workflow

    def get(self, workflow_id: str, workflow_version: str) -> WorkflowDefinition:
        try:
            return self._workflows[(workflow_id, workflow_version)]
        except KeyError as error:
            raise WorkflowNotFoundError(
                f"Workflow not found: {workflow_id}@{workflow_version}"
            ) from error


def artifact_ids(binding: ArtifactBinding) -> tuple[str, ...]:
    if binding is None:
        return ()
    return binding if isinstance(binding, tuple) else (binding,)


class ProjectService:
    """Create, resume, select and approve exact Project Runtime state."""

    def __init__(
        self,
        repository: RuntimeRepository,
        workflows: WorkflowCatalog,
        artifacts: ArtifactRepository,
        *,
        tasks: LocalTaskManager | None = None,
    ) -> None:
        self.repository = repository
        self.workflows = workflows
        self.artifacts = artifacts
        self.tasks = tasks or LocalTaskManager(repository)

    def create_project(
        self,
        display_name: str,
        *,
        workflow_id: str,
        workflow_version: str,
        source_bindings: Mapping[str, object],
        project_inputs: Mapping[str, object] | None = None,
        project_id: str | None = None,
    ) -> ProjectRuntimeV1:
        workflow = self.workflows.get(workflow_id, workflow_version)
        timestamp = now_utc()
        resolved_project_id = project_id or self.repository.new_id("proj")
        stages = tuple(
            StageRunV1(
                stage_run_id=self.repository.new_id("stage_run"),
                project_id=resolved_project_id,
                stage_id=stage.stage_id,
                next_attempt=1,
                task_ids=(),
                approval=None,
                created_at=timestamp,
                updated_at=timestamp,
            )
            for stage in workflow.stages
        )
        project = ProjectRuntimeV1(
            project_id=resolved_project_id,
            display_name=display_name,
            workflow=WorkflowPin(workflow_id, workflow_version),
            source_bindings=source_bindings,
            artifact_bindings=ProjectArtifactBindings(
                project_inputs=project_inputs or {},
                stage_outputs={stage.stage_id: {} for stage in workflow.stages},
            ),
            stage_runs={stage.stage_id: stage.stage_run_id for stage in stages},
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._validate_project_artifacts(project)
        return self.repository.create_project(project, stages)

    def get_project(self, project_id: str) -> ProjectRuntimeV1:
        return self.repository.get_project(project_id)

    def workflow_for(self, project: ProjectRuntimeV1) -> WorkflowDefinition:
        return self.workflows.get(
            project.workflow.workflow_id, project.workflow.workflow_version
        )

    def open_project(self, project_id: str) -> ProjectRuntimeV1:
        project = self.repository.get_project(project_id)
        workflow = self.workflow_for(project)
        expected_stages = {stage.stage_id for stage in workflow.stages}
        if set(project.stage_runs) != expected_stages:
            raise RuntimeConflictError(
                "Project StageRun mapping does not match its pinned Workflow"
            )
        for stage_id, stage_run_id in project.stage_runs.items():
            stage = self.repository.get_stage_run(stage_run_id)
            if stage.project_id != project_id or stage.stage_id != stage_id:
                raise RuntimeConflictError("Project references an incompatible StageRun")
        self._validate_project_artifacts(project)
        self.tasks.recover_project(project_id)
        return self.repository.get_project(project_id)

    resume_project = open_project

    def archive_project(self, project_id: str) -> ProjectRuntimeV1:
        project = self.repository.get_project(project_id)
        if project.deleted_at is not None:
            raise RuntimeConflictError("deleted Project cannot be archived")
        return self.repository.save_project(
            replace(
                project,
                lifecycle_status=ProjectLifecycleStatus.ARCHIVED,
                updated_at=now_utc(),
            )
        )

    def delete_project(self, project_id: str) -> ProjectRuntimeV1:
        project = self.repository.get_project(project_id)
        if project.deleted_at is not None:
            return project
        return self.repository.save_project(
            replace(project, deleted_at=now_utc(), updated_at=now_utc())
        )

    def restore_project(self, project_id: str) -> ProjectRuntimeV1:
        project = self.repository.get_project(project_id)
        self.workflow_for(project)
        restored = replace(project, deleted_at=None, updated_at=now_utc())
        self._validate_project_artifacts(restored)
        return self.repository.save_project(restored)

    def set_active(
        self,
        project_id: str,
        stage_id: str,
        output_slot: str,
        binding: object,
    ) -> ProjectRuntimeV1:
        project = self.repository.get_project(project_id)
        self._ensure_mutable(project)
        workflow = self.workflow_for(project)
        stage = self._stage(workflow, stage_id)
        try:
            slot = stage.outputs[output_slot]
        except KeyError as error:
            raise ValueError(f"unknown output slot: {stage_id}.{output_slot}") from error
        normalized_input = list(binding) if isinstance(binding, tuple) else binding
        normalized = slot.validate_binding(normalized_input)
        self._validate_artifact_binding(normalized, slot.artifact_type)

        stage_outputs = {
            key: dict(outputs)
            for key, outputs in project.artifact_bindings.stage_outputs.items()
        }
        stage_outputs.setdefault(stage_id, {})[output_slot] = normalized
        updated = replace(
            project,
            artifact_bindings=ProjectArtifactBindings(
                project_inputs=project.artifact_bindings.project_inputs,
                stage_outputs=stage_outputs,
            ),
            updated_at=now_utc(),
        )
        return self.repository.save_project(updated)

    def set_project_input(
        self, project_id: str, input_key: str, binding: object
    ) -> ProjectRuntimeV1:
        project = self.repository.get_project(project_id)
        self._ensure_mutable(project)
        normalized = normalize_artifact_binding(binding, f"project input {input_key}")
        self._validate_artifact_binding(normalized)
        project_inputs = dict(project.artifact_bindings.project_inputs)
        project_inputs[input_key] = normalized
        return self.repository.save_project(
            replace(
                project,
                artifact_bindings=ProjectArtifactBindings(
                    project_inputs=project_inputs,
                    stage_outputs=project.artifact_bindings.stage_outputs,
                ),
                updated_at=now_utc(),
            )
        )

    def approve_stage(self, project_id: str, stage_id: str) -> StageRunV1:
        project = self.repository.get_project(project_id)
        self._ensure_mutable(project)
        self._stage(self.workflow_for(project), stage_id)
        outputs = project.artifact_bindings.stage_outputs.get(stage_id, {})
        if not any(artifact_ids(binding) for binding in outputs.values()):
            raise RuntimeConflictError("Stage has no Active output to approve")
        stage_run = self.repository.get_stage_run(project.stage_runs[stage_id])
        return self.repository.save_stage_run(
            replace(
                stage_run,
                approval=ApprovalSnapshot(dict(outputs), now_utc()),
                updated_at=now_utc(),
            )
        )

    def clear_approval(self, project_id: str, stage_id: str) -> StageRunV1:
        project = self.repository.get_project(project_id)
        self._ensure_mutable(project)
        stage_run = self.repository.get_stage_run(project.stage_runs[stage_id])
        return self.repository.save_stage_run(
            replace(stage_run, approval=None, updated_at=now_utc())
        )

    @staticmethod
    def _stage(workflow: WorkflowDefinition, stage_id: str):
        for stage in workflow.stages:
            if stage.stage_id == stage_id:
                return stage
        raise ValueError(f"unknown Stage: {stage_id}")

    @staticmethod
    def _ensure_mutable(project: ProjectRuntimeV1) -> None:
        if project.deleted_at is not None:
            raise RuntimeConflictError("deleted Project cannot be changed")
        if project.lifecycle_status is not ProjectLifecycleStatus.ACTIVE:
            raise RuntimeConflictError("archived Project cannot be changed")

    def _validate_artifact_binding(
        self, binding: ArtifactBinding, expected_type: str | None = None
    ) -> None:
        for artifact_id in artifact_ids(binding):
            revision = self.artifacts.get_revision(artifact_id)
            if revision.deleted_at is not None or revision.purged_at is not None:
                raise RuntimeConflictError("Runtime can reference only live Artifacts")
            if expected_type is not None and revision.artifact_type != expected_type:
                raise RuntimeConflictError(
                    f"Artifact {artifact_id} has type {revision.artifact_type}; "
                    f"expected {expected_type}"
                )

    def _validate_project_artifacts(self, project: ProjectRuntimeV1) -> None:
        for binding in project.artifact_bindings.project_inputs.values():
            self._validate_artifact_binding(binding)
        workflow = self.workflow_for(project)
        stages = {stage.stage_id: stage for stage in workflow.stages}
        for stage_id, outputs in project.artifact_bindings.stage_outputs.items():
            stage = stages.get(stage_id)
            if stage is None:
                raise RuntimeConflictError(f"unknown Stage binding: {stage_id}")
            for slot_name, binding in outputs.items():
                if slot_name not in stage.outputs:
                    raise RuntimeConflictError(
                        f"unknown output binding: {stage_id}.{slot_name}"
                    )
                self._validate_artifact_binding(
                    binding, stage.outputs[slot_name].artifact_type
                )


class RuntimeArtifactReferenceChecker:
    """Callable injected into ArtifactRepository to protect retained Runtime refs."""

    def __init__(self, repository: RuntimeRepository) -> None:
        self.repository = repository

    def __call__(self, artifact_id: str) -> bool:
        for project in self.repository.list_projects(include_deleted=True):
            if any(
                artifact_id in artifact_ids(binding)
                for binding in project.artifact_bindings.project_inputs.values()
            ):
                return True
            for outputs in project.artifact_bindings.stage_outputs.values():
                if any(
                    artifact_id in artifact_ids(binding)
                    for binding in outputs.values()
                ):
                    return True
            for stage_run_id in project.stage_runs.values():
                try:
                    approval = self.repository.get_stage_run(stage_run_id).approval
                except RuntimeNotFoundError:
                    continue
                if approval is not None and any(
                    artifact_id in artifact_ids(binding)
                    for binding in approval.approved_outputs.values()
                ):
                    return True
        return False

