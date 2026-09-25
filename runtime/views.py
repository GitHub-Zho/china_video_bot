"""Derived Stage activity, freshness, review and runnable state."""

from __future__ import annotations

from artifacts import ArtifactRepository

from .bindings import BindingResolutionError, BindingResolver
from .models import (
    ProjectLifecycleStatus,
    ProjectRuntimeV1,
    StageActivity,
    StageFreshness,
    StageReview,
    StageView,
    TaskStatus,
)
from .repository import RuntimeRepository
from .services import WorkflowCatalog, artifact_ids, validate_stage_output_snapshot


class StageStateResolver:
    def __init__(
        self,
        repository: RuntimeRepository,
        workflows: WorkflowCatalog,
        artifacts: ArtifactRepository,
    ) -> None:
        self.repository = repository
        self.workflows = workflows
        self.artifacts = artifacts
        self.bindings = BindingResolver(artifacts)

    def get_view(self, project_id: str, stage_id: str) -> StageView:
        project = self.repository.get_project(project_id)
        workflow = self.workflows.get(
            project.workflow.workflow_id, project.workflow.workflow_version
        )
        stage = self._stage(workflow, stage_id)
        stage_run = self.repository.get_stage_run(project.stage_runs[stage_id])
        tasks = self.repository.list_task_attempts(stage_run.stage_run_id)
        latest = tasks[-1] if tasks else None

        if any(task.status is TaskStatus.RUNNING for task in tasks):
            activity = StageActivity.RUNNING
        elif any(task.status is TaskStatus.QUEUED for task in tasks):
            activity = StageActivity.QUEUED
        else:
            activity = StageActivity.IDLE

        freshness = self._freshness(project, workflow, stage_id, {})
        review = self._review(project, stage, stage_run)
        blocked = self._blocked_by(project, workflow, stage_id, activity)
        return StageView(
            activity=activity,
            freshness=freshness,
            review=review,
            runnable=not blocked,
            blocked_by=tuple(blocked),
            latest_attempt_status=None if latest is None else latest.status.value,
        )

    def can_run_stage(self, project_id: str, stage_id: str) -> bool:
        return self.get_view(project_id, stage_id).runnable

    def blocked_by(self, project_id: str, stage_id: str) -> tuple[str, ...]:
        return self.get_view(project_id, stage_id).blocked_by

    def get_runnable_stages(self, project_id: str) -> tuple[str, ...]:
        project = self.repository.get_project(project_id)
        workflow = self.workflows.get(
            project.workflow.workflow_id, project.workflow.workflow_version
        )
        return tuple(
            stage_id
            for stage_id in workflow.topological_stage_ids()
            if self.can_run_stage(project_id, stage_id)
        )

    def _freshness(self, project, workflow, stage_id, memo) -> StageFreshness:
        if stage_id in memo:
            return memo[stage_id]
        stage = self._stage(workflow, stage_id)
        outputs = project.artifact_bindings.stage_outputs.get(stage_id, {})
        active_ids = tuple(
            artifact_id
            for binding in outputs.values()
            for artifact_id in artifact_ids(binding)
        )
        if not active_ids:
            memo[stage_id] = StageFreshness.NO_OUTPUT
            return memo[stage_id]

        try:
            normalized_outputs = validate_stage_output_snapshot(
                stage, outputs, self.artifacts
            )
        except (RuntimeError, LookupError, ValueError):
            memo[stage_id] = StageFreshness.STALE
            return memo[stage_id]
        active_ids = tuple(
            artifact_id
            for binding in normalized_outputs.values()
            for artifact_id in artifact_ids(binding)
        )

        if any(
            self._freshness(project, workflow, dependency, memo)
            is not StageFreshness.FRESH
            for dependency in stage.depends_on
        ):
            memo[stage_id] = StageFreshness.STALE
            return memo[stage_id]

        try:
            expected = self.bindings.expected_input_refs(project, workflow, stage_id)
        except (BindingResolutionError, LookupError):
            memo[stage_id] = StageFreshness.STALE
            return memo[stage_id]
        expected_key = sorted((ref.slot, ref.kind.value, ref.id) for ref in expected)
        for artifact_id in active_ids:
            try:
                revision = self.artifacts.get_revision(artifact_id)
            except LookupError:
                memo[stage_id] = StageFreshness.STALE
                return memo[stage_id]
            if revision.deleted_at is not None or revision.purged_at is not None:
                memo[stage_id] = StageFreshness.STALE
                return memo[stage_id]
            actual_key = sorted(
                (ref.slot, ref.kind.value, ref.id) for ref in revision.input_refs
            )
            if actual_key != expected_key:
                memo[stage_id] = StageFreshness.STALE
                return memo[stage_id]
        memo[stage_id] = StageFreshness.FRESH
        return memo[stage_id]

    def _review(self, project, stage, stage_run) -> StageReview:
        if not stage.approval_required:
            return StageReview.NOT_REQUIRED
        active = dict(project.artifact_bindings.stage_outputs.get(stage.stage_id, {}))
        try:
            active = validate_stage_output_snapshot(stage, active, self.artifacts)
        except (RuntimeError, LookupError, ValueError):
            return StageReview.NEEDS_REVIEW
        if stage_run.approval is not None and dict(
            stage_run.approval.approved_outputs
        ) == active:
            return StageReview.APPROVED
        return StageReview.NEEDS_REVIEW

    def _blocked_by(self, project, workflow, stage_id, activity) -> list[str]:
        blocked: list[str] = []
        if (
            project.deleted_at is not None
            or project.lifecycle_status is not ProjectLifecycleStatus.ACTIVE
        ):
            blocked.append("project_inactive")
        if activity is not StageActivity.IDLE:
            blocked.append("task_active")

        stage = self._stage(workflow, stage_id)
        memo: dict[str, StageFreshness] = {}
        for dependency_id in stage.depends_on:
            dependency = self._stage(workflow, dependency_id)
            dependency_run = self.repository.get_stage_run(
                project.stage_runs[dependency_id]
            )
            if (
                self._freshness(project, workflow, dependency_id, memo)
                is not StageFreshness.FRESH
            ):
                blocked.append(dependency_id)
                continue
            if dependency.approval_required and self._review(
                project, dependency, dependency_run
            ) is not StageReview.APPROVED:
                blocked.append(dependency_id)

        try:
            self.bindings.resolve_stage_inputs(project, workflow, stage_id)
        except BindingResolutionError as error:
            marker = f"input:{error.slot}" if error.slot else "inputs"
            blocked.append(marker)
        return list(dict.fromkeys(blocked))

    @staticmethod
    def _stage(workflow, stage_id):
        for stage in workflow.stages:
            if stage.stage_id == stage_id:
                return stage
        raise BindingResolutionError(f"unknown Stage: {stage_id}")
