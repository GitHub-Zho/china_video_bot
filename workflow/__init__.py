"""Immutable workflow definitions and logical executor registration."""

from .models import (
    ArtifactSlotDefinition,
    Cardinality,
    StageDefinition,
    WorkflowDefinition,
)
from .adapters import register_existing_executors
from .executors import ExecutorNotFoundError, ExecutorRegistry

__all__ = [
    "ArtifactSlotDefinition",
    "Cardinality",
    "ExecutorNotFoundError",
    "ExecutorRegistry",
    "StageDefinition",
    "WorkflowDefinition",
    "register_existing_executors",
]
