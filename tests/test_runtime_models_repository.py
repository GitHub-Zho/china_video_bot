from dataclasses import replace

import pytest

from runtime.models import (
    ProjectArtifactBindings,
    ProjectRuntimeV1,
    RuntimeRef,
    RuntimeRefKind,
    StageRunV1,
    TaskAttemptV1,
    TaskOperation,
    TaskStatus,
    WorkflowPin,
)
from runtime.repository import RuntimeRepository
from runtime.tasks import LocalTaskManager


def _project() -> ProjectRuntimeV1:
    return ProjectRuntimeV1(
        project_id="proj_1",
        display_name="Runtime test",
        workflow=WorkflowPin("test.default", "1"),
        source_bindings={"primary_video": "asset_1"},
        artifact_bindings=ProjectArtifactBindings(),
        stage_runs={"analyze": "stage_run_1"},
        created_at="2026-09-24T12:00:00+00:00",
        updated_at="2026-09-24T12:00:00+00:00",
    )


def _stage() -> StageRunV1:
    return StageRunV1(
        stage_run_id="stage_run_1",
        project_id="proj_1",
        stage_id="analyze",
        next_attempt=1,
        task_ids=(),
        approval=None,
        created_at="2026-09-24T12:00:00+00:00",
        updated_at="2026-09-24T12:00:00+00:00",
    )


def test_runtime_models_round_trip_and_reject_wrong_schema() -> None:
    """Catch loss of pinned IDs/bindings or acceptance of a different schema."""
    project = _project()
    assert ProjectRuntimeV1.from_dict(project.to_dict()) == project

    stage = _stage()
    assert StageRunV1.from_dict(stage.to_dict()) == stage

    task = TaskAttemptV1(
        task_id="task_1",
        stage_run_id=stage.stage_run_id,
        attempt=1,
        operation=TaskOperation.EXECUTE,
        status=TaskStatus.QUEUED,
        executor_id="test.analyze",
        input_bindings={
            "primary_video": RuntimeRef(RuntimeRefKind.ASSET, "asset_1")
        },
        output_bindings={},
        created_at="2026-09-24T12:00:00+00:00",
        queued_at="2026-09-24T12:00:00+00:00",
    )
    assert TaskAttemptV1.from_dict(task.to_dict()) == task

    malformed = project.to_dict()
    malformed["schema_version"] = "project_runtime.v2"
    with pytest.raises(ValueError, match="project_runtime.v1"):
        ProjectRuntimeV1.from_dict(malformed)


def test_attempt_allocation_retry_and_restart_recovery_preserve_history(tmp_path) -> None:
    """Catch reused attempt numbers, overwritten failures, and stranded running work."""
    repository = RuntimeRepository(tmp_path)
    repository.create_project(_project(), (_stage(),))
    tasks = LocalTaskManager(repository)

    first = tasks.queue(
        "stage_run_1",
        operation=TaskOperation.EXECUTE,
        executor_id="test.analyze",
        input_bindings={
            "primary_video": RuntimeRef(RuntimeRefKind.ASSET, "asset_1")
        },
    )
    tasks.start(first.task_id)
    failed = tasks.fail(first.task_id, code="analysis_failed", message="boom")
    retry = tasks.retry(failed.task_id)
    running_retry = tasks.start(retry.task_id)

    assert (failed.attempt, retry.attempt) == (1, 2)
    assert failed.status is TaskStatus.FAILED
    assert running_retry.status is TaskStatus.RUNNING

    recovered = LocalTaskManager(RuntimeRepository(tmp_path)).recover_project("proj_1")
    assert [task.status for task in recovered] == [TaskStatus.INTERRUPTED]

    history = RuntimeRepository(tmp_path).list_task_attempts("stage_run_1")
    assert [task.status for task in history] == [
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
    ]

    with pytest.raises(ValueError, match="monotonic"):
        RuntimeRepository(tmp_path).save_stage_run(
            replace(RuntimeRepository(tmp_path).get_stage_run("stage_run_1"), next_attempt=1)
        )
