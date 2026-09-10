"""Immutable declarations for workflow DAGs and their artifact slots."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class Cardinality(str, Enum):
    ONE = "one"
    OPTIONAL = "optional"
    MANY = "many"


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True)
class ArtifactSlotDefinition:
    artifact_type: str
    cardinality: Cardinality

    def __post_init__(self) -> None:
        _require_nonempty(self.artifact_type, "artifact_type")
        if not isinstance(self.cardinality, Cardinality):
            raise ValueError("cardinality must be a Cardinality")

    def validate_binding(self, value: object) -> str | None | tuple[str, ...]:
        """Return a normalized artifact-ID binding or reject an invalid shape."""
        if self.cardinality is Cardinality.ONE:
            return self._validate_artifact_id(value)
        if self.cardinality is Cardinality.OPTIONAL:
            if value is None:
                return None
            return self._validate_artifact_id(value)
        if not isinstance(value, list):
            raise ValueError("many binding must be a list of Artifact IDs")
        return tuple(self._validate_artifact_id(item) for item in value)

    @staticmethod
    def _validate_artifact_id(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Artifact ID must be a non-empty string")
        return value


@dataclass(frozen=True)
class StageDefinition:
    stage_id: str
    phase: str
    executor: str
    inputs: Mapping[str, ArtifactSlotDefinition] = field(default_factory=dict)
    outputs: Mapping[str, ArtifactSlotDefinition] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    approval_required: bool = False
    ui_component: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.stage_id, "stage_id")
        _require_nonempty(self.phase, "phase")
        _require_nonempty(self.executor, "executor")
        if self.ui_component is not None:
            _require_nonempty(self.ui_component, "ui_component")
        object.__setattr__(self, "inputs", self._freeze_slots(self.inputs, "inputs"))
        object.__setattr__(self, "outputs", self._freeze_slots(self.outputs, "outputs"))

        dependencies = tuple(self.depends_on)
        for dependency in dependencies:
            _require_nonempty(dependency, "dependency")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("duplicate dependencies are not allowed")
        object.__setattr__(self, "depends_on", dependencies)

    @staticmethod
    def _freeze_slots(
        slots: Mapping[str, ArtifactSlotDefinition], field_name: str
    ) -> Mapping[str, ArtifactSlotDefinition]:
        copied = dict(slots)
        for slot_name, slot in copied.items():
            _require_nonempty(slot_name, f"{field_name} slot name")
            if not isinstance(slot, ArtifactSlotDefinition):
                raise ValueError(f"{field_name} slots must be ArtifactSlotDefinition values")
        return MappingProxyType(copied)


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    workflow_version: str
    mode: str
    stages: tuple[StageDefinition, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.workflow_id, "workflow_id")
        _require_nonempty(self.workflow_version, "workflow_version")
        _require_nonempty(self.mode, "mode")

        stages = tuple(self.stages)
        for stage in stages:
            if not isinstance(stage, StageDefinition):
                raise ValueError("stages must contain StageDefinition values")
        object.__setattr__(self, "stages", stages)

        stage_ids = tuple(stage.stage_id for stage in stages)
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("duplicate stage_id is not allowed")
        known_ids = set(stage_ids)
        for stage in stages:
            for dependency in stage.depends_on:
                if dependency not in known_ids:
                    raise ValueError(f"unknown dependency: {dependency}")
        self._topological_order()

    def topological_stage_ids(self) -> tuple[str, ...]:
        """Return a dependency-valid order, preserving declaration order when tied."""
        return self._topological_order()

    def _topological_order(self) -> tuple[str, ...]:
        indegree = {stage.stage_id: len(stage.depends_on) for stage in self.stages}
        dependents = {stage.stage_id: [] for stage in self.stages}
        for stage in self.stages:
            for dependency in stage.depends_on:
                dependents[dependency].append(stage.stage_id)

        ready = [stage.stage_id for stage in self.stages if indegree[stage.stage_id] == 0]
        order: list[str] = []
        for stage_id in ready:
            order.append(stage_id)
            for dependent_id in dependents[stage_id]:
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(dependent_id)

        if len(order) != len(self.stages):
            raise ValueError("workflow dependency cycle detected")
        return tuple(order)
