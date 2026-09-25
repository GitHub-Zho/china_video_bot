# Project / Stage Runtime v1 — Implementation Notes

This change implements R1–R9 from
`PROJECT_STAGE_RUNTIME_IMPLEMENTATION_GUIDE_V1.md` as an additive local layer.
The locked Workflow and Artifact contracts are unchanged.

## Runtime modules added

- `runtime.models`: immutable `ProjectRuntimeV1`, `StageRunV1`,
  `TaskAttemptV1`, binding, approval, error and derived Stage-view models with
  versioned serialization.
- `runtime.repository`: private filesystem persistence with atomic record writes,
  locked monotonic attempt allocation and append-only attempt history.
- `runtime.services`: exact Workflow catalog/pinning, Project lifecycle,
  Active selection, Stage approval and Runtime Artifact reference checking.
- `runtime.bindings`: exact source, Project-input and upstream-Active binding
  resolution with explicit ambiguity/type/cardinality failures.
- `runtime.views`: derived activity, freshness, review and runnable state.
- `runtime.tasks`: queued/running/terminal transitions, cancellation request,
  retry and local restart recovery from `running` to `interrupted`.
- `runtime.execution`: synchronous execution/reuse through the existing logical
  `ExecutorRegistry`, public Artifact saves and initial-Active behavior.

## Public APIs introduced

The `runtime` package exports the three v1 records and their support types plus:

```text
RuntimeRepository
WorkflowCatalog
ProjectService
BindingResolver
StageStateResolver
LocalTaskManager
WorkflowRuntime
ArtifactOutputSpec
RuntimeArtifactReferenceChecker
adapt_video_analysis_executor
```

`Save Version`, `Set Active` and `Approve` remain separate operations.
Executing a later revision does not replace an existing Project Active or Stage
Approved selection. Retry allocates a new `TaskAttemptV1`.

## Persistence layout

The local backend keeps versioned JSON records below its private
`.runtime-repository/` directory, separated into Project, StageRun and
TaskAttempt records. Callers use `RuntimeRepository`; no GUI/service contract
depends on these paths. Writes use temporary files plus atomic replacement.
Attempt allocation is serialized by the long-lived StageRun lock and protected
by a recoverable private allocation journal, so reserved numbers are never
reused across a write-boundary crash. Project mutations use one locked
read-modify-write operation; direct snapshot saves require a repository-owned,
monotonically advancing update token.

## Artifact and Workflow integration

- Runtime reads and saves Artifacts only through `ArtifactRepository`.
- `RuntimeArtifactReferenceChecker` is injected through the existing
  `external_reference_checker` seam and protects Project Active, Project input
  and Stage Approved references from purge.
- Freshness compares current exact Runtime references to immutable Artifact
  `input_refs`, then propagates stale state through the pinned Workflow DAG.
- The existing `ExecutorRegistry` and
  `video_grounded.analyze_source` logical adapter are invoked through a thin
  Runtime adapter that resolves URL/topic inputs from exact bound
  Asset/Artifact IDs without changing the legacy analyzer's call shape.
- Executor outputs receive `project_id` and `stage_run_id` producer provenance;
  these fields do not own Runtime state.
- Reuse creates a `TaskAttemptV1(operation="reuse")` and points at an existing
  compatible Artifact instead of creating a duplicate.

## Legacy behavior left untouched

No changes were made to:

```text
scripts/run.py
orchestrator.py
launcher/
agents/
FFmpeg/rendering
TTS
QA
publishing
legacy output paths
```

The legacy one-shot path and the new staged Runtime path therefore coexist.

## Tests added

Focused Runtime tests cover:

- schema round trips and version rejection;
- monotonic attempt allocation, retry history and restart recovery;
- pinned Workflow resume;
- separate Active and Approved pointers;
- reversible recursive stale/fresh derivation;
- Runtime-aware Artifact purge blocking;
- source Artifact retention after Project deletion;
- execution through the existing logical analysis adapter;
- first-bind without later silent Active replacement;
- existing-Artifact reuse without duplication; and
- explicit ambiguous-binding failure;
- recoverable attempt allocation and active-attempt exclusion;
- stale Project snapshot rejection;
- archived-to-active restoration; and
- complete required-output validation before freshness/approval.

Final verification on 2026-09-24:

```text
119 passed
```

This includes the Runtime, Workflow, Artifact, launcher UI/core and legacy
result-marker tests.

## Compatibility choices and deviations

There is no semantic deviation from a `[LOCKED CONTRACT]` section.

Workflow v1 does not declare Asset slots, so the binding resolver conservatively
includes every pinned Project source role in a Stage's effective inputs. This
avoids false freshness and can be narrowed when a future Workflow schema adds
first-class Asset-slot declarations.

Execution remains synchronous/local. Executors receive a
`RuntimeExecutionContext`; the Runtime API does not accept arbitrary untracked
executor arguments. Content-affecting data must first be represented by a
stable source or Artifact binding.

The filesystem backend makes each canonical record write atomic. It does not
attempt a multi-record database transaction or event sourcing.

## Remaining risks and next boundary

- A process failure between saving several output Artifacts can leave valid but
  unselected Artifacts. It cannot corrupt an existing Active/Approved pointer.
- Artifact storage and Runtime storage are separate repositories, so a future
  multi-process UI should coordinate Artifact deletion against Runtime pointer
  creation at the application-service boundary.
- The staged Runtime is not yet exposed in the Gradio launcher or legacy CLI.

Before `video_understanding.v1`, the next work should provide a concrete Asset
resolver/composition root and register the production Workflow definitions and
payload adapter. It should then implement the Understand/Review vertical slice.
This Runtime change intentionally stops before those payload and UI migrations.
