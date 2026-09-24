"""Local TaskAttempt lifecycle and restart recovery."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .models import ArtifactBinding, TaskAttemptV1, TaskError, TaskOperation, TaskStatus
from .repository import RuntimeConflictError, RuntimeRepository, now_utc


class LocalTaskManager:
    """Manage durable local attempts without distributed queue semantics."""

    def __init__(self, repository: RuntimeRepository) -> None:
        self.repository = repository

    def queue(
        self,
        stage_run_id: str,
        *,
        operation: TaskOperation,
        executor_id: str | None,
        input_bindings,
    ) -> TaskAttemptV1:
        return self.repository.allocate_task(
            stage_run_id,
            operation=operation,
            executor_id=executor_id,
            input_bindings=input_bindings,
        )

    def start(self, task_id: str) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(task, {TaskStatus.QUEUED}, "start")
        if task.cancel_requested_at is not None:
            return self.cancel(task_id)
        return self.repository.save_task_attempt(
            replace(task, status=TaskStatus.RUNNING, started_at=now_utc())
        )

    def succeed(
        self, task_id: str, output_bindings: Mapping[str, ArtifactBinding]
    ) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(task, {TaskStatus.RUNNING}, "succeed")
        if task.cancel_requested_at is not None:
            return self.cancel(task_id)
        return self.repository.save_task_attempt(
            replace(
                task,
                status=TaskStatus.SUCCEEDED,
                output_bindings=output_bindings,
                finished_at=now_utc(),
                error=None,
            )
        )

    def fail(
        self,
        task_id: str,
        *,
        code: str,
        message: str,
        retryable: bool = True,
    ) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(task, {TaskStatus.QUEUED, TaskStatus.RUNNING}, "fail")
        return self.repository.save_task_attempt(
            replace(
                task,
                status=TaskStatus.FAILED,
                finished_at=now_utc(),
                error=TaskError(code, message, retryable),
            )
        )

    def request_cancel(self, task_id: str) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(
            task, {TaskStatus.QUEUED, TaskStatus.RUNNING}, "request cancellation"
        )
        if task.cancel_requested_at is not None:
            return task
        return self.repository.save_task_attempt(
            replace(task, cancel_requested_at=now_utc())
        )

    def cancel(self, task_id: str) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(task, {TaskStatus.QUEUED, TaskStatus.RUNNING}, "cancel")
        timestamp = now_utc()
        return self.repository.save_task_attempt(
            replace(
                task,
                status=TaskStatus.CANCELLED,
                cancel_requested_at=task.cancel_requested_at or timestamp,
                finished_at=timestamp,
            )
        )

    def interrupt(self, task_id: str) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(task, {TaskStatus.RUNNING}, "interrupt")
        return self.repository.save_task_attempt(
            replace(task, status=TaskStatus.INTERRUPTED, finished_at=now_utc())
        )

    def retry(self, task_id: str) -> TaskAttemptV1:
        task = self.repository.get_task_attempt(task_id)
        self._require_status(
            task,
            {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED},
            "retry",
        )
        return self.queue(
            task.stage_run_id,
            operation=task.operation,
            executor_id=task.executor_id,
            input_bindings=task.input_bindings,
        )

    def recover_project(self, project_id: str) -> tuple[TaskAttemptV1, ...]:
        recovered: list[TaskAttemptV1] = []
        for stage in self.repository.list_stage_runs(project_id):
            for task in self.repository.list_task_attempts(stage.stage_run_id):
                if task.status is TaskStatus.RUNNING:
                    recovered.append(self.interrupt(task.task_id))
        return tuple(recovered)

    @staticmethod
    def _require_status(
        task: TaskAttemptV1, allowed: set[TaskStatus], action: str
    ) -> None:
        if task.status not in allowed:
            expected = ", ".join(sorted(status.value for status in allowed))
            raise RuntimeConflictError(
                f"cannot {action} TaskAttempt in {task.status.value}; expected {expected}"
            )

