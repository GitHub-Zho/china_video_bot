"""Private filesystem persistence for Project / Stage Runtime v1 records."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping

from .models import (
    InputBinding,
    ProjectRuntimeV1,
    StageRunV1,
    TaskAttemptV1,
    TaskError,
    TaskOperation,
    TaskStatus,
)


class RuntimeNotFoundError(LookupError):
    """Raised when a requested Runtime record does not exist."""


class RuntimeConflictError(RuntimeError):
    """Raised when a write would violate Runtime identity or history."""


_SAFE_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.INTERRUPTED,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeRepository:
    """Store Runtime records without exposing their filesystem layout to callers."""

    def __init__(self, storage_root: Path | str) -> None:
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._records = self.storage_root / ".runtime-repository"
        self._projects = self._records / "projects"
        self._stages = self._records / "stages"
        self._tasks = self._records / "tasks"
        self._allocations = self._records / "allocations"
        self._locks = self._records / "locks"
        for directory in (
            self._projects,
            self._stages,
            self._tasks,
            self._allocations,
            self._locks,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._global_lock = self._locks / "repository.lock"
        self._recover_allocations()

    def create_project(
        self, project: ProjectRuntimeV1, stage_runs: tuple[StageRunV1, ...]
    ) -> ProjectRuntimeV1:
        if not isinstance(project, ProjectRuntimeV1):
            raise ValueError("project must be ProjectRuntimeV1")
        stages = tuple(stage_runs)
        expected = dict(project.stage_runs)
        actual = {stage.stage_id: stage.stage_run_id for stage in stages}
        if actual != expected or any(stage.project_id != project.project_id for stage in stages):
            raise RuntimeConflictError("StageRun records must exactly match Project stage_runs")
        with self._lock(self._global_lock):
            if self._project_path(project.project_id).exists():
                raise RuntimeConflictError(f"Project already exists: {project.project_id}")
            for stage in stages:
                if self._stage_path(stage.stage_run_id).exists():
                    raise RuntimeConflictError(
                        f"StageRun already exists: {stage.stage_run_id}"
                    )
            for stage in stages:
                self._write_json(self._stage_path(stage.stage_run_id), stage.to_dict())
            self._write_json(self._project_path(project.project_id), project.to_dict())
        return project

    def get_project(self, project_id: str) -> ProjectRuntimeV1:
        path = self._project_path(project_id)
        if not path.is_file():
            raise RuntimeNotFoundError(f"Project not found: {project_id}")
        return ProjectRuntimeV1.from_dict(self._read_json(path))

    def save_project(
        self, project: ProjectRuntimeV1, *, expected_updated_at: str
    ) -> ProjectRuntimeV1:
        with self._lock(self._project_lock_path(project.project_id)):
            existing = self.get_project(project.project_id)
            if existing.updated_at != expected_updated_at:
                raise RuntimeConflictError("Project changed since it was read")
            self._validate_project_update(existing, project)
            project = replace(project, updated_at=existing.updated_at)
            if project == existing:
                return existing
            project = replace(
                project, updated_at=self._next_update_token(existing.updated_at)
            )
            self._write_json(self._project_path(project.project_id), project.to_dict())
        return project

    def update_project(
        self,
        project_id: str,
        updater: Callable[[ProjectRuntimeV1], ProjectRuntimeV1],
    ) -> ProjectRuntimeV1:
        """Apply one read-modify-write while holding the Project lock."""
        if not callable(updater):
            raise ValueError("updater must be callable")
        with self._lock(self._project_lock_path(project_id)):
            existing = self.get_project(project_id)
            updated = updater(existing)
            if not isinstance(updated, ProjectRuntimeV1):
                raise ValueError("updater must return ProjectRuntimeV1")
            self._validate_project_update(existing, updated)
            updated = replace(updated, updated_at=existing.updated_at)
            if updated == existing:
                return existing
            updated = replace(
                updated, updated_at=self._next_update_token(existing.updated_at)
            )
            self._write_json(self._project_path(project_id), updated.to_dict())
            return updated

    def list_projects(self, *, include_deleted: bool = False) -> tuple[ProjectRuntimeV1, ...]:
        projects = [
            ProjectRuntimeV1.from_dict(self._read_json(path))
            for path in self._projects.glob("*.json")
        ]
        if not include_deleted:
            projects = [project for project in projects if project.deleted_at is None]
        return tuple(sorted(projects, key=lambda item: (item.created_at, item.project_id)))

    def get_stage_run(self, stage_run_id: str) -> StageRunV1:
        path = self._stage_path(stage_run_id)
        if not path.is_file():
            raise RuntimeNotFoundError(f"StageRun not found: {stage_run_id}")
        return StageRunV1.from_dict(self._read_json(path))

    def save_stage_run(self, stage_run: StageRunV1) -> StageRunV1:
        with self._lock(self._stage_lock_path(stage_run.stage_run_id)):
            existing = self.get_stage_run(stage_run.stage_run_id)
            if (
                stage_run.project_id != existing.project_id
                or stage_run.stage_id != existing.stage_id
                or stage_run.created_at != existing.created_at
            ):
                raise RuntimeConflictError("StageRun identity is immutable")
            if stage_run.next_attempt < existing.next_attempt:
                raise ValueError("next_attempt must remain monotonic")
            if stage_run.task_ids[: len(existing.task_ids)] != existing.task_ids:
                raise RuntimeConflictError("TaskAttempt history cannot be rewritten")
            self._write_json(self._stage_path(stage_run.stage_run_id), stage_run.to_dict())
        return stage_run

    def list_stage_runs(self, project_id: str) -> tuple[StageRunV1, ...]:
        project = self.get_project(project_id)
        return tuple(
            self.get_stage_run(project.stage_runs[stage_id])
            for stage_id in project.stage_runs
        )

    def allocate_task(
        self,
        stage_run_id: str,
        *,
        operation: TaskOperation,
        executor_id: str | None,
        input_bindings: Mapping[str, InputBinding],
    ) -> TaskAttemptV1:
        if not isinstance(operation, TaskOperation):
            raise ValueError("operation must be a TaskOperation")
        with self._lock(self._stage_lock_path(stage_run_id)):
            self._recover_allocation_locked(stage_run_id)
            stage = self.get_stage_run(stage_run_id)
            for task_id in stage.task_ids:
                status = self.get_task_attempt(task_id).status
                if status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                    raise RuntimeConflictError(
                        f"StageRun already has an active TaskAttempt: {task_id}"
                    )
            timestamp = now_utc()
            task = TaskAttemptV1(
                task_id=self.new_id("task"),
                stage_run_id=stage_run_id,
                attempt=stage.next_attempt,
                operation=operation,
                status=TaskStatus.QUEUED,
                executor_id=executor_id,
                input_bindings=input_bindings,
                output_bindings={},
                created_at=timestamp,
                queued_at=timestamp,
            )
            reserved_stage = replace(
                stage,
                next_attempt=stage.next_attempt + 1,
                updated_at=timestamp,
            )
            completed_stage = replace(
                reserved_stage, task_ids=stage.task_ids + (task.task_id,)
            )
            journal_path = self._allocation_path(stage_run_id)
            self._write_json(
                journal_path,
                {
                    "schema_version": "task_allocation.v1",
                    "task": task.to_dict(),
                },
            )
            # The journal makes every boundary recoverable. Reserve the number
            # before exposing the Task so a crash can only create a gap, never reuse.
            self._write_json(
                self._stage_path(reserved_stage.stage_run_id), reserved_stage.to_dict()
            )
            self._write_json(self._task_path(task.task_id), task.to_dict())
            self._write_json(
                self._stage_path(completed_stage.stage_run_id), completed_stage.to_dict()
            )
            self._delete_file(journal_path)
            return task

    def get_task_attempt(self, task_id: str) -> TaskAttemptV1:
        path = self._task_path(task_id)
        if not path.is_file():
            raise RuntimeNotFoundError(f"TaskAttempt not found: {task_id}")
        return TaskAttemptV1.from_dict(self._read_json(path))

    def save_task_attempt(self, task: TaskAttemptV1) -> TaskAttemptV1:
        with self._lock(self._task_lock_path(task.task_id)):
            existing = self.get_task_attempt(task.task_id)
            immutable = (
                "stage_run_id",
                "attempt",
                "operation",
                "executor_id",
                "input_bindings",
                "created_at",
                "queued_at",
            )
            if any(getattr(task, name) != getattr(existing, name) for name in immutable):
                raise RuntimeConflictError("TaskAttempt execution identity is immutable")
            if existing.status in _TERMINAL and task != existing:
                raise RuntimeConflictError("terminal TaskAttempt history cannot be rewritten")
            self._write_json(self._task_path(task.task_id), task.to_dict())
        return task

    def list_task_attempts(self, stage_run_id: str) -> tuple[TaskAttemptV1, ...]:
        stage = self.get_stage_run(stage_run_id)
        return tuple(self.get_task_attempt(task_id) for task_id in stage.task_ids)

    @staticmethod
    def new_id(prefix: str) -> str:
        if not _SAFE_RECORD_ID.fullmatch(prefix):
            raise ValueError("ID prefix is invalid")
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _validate_record_id(value: str) -> str:
        if not isinstance(value, str) or not _SAFE_RECORD_ID.fullmatch(value):
            raise ValueError("Runtime record ID contains unsupported characters")
        return value

    def _project_path(self, project_id: str) -> Path:
        return self._projects / f"{self._validate_record_id(project_id)}.json"

    def _stage_path(self, stage_run_id: str) -> Path:
        return self._stages / f"{self._validate_record_id(stage_run_id)}.json"

    def _task_path(self, task_id: str) -> Path:
        return self._tasks / f"{self._validate_record_id(task_id)}.json"

    def _project_lock_path(self, project_id: str) -> Path:
        return self._locks / f"project-{self._validate_record_id(project_id)}.lock"

    def _stage_lock_path(self, stage_run_id: str) -> Path:
        return self._locks / f"stage-{self._validate_record_id(stage_run_id)}.lock"

    def _task_lock_path(self, task_id: str) -> Path:
        return self._locks / f"task-{self._validate_record_id(task_id)}.lock"

    def _allocation_path(self, stage_run_id: str) -> Path:
        return self._allocations / f"{self._validate_record_id(stage_run_id)}.json"

    @staticmethod
    def _validate_project_update(
        existing: ProjectRuntimeV1, updated: ProjectRuntimeV1
    ) -> None:
        if updated.project_id != existing.project_id:
            raise RuntimeConflictError("Project identity is immutable")
        if (
            updated.workflow != existing.workflow
            or updated.created_at != existing.created_at
            or updated.stage_runs != existing.stage_runs
        ):
            raise RuntimeConflictError(
                "Project workflow, creation time, and StageRun identity are immutable"
            )

    @staticmethod
    def _next_update_token(previous: str) -> str:
        """Return a repository-owned token that strictly advances from *previous*."""
        current = datetime.now(timezone.utc)
        try:
            prior = datetime.fromisoformat(previous)
            if prior.tzinfo is None:
                prior = prior.replace(tzinfo=timezone.utc)
            prior = prior.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return current.isoformat()
        if current <= prior:
            current = prior + timedelta(microseconds=1)
        return current.isoformat()

    def _recover_allocations(self) -> None:
        for path in sorted(self._allocations.glob("*.json")):
            value = self._read_json(path)
            task_value = value.get("task")
            if value.get("schema_version") != "task_allocation.v1" or not isinstance(
                task_value, Mapping
            ):
                raise RuntimeConflictError(f"Invalid Task allocation journal: {path.name}")
            task = TaskAttemptV1.from_dict(task_value)
            with self._lock(self._stage_lock_path(task.stage_run_id)):
                self._recover_allocation_locked(task.stage_run_id)

    def _recover_allocation_locked(self, stage_run_id: str) -> None:
        path = self._allocation_path(stage_run_id)
        if not path.is_file():
            return
        value = self._read_json(path)
        task_value = value.get("task")
        if value.get("schema_version") != "task_allocation.v1" or not isinstance(
            task_value, Mapping
        ):
            raise RuntimeConflictError(f"Invalid Task allocation journal: {path.name}")
        task = TaskAttemptV1.from_dict(task_value)
        if task.stage_run_id != stage_run_id:
            raise RuntimeConflictError("Task allocation journal targets another StageRun")
        if task.status is TaskStatus.QUEUED:
            task = replace(
                task,
                status=TaskStatus.INTERRUPTED,
                finished_at=now_utc(),
                error=TaskError(
                    code="allocation_interrupted",
                    message="local process stopped during TaskAttempt allocation",
                    retryable=True,
                ),
            )
        stage = self.get_stage_run(stage_run_id)
        reserved = replace(
            stage,
            next_attempt=max(stage.next_attempt, task.attempt + 1),
            updated_at=max(stage.updated_at, task.created_at),
        )
        self._write_json(self._stage_path(stage_run_id), reserved.to_dict())
        self._write_json(self._task_path(task.task_id), task.to_dict())
        if task.task_id not in reserved.task_ids:
            reserved = replace(
                reserved,
                task_ids=reserved.task_ids + (task.task_id,),
                updated_at=max(reserved.updated_at, task.created_at),
            )
            self._write_json(self._stage_path(stage_run_id), reserved.to_dict())
        self._delete_file(path)

    @staticmethod
    def _delete_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        RuntimeRepository._fsync_directory(path.parent)

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeConflictError(f"Could not read Runtime record: {path.name}") from error
        if not isinstance(value, dict):
            raise RuntimeConflictError(f"Runtime record must be a mapping: {path.name}")
        return value

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            RuntimeRepository._fsync_directory(path.parent)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    @contextmanager
    def _lock(path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
