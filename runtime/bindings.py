"""Exact Stage input resolution against Project-selected Runtime bindings."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from artifacts import ArtifactInputRef, ArtifactRepository, InputRefKind
from workflow import Cardinality, WorkflowDefinition

from .models import (
    ArtifactBinding,
    InputBinding,
    ProjectRuntimeV1,
    RuntimeRef,
    RuntimeRefKind,
)
from .services import artifact_ids


class BindingResolutionError(RuntimeError):
    """Raised instead of guessing when exact Stage bindings cannot be resolved."""

    def __init__(self, message: str, *, slot: str | None = None) -> None:
        super().__init__(message)
        self.slot = slot


class BindingResolver:
    def __init__(self, artifacts: ArtifactRepository) -> None:
        self.artifacts = artifacts

    def resolve_stage_inputs(
        self,
        project: ProjectRuntimeV1,
        workflow: WorkflowDefinition,
        stage_id: str,
    ) -> Mapping[str, InputBinding]:
        stage = self._stage(workflow, stage_id)
        resolved: dict[str, InputBinding] = {}

        # Workflow v1 has no Asset slots. Supplying all pinned source roles is
        # deliberately conservative and prevents false freshness.
        for role, binding in project.source_bindings.items():
            resolved[role] = self._runtime_refs(RuntimeRefKind.ASSET, binding)

        for slot_name, slot in stage.inputs.items():
            if slot_name in resolved:
                raise BindingResolutionError(
                    f"artifact input {slot_name} conflicts with a source role",
                    slot=slot_name,
                )
            candidates: list[ArtifactBinding] = []
            if slot_name in project.artifact_bindings.project_inputs:
                candidates.append(project.artifact_bindings.project_inputs[slot_name])
            for dependency_id in stage.depends_on:
                outputs = project.artifact_bindings.stage_outputs.get(dependency_id, {})
                if slot_name in outputs:
                    candidates.append(outputs[slot_name])

            if len(candidates) > 1:
                raise BindingResolutionError(
                    f"ambiguous binding for input slot {slot_name}", slot=slot_name
                )
            if not candidates:
                if slot.cardinality is Cardinality.ONE:
                    raise BindingResolutionError(
                        f"missing binding for input slot {slot_name}", slot=slot_name
                    )
                candidate: ArtifactBinding = (
                    None if slot.cardinality is Cardinality.OPTIONAL else ()
                )
            else:
                candidate = candidates[0]

            validation_value = list(candidate) if isinstance(candidate, tuple) else candidate
            try:
                normalized = slot.validate_binding(validation_value)
            except ValueError as error:
                raise BindingResolutionError(str(error), slot=slot_name) from error
            self._validate_artifacts(normalized, slot.artifact_type, slot_name)
            resolved[slot_name] = self._runtime_refs(
                RuntimeRefKind.ARTIFACT, normalized
            )

        return MappingProxyType(resolved)

    def expected_input_refs(
        self,
        project: ProjectRuntimeV1,
        workflow: WorkflowDefinition,
        stage_id: str,
    ) -> tuple[ArtifactInputRef, ...]:
        bindings = self.resolve_stage_inputs(project, workflow, stage_id)
        refs: list[ArtifactInputRef] = []
        for slot, binding in bindings.items():
            if binding is None:
                continue
            values = binding if isinstance(binding, tuple) else (binding,)
            for value in values:
                refs.append(
                    ArtifactInputRef(
                        slot=slot,
                        kind=InputRefKind(value.kind.value),
                        id=value.id,
                    )
                )
        return tuple(refs)

    def _validate_artifacts(
        self, binding: ArtifactBinding, expected_type: str, slot_name: str
    ) -> None:
        for artifact_id in artifact_ids(binding):
            revision = self.artifacts.get_revision(artifact_id)
            if revision.deleted_at is not None or revision.purged_at is not None:
                raise BindingResolutionError(
                    f"input slot {slot_name} references a non-live Artifact",
                    slot=slot_name,
                )
            if revision.artifact_type != expected_type:
                raise BindingResolutionError(
                    f"input slot {slot_name} expects {expected_type}, got "
                    f"{revision.artifact_type}",
                    slot=slot_name,
                )

    @staticmethod
    def _runtime_refs(kind: RuntimeRefKind, binding: object) -> InputBinding:
        if binding is None:
            return None
        if isinstance(binding, str):
            return RuntimeRef(kind, binding)
        if isinstance(binding, tuple):
            return tuple(RuntimeRef(kind, item) for item in binding)
        raise BindingResolutionError("binding has an invalid normalized shape")

    @staticmethod
    def _stage(workflow: WorkflowDefinition, stage_id: str):
        for stage in workflow.stages:
            if stage.stage_id == stage_id:
                return stage
        raise BindingResolutionError(f"unknown Stage: {stage_id}")

