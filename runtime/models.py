"""Immutable value models for the locked Project / Stage Runtime v1 contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TypeAlias


class ProjectLifecycleStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RuntimeRefKind(str, Enum):
    ASSET = "asset"
    ARTIFACT = "artifact"


class TaskOperation(str, Enum):
    EXECUTE = "execute"
    REUSE = "reuse"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StageActivity(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"


class StageFreshness(str, Enum):
    NO_OUTPUT = "no_output"
    FRESH = "fresh"
    STALE = "stale"


class StageReview(str, Enum):
    NOT_REQUIRED = "not_required"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


def _require_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_id(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_id(value, field_name)


def _require_schema(value: Mapping[str, object], expected: str) -> None:
    if not isinstance(value, Mapping) or value.get("schema_version") != expected:
        raise ValueError(f"schema_version must be {expected}")


ArtifactBinding: TypeAlias = str | tuple[str, ...] | None
AssetBinding: TypeAlias = str | tuple[str, ...]


def normalize_artifact_binding(value: object, field_name: str = "binding") -> ArtifactBinding:
    if value is None:
        return None
    if isinstance(value, str):
        return _require_id(value, field_name)
    if isinstance(value, (list, tuple)):
        return tuple(_require_id(item, field_name) for item in value)
    raise ValueError(f"{field_name} must be an ID, a list of IDs, or None")


def normalize_asset_binding(value: object, field_name: str = "source binding") -> AssetBinding:
    normalized = normalize_artifact_binding(value, field_name)
    if normalized is None:
        raise ValueError(f"{field_name} must not be None")
    return normalized


def _binding_to_dict(value: ArtifactBinding) -> object:
    return list(value) if isinstance(value, tuple) else value


def _freeze_bindings(
    values: Mapping[str, object], field_name: str
) -> Mapping[str, ArtifactBinding]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return MappingProxyType(
        {
            _require_id(key, f"{field_name} key"): normalize_artifact_binding(
                value, f"{field_name}[{key}]"
            )
            for key, value in values.items()
        }
    )


@dataclass(frozen=True)
class WorkflowPin:
    workflow_id: str
    workflow_version: str

    def __post_init__(self) -> None:
        _require_id(self.workflow_id, "workflow_id")
        _require_id(self.workflow_version, "workflow_version")

    def to_dict(self) -> dict[str, str]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "WorkflowPin":
        if not isinstance(value, Mapping):
            raise ValueError("workflow must be a mapping")
        return cls(value.get("workflow_id"), value.get("workflow_version"))


@dataclass(frozen=True)
class ProjectArtifactBindings:
    project_inputs: Mapping[str, ArtifactBinding] = field(default_factory=dict)
    stage_outputs: Mapping[str, Mapping[str, ArtifactBinding]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_inputs",
            _freeze_bindings(self.project_inputs, "project_inputs"),
        )
        if not isinstance(self.stage_outputs, Mapping):
            raise ValueError("stage_outputs must be a mapping")
        frozen_outputs = {
            _require_id(stage_id, "stage_outputs key"): _freeze_bindings(
                outputs, f"stage_outputs[{stage_id}]"
            )
            for stage_id, outputs in self.stage_outputs.items()
        }
        object.__setattr__(self, "stage_outputs", MappingProxyType(frozen_outputs))

    def to_dict(self) -> dict[str, object]:
        return {
            "project_inputs": {
                key: _binding_to_dict(value) for key, value in self.project_inputs.items()
            },
            "stage_outputs": {
                stage_id: {
                    slot: _binding_to_dict(binding)
                    for slot, binding in outputs.items()
                }
                for stage_id, outputs in self.stage_outputs.items()
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProjectArtifactBindings":
        if not isinstance(value, Mapping):
            raise ValueError("artifact_bindings must be a mapping")
        project_inputs = value.get("project_inputs")
        stage_outputs = value.get("stage_outputs")
        if not isinstance(project_inputs, Mapping):
            raise ValueError("project_inputs must be a mapping")
        if not isinstance(stage_outputs, Mapping) or any(
            not isinstance(outputs, Mapping) for outputs in stage_outputs.values()
        ):
            raise ValueError("stage_outputs must contain mappings")
        return cls(project_inputs=project_inputs, stage_outputs=stage_outputs)


@dataclass(frozen=True)
class ProjectLineage:
    parent_project_id: str
    relation: str

    def __post_init__(self) -> None:
        _require_id(self.parent_project_id, "parent_project_id")
        if self.relation not in {"clone", "fork"}:
            raise ValueError("relation must be clone or fork")

    def to_dict(self) -> dict[str, str]:
        return {
            "parent_project_id": self.parent_project_id,
            "relation": self.relation,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProjectLineage":
        return cls(value.get("parent_project_id"), value.get("relation"))


@dataclass(frozen=True)
class ProjectUIState:
    selected_stage_id: str | None = None

    def __post_init__(self) -> None:
        _optional_id(self.selected_stage_id, "selected_stage_id")

    def to_dict(self) -> dict[str, object]:
        return {"selected_stage_id": self.selected_stage_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProjectUIState":
        return cls(value.get("selected_stage_id"))


@dataclass(frozen=True)
class ProjectRuntimeV1:
    project_id: str
    display_name: str
    workflow: WorkflowPin
    source_bindings: Mapping[str, AssetBinding]
    artifact_bindings: ProjectArtifactBindings
    stage_runs: Mapping[str, str]
    created_at: str
    updated_at: str
    lifecycle_status: ProjectLifecycleStatus = ProjectLifecycleStatus.ACTIVE
    deleted_at: str | None = None
    lineage: ProjectLineage | None = None
    ui_state: ProjectUIState | None = None

    def __post_init__(self) -> None:
        _require_id(self.project_id, "project_id")
        _require_id(self.display_name, "display_name")
        if not isinstance(self.workflow, WorkflowPin):
            raise ValueError("workflow must be a WorkflowPin")
        if not isinstance(self.lifecycle_status, ProjectLifecycleStatus):
            raise ValueError("lifecycle_status must be a ProjectLifecycleStatus")
        if not isinstance(self.source_bindings, Mapping):
            raise ValueError("source_bindings must be a mapping")
        object.__setattr__(
            self,
            "source_bindings",
            MappingProxyType(
                {
                    _require_id(key, "source binding key"): normalize_asset_binding(
                        value, f"source_bindings[{key}]"
                    )
                    for key, value in self.source_bindings.items()
                }
            ),
        )
        if not isinstance(self.artifact_bindings, ProjectArtifactBindings):
            raise ValueError("artifact_bindings must be ProjectArtifactBindings")
        if not isinstance(self.stage_runs, Mapping):
            raise ValueError("stage_runs must be a mapping")
        object.__setattr__(
            self,
            "stage_runs",
            MappingProxyType(
                {
                    _require_id(stage_id, "stage_id"): _require_id(
                        stage_run_id, "stage_run_id"
                    )
                    for stage_id, stage_run_id in self.stage_runs.items()
                }
            ),
        )
        _require_id(self.created_at, "created_at")
        _require_id(self.updated_at, "updated_at")
        _optional_id(self.deleted_at, "deleted_at")
        if self.lineage is not None and not isinstance(self.lineage, ProjectLineage):
            raise ValueError("lineage must be ProjectLineage or None")
        if self.ui_state is not None and not isinstance(self.ui_state, ProjectUIState):
            raise ValueError("ui_state must be ProjectUIState or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "project_runtime.v1",
            "project_id": self.project_id,
            "display_name": self.display_name,
            "workflow": self.workflow.to_dict(),
            "lifecycle_status": self.lifecycle_status.value,
            "deleted_at": self.deleted_at,
            "source_bindings": {
                key: _binding_to_dict(value)
                for key, value in self.source_bindings.items()
            },
            "artifact_bindings": self.artifact_bindings.to_dict(),
            "stage_runs": dict(self.stage_runs),
            "lineage": None if self.lineage is None else self.lineage.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ui_state": None if self.ui_state is None else self.ui_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProjectRuntimeV1":
        _require_schema(value, "project_runtime.v1")
        workflow = value.get("workflow")
        sources = value.get("source_bindings")
        artifacts = value.get("artifact_bindings")
        stage_runs = value.get("stage_runs")
        if not isinstance(workflow, Mapping):
            raise ValueError("workflow must be a mapping")
        if not isinstance(sources, Mapping):
            raise ValueError("source_bindings must be a mapping")
        if not isinstance(artifacts, Mapping):
            raise ValueError("artifact_bindings must be a mapping")
        if not isinstance(stage_runs, Mapping):
            raise ValueError("stage_runs must be a mapping")
        lineage_value = value.get("lineage")
        ui_value = value.get("ui_state")
        if lineage_value is not None and not isinstance(lineage_value, Mapping):
            raise ValueError("lineage must be a mapping or None")
        if ui_value is not None and not isinstance(ui_value, Mapping):
            raise ValueError("ui_state must be a mapping or None")
        return cls(
            project_id=value.get("project_id"),
            display_name=value.get("display_name"),
            workflow=WorkflowPin.from_dict(workflow),
            lifecycle_status=ProjectLifecycleStatus(value.get("lifecycle_status")),
            deleted_at=value.get("deleted_at"),
            source_bindings=sources,
            artifact_bindings=ProjectArtifactBindings.from_dict(artifacts),
            stage_runs=stage_runs,
            lineage=None
            if lineage_value is None
            else ProjectLineage.from_dict(lineage_value),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            ui_state=None if ui_value is None else ProjectUIState.from_dict(ui_value),
        )


@dataclass(frozen=True)
class ApprovalSnapshot:
    approved_outputs: Mapping[str, ArtifactBinding]
    approved_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approved_outputs",
            _freeze_bindings(self.approved_outputs, "approved_outputs"),
        )
        _require_id(self.approved_at, "approved_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "approved_outputs": {
                key: _binding_to_dict(value)
                for key, value in self.approved_outputs.items()
            },
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ApprovalSnapshot":
        outputs = value.get("approved_outputs")
        if not isinstance(outputs, Mapping):
            raise ValueError("approved_outputs must be a mapping")
        return cls(outputs, value.get("approved_at"))


@dataclass(frozen=True)
class StageRunV1:
    stage_run_id: str
    project_id: str
    stage_id: str
    next_attempt: int
    task_ids: tuple[str, ...]
    approval: ApprovalSnapshot | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _require_id(self.stage_run_id, "stage_run_id")
        _require_id(self.project_id, "project_id")
        _require_id(self.stage_id, "stage_id")
        if (
            isinstance(self.next_attempt, bool)
            or not isinstance(self.next_attempt, int)
            or self.next_attempt < 1
        ):
            raise ValueError("next_attempt must be a positive integer")
        task_ids = tuple(self.task_ids)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task_ids must be unique")
        for task_id in task_ids:
            _require_id(task_id, "task_id")
        object.__setattr__(self, "task_ids", task_ids)
        if self.approval is not None and not isinstance(self.approval, ApprovalSnapshot):
            raise ValueError("approval must be ApprovalSnapshot or None")
        _require_id(self.created_at, "created_at")
        _require_id(self.updated_at, "updated_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "stage_run.v1",
            "stage_run_id": self.stage_run_id,
            "project_id": self.project_id,
            "stage_id": self.stage_id,
            "next_attempt": self.next_attempt,
            "task_ids": list(self.task_ids),
            "approval": None if self.approval is None else self.approval.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "StageRunV1":
        _require_schema(value, "stage_run.v1")
        task_ids = value.get("task_ids")
        approval = value.get("approval")
        if not isinstance(task_ids, list):
            raise ValueError("task_ids must be a list")
        if approval is not None and not isinstance(approval, Mapping):
            raise ValueError("approval must be a mapping or None")
        return cls(
            stage_run_id=value.get("stage_run_id"),
            project_id=value.get("project_id"),
            stage_id=value.get("stage_id"),
            next_attempt=value.get("next_attempt"),
            task_ids=tuple(task_ids),
            approval=None if approval is None else ApprovalSnapshot.from_dict(approval),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
        )


@dataclass(frozen=True)
class RuntimeRef:
    kind: RuntimeRefKind
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RuntimeRefKind):
            raise ValueError("kind must be a RuntimeRefKind")
        _require_id(self.id, "runtime reference id")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "id": self.id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuntimeRef":
        return cls(RuntimeRefKind(value.get("kind")), value.get("id"))


InputBinding: TypeAlias = RuntimeRef | tuple[RuntimeRef, ...] | None


def normalize_input_binding(value: object, field_name: str = "input binding") -> InputBinding:
    if value is None:
        return None
    if isinstance(value, RuntimeRef):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, RuntimeRef) for item in value
    ):
        return tuple(value)
    raise ValueError(f"{field_name} must be RuntimeRef, a list of RuntimeRef, or None")


def _freeze_input_bindings(values: Mapping[str, object]) -> Mapping[str, InputBinding]:
    if not isinstance(values, Mapping):
        raise ValueError("input_bindings must be a mapping")
    return MappingProxyType(
        {
            _require_id(key, "input binding key"): normalize_input_binding(
                value, f"input_bindings[{key}]"
            )
            for key, value in values.items()
        }
    )


@dataclass(frozen=True)
class TaskError:
    code: str
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        _require_id(self.code, "error code")
        _require_id(self.message, "error message")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskError":
        return cls(value.get("code"), value.get("message"), value.get("retryable"))


@dataclass(frozen=True)
class TaskAttemptV1:
    task_id: str
    stage_run_id: str
    attempt: int
    operation: TaskOperation
    status: TaskStatus
    executor_id: str | None
    input_bindings: Mapping[str, InputBinding]
    output_bindings: Mapping[str, ArtifactBinding]
    created_at: str
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested_at: str | None = None
    error: TaskError | None = None

    def __post_init__(self) -> None:
        _require_id(self.task_id, "task_id")
        _require_id(self.stage_run_id, "stage_run_id")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if not isinstance(self.operation, TaskOperation):
            raise ValueError("operation must be a TaskOperation")
        if not isinstance(self.status, TaskStatus):
            raise ValueError("status must be a TaskStatus")
        _optional_id(self.executor_id, "executor_id")
        object.__setattr__(self, "input_bindings", _freeze_input_bindings(self.input_bindings))
        object.__setattr__(
            self,
            "output_bindings",
            _freeze_bindings(self.output_bindings, "output_bindings"),
        )
        _require_id(self.created_at, "created_at")
        for field_name in (
            "queued_at",
            "started_at",
            "finished_at",
            "cancel_requested_at",
        ):
            _optional_id(getattr(self, field_name), field_name)
        if self.error is not None and not isinstance(self.error, TaskError):
            raise ValueError("error must be TaskError or None")

    def to_dict(self) -> dict[str, object]:
        def input_value(value: InputBinding) -> object:
            if isinstance(value, tuple):
                return [item.to_dict() for item in value]
            return None if value is None else value.to_dict()

        return {
            "schema_version": "task_attempt.v1",
            "task_id": self.task_id,
            "stage_run_id": self.stage_run_id,
            "attempt": self.attempt,
            "operation": self.operation.value,
            "status": self.status.value,
            "executor_id": self.executor_id,
            "input_bindings": {
                key: input_value(value) for key, value in self.input_bindings.items()
            },
            "output_bindings": {
                key: _binding_to_dict(value)
                for key, value in self.output_bindings.items()
            },
            "created_at": self.created_at,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested_at": self.cancel_requested_at,
            "error": None if self.error is None else self.error.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskAttemptV1":
        _require_schema(value, "task_attempt.v1")
        inputs = value.get("input_bindings")
        outputs = value.get("output_bindings")
        error_value = value.get("error")
        if not isinstance(inputs, Mapping):
            raise ValueError("input_bindings must be a mapping")
        if not isinstance(outputs, Mapping):
            raise ValueError("output_bindings must be a mapping")
        if error_value is not None and not isinstance(error_value, Mapping):
            raise ValueError("error must be a mapping or None")

        parsed_inputs: dict[str, InputBinding] = {}
        for key, binding in inputs.items():
            if binding is None:
                parsed_inputs[key] = None
            elif isinstance(binding, Mapping):
                parsed_inputs[key] = RuntimeRef.from_dict(binding)
            elif isinstance(binding, list) and all(
                isinstance(item, Mapping) for item in binding
            ):
                parsed_inputs[key] = tuple(RuntimeRef.from_dict(item) for item in binding)
            else:
                raise ValueError("input binding has an invalid serialized shape")
        return cls(
            task_id=value.get("task_id"),
            stage_run_id=value.get("stage_run_id"),
            attempt=value.get("attempt"),
            operation=TaskOperation(value.get("operation")),
            status=TaskStatus(value.get("status")),
            executor_id=value.get("executor_id"),
            input_bindings=parsed_inputs,
            output_bindings=outputs,
            created_at=value.get("created_at"),
            queued_at=value.get("queued_at"),
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            cancel_requested_at=value.get("cancel_requested_at"),
            error=None if error_value is None else TaskError.from_dict(error_value),
        )


@dataclass(frozen=True)
class StageView:
    activity: StageActivity
    freshness: StageFreshness
    review: StageReview
    runnable: bool
    blocked_by: tuple[str, ...]
    latest_attempt_status: str | None

