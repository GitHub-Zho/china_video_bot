"""Pinned Workflow definition and Project creation boundary for Understanding v1."""

from __future__ import annotations

from runtime import ProjectService, WorkflowCatalog
from workflow import (
    ArtifactSlotDefinition,
    Cardinality,
    StageDefinition,
    WorkflowDefinition,
)


WORKFLOW_ID = "video_grounded.understand"
WORKFLOW_VERSION = "1.0"
PRIMARY_VIDEO_ROLE = "primary_video"
TRANSCRIPTION_EXECUTOR_ID = "video_grounded.transcribe_source"
ANALYSIS_EXECUTOR_ID = "video_grounded.analyze_source"
PLAN_EXECUTOR_ID = "video_grounded.plan_source"


def build_understanding_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        mode="video_grounded",
        stages=(
            StageDefinition(
                stage_id="transcribe_source",
                phase="analyze",
                executor=TRANSCRIPTION_EXECUTOR_ID,
                inputs={},
                outputs={
                    "transcript": ArtifactSlotDefinition(
                        artifact_type="transcript",
                        cardinality=Cardinality.ONE,
                    )
                },
                depends_on=(),
                approval_required=False,
                ui_component="transcript_viewer",
            ),
            StageDefinition(
                stage_id="analyze_source",
                phase="analyze",
                executor=ANALYSIS_EXECUTOR_ID,
                inputs={
                    "transcript": ArtifactSlotDefinition(
                        artifact_type="transcript",
                        cardinality=Cardinality.ONE,
                    )
                },
                outputs={
                    "understanding": ArtifactSlotDefinition(
                        artifact_type="video_understanding",
                        cardinality=Cardinality.ONE,
                    )
                },
                depends_on=("transcribe_source",),
                approval_required=True,
                ui_component="understanding_editor",
            ),
            StageDefinition(
                stage_id="plan_source",
                phase="plan",
                executor=PLAN_EXECUTOR_ID,
                inputs={
                    "understanding": ArtifactSlotDefinition(
                        artifact_type="video_understanding",
                        cardinality=Cardinality.ONE,
                    )
                },
                outputs={
                    "plan": ArtifactSlotDefinition(
                        artifact_type="edit_plan",
                        cardinality=Cardinality.ONE,
                    )
                },
                depends_on=("analyze_source",),
                approval_required=True,
                ui_component="edit_plan_editor",
            ),
        ),
    )


def register_understanding_workflow(catalog: WorkflowCatalog) -> WorkflowDefinition:
    workflow = build_understanding_workflow()
    catalog.register(workflow)
    return workflow


def create_understanding_project(
    projects: ProjectService,
    display_name: str,
    source_asset_id: str,
    *,
    project_id: str | None = None,
):
    if not isinstance(source_asset_id, str) or not source_asset_id.strip():
        raise ValueError("source_asset_id must be a non-empty string")
    return projects.create_project(
        display_name,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        source_bindings={PRIMARY_VIDEO_ROLE: source_asset_id},
        project_id=project_id,
    )

