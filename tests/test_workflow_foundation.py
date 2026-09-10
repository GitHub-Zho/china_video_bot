from types import MappingProxyType

import pytest

from workflow.models import (
    ArtifactSlotDefinition,
    Cardinality,
    StageDefinition,
    WorkflowDefinition,
)
from workflow.adapters import register_existing_executors
from workflow.executors import ExecutorNotFoundError, ExecutorRegistry


def test_slot_cardinality_validates_runtime_binding_shapes():
    """Catch acceptance of malformed artifact binding values."""
    assert (
        ArtifactSlotDefinition("video_understanding", Cardinality.ONE).validate_binding(
            "art_1"
        )
        == "art_1"
    )
    assert (
        ArtifactSlotDefinition("video_understanding", Cardinality.OPTIONAL).validate_binding(
            None
        )
        is None
    )
    assert ArtifactSlotDefinition("frame", Cardinality.MANY).validate_binding(
        ["art_1", "art_2"]
    ) == ("art_1", "art_2")


@pytest.mark.parametrize(
    ("slot", "value"),
    [
        (ArtifactSlotDefinition("one", Cardinality.ONE), None),
        (ArtifactSlotDefinition("one", Cardinality.ONE), ["art_1"]),
        (ArtifactSlotDefinition("optional", Cardinality.OPTIONAL), ""),
        (ArtifactSlotDefinition("optional", Cardinality.OPTIONAL), ["art_1"]),
        (ArtifactSlotDefinition("many", Cardinality.MANY), None),
        (ArtifactSlotDefinition("many", Cardinality.MANY), "art_1"),
        (ArtifactSlotDefinition("many", Cardinality.MANY), ["art_1", ""]),
    ],
)
def test_slot_cardinality_rejects_invalid_runtime_binding_shapes(slot, value):
    """Catch a cardinality branch that accepts a value outside its contract."""
    with pytest.raises(ValueError):
        slot.validate_binding(value)


def test_workflow_rejects_unknown_dependency():
    """Catch a graph that refers to a stage which cannot be scheduled."""
    with pytest.raises(ValueError, match="unknown dependency"):
        WorkflowDefinition(
            workflow_id="rough_cut.default",
            workflow_version="1",
            mode="rough_cut",
            stages=(
                StageDefinition(
                    "plan", "plan", "rough_cut.plan", depends_on=("missing",)
                ),
            ),
        )


def test_workflow_rejects_dependency_cycle():
    """Catch a graph that could never reach a runnable stage."""
    stages = (
        StageDefinition("a", "analyze", "test.a", depends_on=("b",)),
        StageDefinition("b", "plan", "test.b", depends_on=("a",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        WorkflowDefinition("test.default", "1", "rough_cut", stages)


def test_workflow_rejects_duplicate_stage_ids():
    """Catch ambiguous stage references caused by duplicate stage IDs."""
    with pytest.raises(ValueError, match="duplicate stage_id"):
        WorkflowDefinition(
            "test.default",
            "1",
            "rough_cut",
            (
                StageDefinition("plan", "plan", "test.plan"),
                StageDefinition("plan", "review", "test.review"),
            ),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ArtifactSlotDefinition("", Cardinality.ONE),
        lambda: StageDefinition("", "plan", "test.plan"),
        lambda: StageDefinition("plan", "", "test.plan"),
        lambda: StageDefinition("plan", "plan", ""),
        lambda: WorkflowDefinition("", "1", "rough_cut", ()),
        lambda: WorkflowDefinition("test.default", "", "rough_cut", ()),
        lambda: WorkflowDefinition("test.default", "1", "", ()),
    ],
)
def test_workflow_definitions_reject_empty_ids(factory):
    """Catch blank identifiers at the workflow-definition boundary."""
    with pytest.raises(ValueError, match="must not be empty"):
        factory()


def test_stage_rejects_duplicate_dependencies():
    """Catch duplicated graph edges that make dependency declarations ambiguous."""
    with pytest.raises(ValueError, match="duplicate dependencies"):
        StageDefinition("publish", "publish", "test.publish", depends_on=("review", "review"))


def test_workflow_returns_deterministic_declaration_order_topology():
    """Catch traversal changes that reorder independent ready stages."""
    workflow = WorkflowDefinition(
        "test.default",
        "1",
        "rough_cut",
        (
            StageDefinition("outline", "plan", "test.outline"),
            StageDefinition("script", "write", "test.script", depends_on=("outline",)),
            StageDefinition("assets", "gather", "test.assets"),
            StageDefinition(
                "render", "render", "test.render", depends_on=("script", "assets")
            ),
        ),
    )

    assert workflow.topological_stage_ids() == ("outline", "assets", "script", "render")


def test_stage_copies_definition_collections_to_immutable_values():
    """Catch shared mutable stage definitions and mutable DAG collection state."""
    inputs = {"source": ArtifactSlotDefinition("source", Cardinality.ONE)}
    outputs = {"summary": ArtifactSlotDefinition("summary", Cardinality.OPTIONAL)}
    depends_on = ["analyze"]

    stage = StageDefinition("plan", "plan", "test.plan", inputs, outputs, depends_on)

    inputs["other"] = ArtifactSlotDefinition("other", Cardinality.ONE)
    outputs["other"] = ArtifactSlotDefinition("other", Cardinality.ONE)
    depends_on.append("other")

    assert stage.inputs == {"source": ArtifactSlotDefinition("source", Cardinality.ONE)}
    assert stage.outputs == {"summary": ArtifactSlotDefinition("summary", Cardinality.OPTIONAL)}
    assert stage.depends_on == ("analyze",)
    assert isinstance(stage.inputs, MappingProxyType)
    assert isinstance(stage.outputs, MappingProxyType)


def test_registry_rejects_duplicate_logical_executor_id():
    """Catch an overwrite that silently changes a workflow's executor target."""
    registry = ExecutorRegistry()
    registry.register("test.echo", lambda value: value)

    with pytest.raises(ValueError, match="duplicate executor"):
        registry.register("test.echo", lambda value: value)


def test_registry_rejects_empty_logical_executor_id():
    """Catch an executor entry that cannot be unambiguously resolved."""
    with pytest.raises(ValueError, match="must not be empty"):
        ExecutorRegistry().register("", lambda: None)


def test_registry_raises_specific_error_for_unknown_logical_executor_id():
    """Catch an unresolved logical executor being mistaken for a callable failure."""
    with pytest.raises(ExecutorNotFoundError, match="unknown executor"):
        ExecutorRegistry().resolve("test.missing")


def test_registry_passes_positional_and_keyword_arguments_to_executor():
    """Catch registry execution that drops caller-supplied executor arguments."""
    registry = ExecutorRegistry()
    registry.register(
        "test.combine", lambda prefix, value, suffix="": f"{prefix}{value}{suffix}"
    )

    assert registry.execute("test.combine", "a", "b", suffix="c") == "abc"


def test_existing_video_analysis_adapter_uses_logical_id():
    """Catch the adapter bypassing the logical registry or changing analyzer arguments."""
    calls = []

    def analyzer(url, topic, sample_interval=4.0):
        calls.append((url, topic, sample_interval))
        return {"summary": "grounded"}

    registry = ExecutorRegistry()
    register_existing_executors(registry, analyzer=analyzer)
    result = registry.execute(
        "video_grounded.analyze_source",
        "https://example.invalid/source",
        "roast duck",
        sample_interval=6.0,
    )

    assert result == {"summary": "grounded"}
    assert calls == [("https://example.invalid/source", "roast duck", 6.0)]
