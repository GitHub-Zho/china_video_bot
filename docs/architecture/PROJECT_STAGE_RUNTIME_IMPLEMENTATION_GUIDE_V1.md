# Project / Stage Runtime v1 — Implementation Guide

> Status: **IMPLEMENTATION CONTRACT / LOCKED RUNTIME BASELINE**
>
> Repository baseline when this guide was written: `main` at `23925481edec7cffa87abae055eaadd49d7bfce4` (`feat(artifacts): add filesystem revision repository`).
>
> Read with:
>
> ```text
> docs/architecture/CODEX_ARCHITECTURE_HANDOFF.md
> docs/architecture/ARTIFACT_SCHEMA_V1.md
> docs/architecture/WORKFLOW_ARTIFACT_FOUNDATION_IMPLEMENTED.md
> ```

This document tells a future Work/Codex implementation session how to add the Project / Stage Runtime layer **without re-designing the already locked Workflow / Artifact foundation and without breaking the existing Mode 1 / Mode 2 pipeline while migration is in progress**.

Reasoning and examples are included to preserve context and prevent semantic mistakes. They are **not invitations to re-open locked design decisions**. If the current repository makes a locked contract impossible to implement, document the incompatibility first and make the smallest compatibility-preserving deviation rather than silently changing the architecture.

---

## 1. Objective

Add the persistent execution/runtime layer that makes one Project:

- resumable;
- reproducible;
- reviewable;
- version-aware;
- stale-aware;
- retryable;
- able to reuse existing Artifact revisions;
- safe to migrate incrementally from the current one-shot pipeline.

The first implementation must remain local-first and suitable for Python + Gradio. It should not introduce distributed infrastructure merely because the data model leaves room for future concurrency.

The intended dependency direction is:

```text
WorkflowDefinition / Executor Registry
                ↓
            Runtime
                ↓
        Artifact public API
                ↓
       filesystem backend
```

The reverse dependency is forbidden:

```text
Artifact package
    ✕ must not import ProjectRuntime / StageRun / TaskAttempt
```

---

## 2. Migration goal and success metrics

This runtime work is a **new additive layer**, not a destructive conversion of the working application.

### [LOCKED CONTRACT] Compatibility target

During initial Runtime implementation:

- existing `scripts/run.py` paths continue to work;
- existing `orchestrator.py` behavior continues to work;
- existing launcher behavior continues to work;
- existing Agents remain callable;
- existing FFmpeg / TTS / QA / publishing code remains usable;
- existing output folders are not moved or rewritten merely to satisfy Runtime layout;
- existing Artifact Family / Revision records are not migrated to a different identity model;
- existing Artifact storage layout is not exposed to Runtime or GUI code.

### [LOCKED CONTRACT] No bad v1 migration

Runtime v1 must **not** require any of the following:

```text
rewrite ArtifactRevision fields
renumber Artifact revisions
change Artifact Family identity
replace content_ref semantics
move existing blobs/files
make Project Active equal Family Preferred
store Stage Approved on ArtifactRevision
force old output directories into a new format
replace the working legacy pipeline before the staged path is proven
```

### Acceptance metrics

The Runtime implementation is considered safe only if all of these remain true:

1. Existing legacy smoke tests still pass.
2. Workflow / Artifact foundation tests still pass.
3. Runtime never scans `.artifact-repository/`, `files/`, `blobs/`, or Artifact lock files directly.
4. Runtime reaches Artifact content only through public Artifact repository/model APIs.
5. Creating Runtime state does not mutate an existing Artifact Revision.
6. Changing Family Preferred does not silently change Project Active.
7. Changing Project Active does not delete old Artifacts.
8. Resume preserves the same pinned Workflow/version and exact selected inputs.
9. Stale is derived from exact bindings and is reversible when bindings return to a prior matching state.
10. Retry creates a new TaskAttempt; it never overwrites attempt history.
11. Artifact purge is blocked while Runtime still references the Artifact as Active or Approved.
12. Asset-owned reusable source analysis can outlive deletion of a Project.

---

## 3. What is already locked and must not be redesigned

### Workflow / Artifact foundation

```text
Mode
→ Workflow
→ Phase
→ Stage DAG
→ Executor / Strategy
→ Capability
```

Workflow definitions contain semantic Stage I/O slots and logical executor IDs. They do not contain Project-specific Artifact IDs.

Artifact v1 remains the source of truth for:

```text
ArtifactFamily
ArtifactRevision
WorkingDraft
Family Preferred
Artifact dependency input_refs
Artifact content_ref
soft delete / restore / purge
Artifact dependency lookup / GC
```

State ownership remains:

```text
Family Preferred → ArtifactFamily
Project Active    → Project Runtime
Stage Approved    → Stage Runtime
```

Do not collapse these pointers.

---

## 4. Runtime conceptual boundary

### [LOCKED CONTRACT] ProjectRuntime

ProjectRuntime answers:

> What exact persistent context is this Project currently using?

It owns:

- Project identity and lifecycle;
- one pinned `workflow_id + workflow_version`;
- source Asset bindings;
- Project-level versioned input Artifact bindings;
- Project Active Stage-output Artifact bindings;
- `stage_id → stage_run_id` references;
- optional lineage metadata;
- small UI/navigation convenience state.

It does **not** own large Artifact payloads, Task logs, executor internals, model configuration, FFmpeg details, or retry history.

### [LOCKED CONTRACT] StageRun

StageRun is the long-lived runtime object for:

```text
one Project + one StageDefinition
```

It owns:

- stage runtime identity;
- attempt allocation/history references;
- the exact approved output snapshot for that Stage.

It does not own another ambiguous "current output" pointer.

### [LOCKED CONTRACT] TaskAttempt

TaskAttempt is one concrete execution / retry / reuse event.

It owns:

- exact effective inputs used by that attempt;
- executor ID;
- outputs produced/reused by that attempt;
- queued/running/terminal state;
- timestamps;
- cancellation request;
- structured error.

A retry creates a new TaskAttempt.

---

## 5. ProjectRuntime v1 schema

Recommended value model shape:

```python
class ProjectRuntimeV1:
    schema_version: Literal["project_runtime.v1"]

    project_id: str
    display_name: str

    workflow: WorkflowPin

    lifecycle_status: Literal["active", "archived"]
    deleted_at: str | None

    source_bindings: dict[str, AssetBinding]
    artifact_bindings: ProjectArtifactBindings

    stage_runs: dict[str, str]  # stage_id -> stage_run_id

    lineage: ProjectLineage | None

    created_at: str
    updated_at: str

    ui_state: ProjectUIState | None
```

Supporting shapes:

```python
class WorkflowPin:
    workflow_id: str
    workflow_version: str

AssetBinding = str | list[str]
ArtifactBinding = str | list[str] | None

class ProjectArtifactBindings:
    # Versioned Project-owned inputs, e.g. future director/project control.
    project_inputs: dict[str, ArtifactBinding]

    # Exact Active outputs selected by this Project.
    stage_outputs: dict[
        str,                       # stage_id
        dict[str, ArtifactBinding] # output_slot -> artifact_id(s)
    ]

class ProjectLineage:
    parent_project_id: str
    relation: Literal["clone", "fork"]

class ProjectUIState:
    selected_stage_id: str | None
```

Example:

```json
{
  "schema_version": "project_runtime.v1",
  "project_id": "proj_001",
  "display_name": "Shanghai Rough Cut",
  "workflow": {
    "workflow_id": "rough_cut.default",
    "workflow_version": "1.0"
  },
  "lifecycle_status": "active",
  "deleted_at": null,
  "source_bindings": {
    "primary_video": "asset_video_001"
  },
  "artifact_bindings": {
    "project_inputs": {
      "director_control": "art_control_003"
    },
    "stage_outputs": {
      "analyze_source": {
        "understanding": "art_U7"
      },
      "plan_highlights": {
        "plan": "art_P4"
      },
      "build_timeline": {
        "timeline": "art_T2"
      }
    }
  },
  "stage_runs": {
    "analyze_source": "stage_run_101",
    "plan_highlights": "stage_run_102",
    "build_timeline": "stage_run_103",
    "render_preview": "stage_run_104"
  },
  "lineage": null,
  "created_at": "...",
  "updated_at": "...",
  "ui_state": {
    "selected_stage_id": "plan_highlights"
  }
}
```

### [RATIONALE — CONTEXT ONLY]

Project root stays compact so reopening a Project does not require copying or parsing all payloads. Exact IDs make resume/reproducibility possible and allow future repository backends without making GUI code depend on filesystem locations.

---

## 6. StageRun v1 schema

```python
class StageRunV1:
    schema_version: Literal["stage_run.v1"]

    stage_run_id: str
    project_id: str
    stage_id: str

    next_attempt: int
    task_ids: list[str]

    approval: ApprovalSnapshot | None

    created_at: str
    updated_at: str

class ApprovalSnapshot:
    approved_outputs: dict[str, ArtifactBinding]
    approved_at: str
```

Example:

```json
{
  "schema_version": "stage_run.v1",
  "stage_run_id": "stage_run_102",
  "project_id": "proj_001",
  "stage_id": "plan_highlights",
  "next_attempt": 4,
  "task_ids": ["task_001", "task_002", "task_003"],
  "approval": {
    "approved_outputs": {
      "plan": "art_P4"
    },
    "approved_at": "..."
  },
  "created_at": "...",
  "updated_at": "..."
}
```

### [LOCKED CONTRACT] Do not add a fourth output pointer

Do not add a StageRun field whose semantics are vaguely "current output" or "latest output".

The meanings are already complete:

```text
Generated/reused by attempt → TaskAttempt.output_bindings
Selected for Project        → ProjectRuntime Active
Approved by human/stage      → StageRun approval
Recommended by Family        → ArtifactFamily Preferred
```

Adding another StageRun output pointer creates drift and ambiguity.

---

## 7. TaskAttempt v1 schema

```python
class TaskAttemptV1:
    schema_version: Literal["task_attempt.v1"]

    task_id: str
    stage_run_id: str
    attempt: int

    operation: Literal["execute", "reuse"]

    status: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    ]

    executor_id: str | None

    input_bindings: dict[str, InputBinding]
    output_bindings: dict[str, ArtifactBinding]

    created_at: str
    queued_at: str | None
    started_at: str | None
    finished_at: str | None

    cancel_requested_at: str | None
    error: TaskError | None

class RuntimeRef:
    kind: Literal["asset", "artifact"]
    id: str

InputBinding = RuntimeRef | list[RuntimeRef] | None

class TaskError:
    code: str
    message: str
    retryable: bool
```

Example execution:

```json
{
  "schema_version": "task_attempt.v1",
  "task_id": "task_003",
  "stage_run_id": "stage_run_102",
  "attempt": 3,
  "operation": "execute",
  "status": "succeeded",
  "executor_id": "rough_cut.plan_highlights",
  "input_bindings": {
    "understanding": {"kind": "artifact", "id": "art_U7"},
    "source": {"kind": "asset", "id": "asset_video_001"},
    "director_control": {"kind": "artifact", "id": "art_control_003"}
  },
  "output_bindings": {
    "plan": "art_P5"
  },
  "created_at": "...",
  "queued_at": "...",
  "started_at": "...",
  "finished_at": "...",
  "cancel_requested_at": null,
  "error": null
}
```

### [LOCKED CONTRACT] Retry history

```text
Attempt 1 failed
Attempt 2 failed
Attempt 3 succeeded
```

must remain three records. Never overwrite Attempt 1 to make it appear successful.

`next_attempt` must increase monotonically; an allocated attempt number is never reused.

---

## 8. Project / Artifact decoupling contract

The current Artifact package already provides the correct public seam. Runtime must use it rather than Artifact storage internals.

### Runtime may depend on public Artifact interfaces

Examples include:

```text
ArtifactRepository.get_revision(...)
ArtifactRepository.get_or_create_family(...)
ArtifactRepository.create_draft(...)
ArtifactRepository.save_revision(...)
ArtifactRepository.resolve_content(...)
ArtifactRepository.list_dependencies(...)
ArtifactRepository.list_dependents(...)
```

Exact method names already implemented in the repository should be reused rather than wrapped without reason.

### Runtime must not depend on Artifact backend layout

Forbidden Runtime behavior:

```text
scan .artifact-repository/revisions/*.json
read Artifact index.json directly
construct files/... paths manually
construct blobs/... paths manually
mutate Artifact JSON records directly
```

### ArtifactProducer is provenance, not Runtime ownership

Artifact v1 allows optional:

```text
project_id
stage_run_id
```

inside `ArtifactProducer`.

These fields mean:

> which Project/StageRun produced this Revision, if applicable

They do **not** mean:

```text
Artifact owns Stage state
Artifact owns approval
Artifact owns Active selection
```

Source-level reusable analysis may still be produced without a Project.

---

## 9. Runtime external-reference integration with Artifact purge

The current `ArtifactRepository` already accepts:

```python
external_reference_checker: Callable[[str], bool] | None
```

Use this seam rather than making `artifacts` import Runtime.

Recommended composition:

```text
Application composition root
        │
        ├── RuntimeRepository / RuntimeReferenceChecker
        │
        └── ArtifactRepository(
                external_reference_checker=runtime_reference_checker
            )
```

The checker returns true if an Artifact is still referenced by retained Runtime state, at minimum:

```text
Project Active
Stage Approved
```

### [EXAMPLE]

```text
proj_1 Active understanding = art_U3
stage_run_10 Approved        = art_U3
```

A purge request for `art_U3` must be blocked.

After the Project switches Active away from U3 and the Stage approval no longer references U3, purge may proceed if Artifact-level dependency checks also allow it.

This callback is intentional integration, not tight coupling.

---

## 10. Project Active / Preferred / Approved semantics

### [LOCKED CONTRACT]

```text
Family Preferred != Project Active != Stage Approved
```

A newly saved Revision never silently replaces an existing Project Active selection.

Allowed first-bind behavior:

```text
Active slot is empty
→ first successful output may become initial Active
```

But:

```text
Active = U1
new output U2 generated
→ Active remains U1 unless user/runtime action explicitly selects U2
```

### [EXAMPLE]

After Task 1:

```text
Generated = U1
Active    = U1
Approved  = U1
```

After regeneration:

```text
Generated latest = U2
Active           = U1
Approved         = U1
```

This is valid.

User selects U2:

```text
Active   = U2
Approved = U1
```

The Stage is now `needs_review` for the current Active selection.

User approves U2:

```text
Active   = U2
Approved = U2
```

---

## 11. Save, Set Active and Approve are separate operations

### [LOCKED CONTRACT]

```text
Save Version != Set Active != Approve
```

Recommended service/API semantics:

```text
save_revision(...)
set_active(project_id, stage_id/input_key, slot, artifact_id)
approve_stage(project_id, stage_id)
```

UI may later provide compound actions such as "Save & Set Active", but the domain operations must remain separable.

---

## 12. Stale / fresh derivation

### [LOCKED CONTRACT]

Do not make a persisted `dirty=true` or `stale=true` flag the canonical truth.

Freshness is derived from **exact dependency bindings**.

Conceptually:

```text
current expected inputs for Stage
            ==
inputs that produced current Active output
```

means fresh.

A mismatch means stale.

Artifact `input_refs` are the durable dependency record for formal output Revisions. TaskAttempt also stores the exact execution input snapshot for audit/retry.

### [EXAMPLE] Reversible stale state

Initial chain:

```text
Understanding U3
      ↓
Plan P4
      ↓
Timeline T2
```

Project changes Active Understanding:

```text
U3 → U7
```

Then P4 and T2 are stale for the current Project state, but **remain stored and selectable**.

If the Project later switches back:

```text
U7 → U3
```

and the exact dependency chain matches again, P4/T2 can become fresh again without recomputation.

This reversibility is the reason a simple dirty boolean is insufficient.

### [IMPLEMENTATION GUIDANCE]

For v1, conservative invalidation is preferred to false freshness. Formal output Artifacts produced by an executor should record all effective data dependencies needed to decide freshness.

Do not silently omit a dependency merely because it is inconvenient to thread through an executor adapter.

---

## 13. Input binding resolution

Runtime must resolve Stage execution to stable IDs before invoking an executor.

Conceptually:

```text
Workflow Stage definition
+ Project source bindings
+ Project versioned input bindings
+ upstream Project Active outputs
+ Stage/DAG requirements
        ↓
exact TaskAttempt.input_bindings
```

### Current Workflow v1 seam

Current Workflow models explicitly describe Artifact slots. Project source Assets are kept separately in `ProjectRuntime.source_bindings`.

Do **not** redesign `workflow.models` merely to force Asset bindings into Artifact slot definitions during this Runtime task.

For P0:

- validate declared Artifact inputs against the existing Stage slot definitions;
- make required source Asset roles available to executor adapters through Runtime execution context / source bindings;
- record source Assets as `kind=asset` in TaskAttempt inputs and resulting Artifact `input_refs` where they are true data dependencies;
- fail clearly on ambiguous input resolution rather than guessing.

If future Workflow design needs first-class Asset slots, treat that as a separate schema extension rather than silently mutating Workflow v1 now.

### Slot-resolution safety

Do not infer between multiple candidate upstream Artifacts only by "latest" revision.

Prefer exact Project Active bindings. If a current Workflow definition leaves a Stage input ambiguous, raise a structured binding error or use an explicit Runtime/preset mapping; do not choose a Family Preferred/latest Revision silently.

---

## 14. Derived StageView; do not force one canonical Stage status

A Stage can legitimately have overlapping facts, e.g.:

```text
old approved output is still active
+ a regeneration Task is currently running
```

A single canonical enum such as `approved` vs `running` cannot represent this cleanly.

Derive a view similar to:

```python
class StageView:
    activity: Literal["idle", "queued", "running"]
    freshness: Literal["no_output", "fresh", "stale"]
    review: Literal["not_required", "needs_review", "approved"]
    runnable: bool
    blocked_by: list[str]
    latest_attempt_status: str | None
```

GUI can present combined labels such as:

```text
Approved
Approved · Regenerating…
Needs Review
Stale
Failed — old approved result still active
Ready
Blocked
```

The view is derived from ProjectRuntime, StageRun, TaskAttempt history, WorkflowDefinition and Artifact dependencies.

---

## 15. Runnable / DAG semantics

Runtime determines runnable Stages; GUI does not.

At minimum provide logic equivalent to:

```text
get_runnable_stages(project_id)
can_run_stage(project_id, stage_id)
blocked_by(project_id, stage_id)
```

Requirements:

- respect Workflow DAG dependencies;
- respect required approval gates;
- respect required input bindings;
- do not treat GUI navigation as execution state;
- preserve DAG semantics even if v1 dispatch executes sequentially.

Parallel scheduling is not required in this task.

---

## 16. Resume semantics

### [LOCKED CONTRACT]

Reopening/resuming a Project must preserve:

```text
same project_id
same workflow_id + workflow_version
same source context
same selected Project Active Artifact IDs
same Stage approvals
same dependency graph semantics
```

This directly fixes the existing Mode 2 class of bug where a review stop could later continue through a generic `--from-brief` path with different source-grounding semantics.

Runtime must never silently resume a Project through a different generic pipeline because that path happens to be easier to call.

### Crash recovery

For local v1, if persistence says:

```text
TaskAttempt.status == running
```

but the application has restarted and the original local worker no longer exists, recover it to:

```text
interrupted
```

and permit explicit retry.

Do not invent distributed leases/heartbeats yet.

---

## 17. Reuse semantics

Reuse is a first-class TaskAttempt operation:

```text
operation = reuse
```

A reuse attempt may point output bindings at an existing Artifact Revision rather than creating a duplicate Revision.

Example:

```text
asset_video_001 already has reusable video_understanding art_U3
new Project selects/reuses art_U3
```

Record enough TaskAttempt context to explain why the Stage completed without rerunning the expensive analysis.

Do not duplicate source-level reusable analysis simply to make it Project-owned.

---

## 18. Project lifecycle

Minimum lifecycle:

```text
active
archived
deleted_at nullable
```

Recommended behavior:

### create

- allocate Project ID;
- pin exact Workflow ID/version;
- validate source bindings;
- create persistent StageRun objects for Workflow Stages (or an equivalent deterministic initialization that yields exactly one long-lived StageRun per Project+Stage);
- persist Project root.

### open

- load exact pinned Workflow version;
- validate references;
- recover interrupted local tasks;
- recompute derived Stage views/freshness.

### archive

- remain readable/reviewable;
- normally block new task execution until restored active.

### delete

- soft-delete Project runtime state;
- do not cascade-delete source Assets;
- do not cascade-delete reusable Asset-owned Artifacts;
- do not automatically purge Project-owned Artifacts in v1.

### restore

- restore Project runtime visibility/state;
- revalidate all references.

### clone/fork

Not required for first Runtime implementation, but schema must remain compatible with future lineage via `ProjectLineage`.

---

## 19. Persistence boundary

Implement Runtime persistence behind repositories/services rather than direct GUI filesystem scans.

Recommended conceptual interfaces:

```text
ProjectRuntimeRepository
StageRunRepository
TaskAttemptRepository
```

They may use JSON/filesystem locally in v1.

The exact local directory layout is an implementation detail. It is acceptable to use a readable `projects/<project_id>/...` layout if it fits the repository, but callers should still use repository APIs so later SQLite/PostgreSQL migration does not require rewriting GUI/business logic.

Persistence writes that change canonical Runtime pointers should be atomic enough to avoid half-written Project state on normal local crashes.

Do not build event sourcing for v1.

---

## 20. Recommended module responsibility, not mandatory filenames

Adapt to the repository instead of forcing a directory tree, but keep these responsibilities separate:

```text
runtime/
├── project models / repository
├── stage-run models / repository
├── task-attempt models / repository
├── binding resolver
├── dependency/freshness resolver
├── stage view / runnable resolver
├── workflow engine
├── local task manager
└── recovery
```

Service layer may expose responsibilities equivalent to:

```text
ProjectService
TaskService
Runtime/Workflow service
```

Stage-specific business services such as Understanding/Director/Render belong later and should call into Runtime rather than re-implementing its state logic.

---

## 21. Integration strategy: preserve working functionality

### [LOCKED CONTRACT] Adapter-first migration

Use this migration pattern:

```text
existing capability / Agent
        ↓
thin logical executor adapter
        ↓
Runtime Stage execution
        ↓
Artifact save + Runtime bindings
```

Do not start by rewriting `orchestrator.py` or all Agents.

The repository already demonstrates the intended seam with `video_grounded.analyze_source` adapting the existing video analyst path through the Executor Registry. Extend that pattern gradually.

### First safe vertical slice

A good first Runtime-connected vertical slice is:

```text
Create Project
→ bind source Asset
→ execute existing video_grounded.analyze_source adapter
→ save reusable source analysis Artifact
→ set initial Project Active understanding
→ optionally approve exact revision
→ close Project
→ reopen Project
→ verify same workflow/source/Artifact selection
```

Do not migrate all Mode 1 / Mode 2 behavior at once.

### Dual-path period is expected

For a migration period it is acceptable that:

```text
legacy one-shot path
and
new staged Runtime path
```

coexist.

Do not remove the legacy path until equivalent staged behavior has tests and has been explicitly accepted.

---

## 22. Implementation phases

### R1 — Runtime value models

Implement and test:

```text
ProjectRuntimeV1
StageRunV1
TaskAttemptV1
supporting binding/error/approval types
serialization/deserialization
schema-version validation
```

No legacy wiring yet.

### R2 — Runtime repositories

Implement local persistence with:

```text
create/get/save/list Project
create/get/save StageRun
allocate/save/list TaskAttempt
atomic attempt allocation
archive/delete/restore Project
```

Keep storage layout private to repository callers.

### R3 — Artifact reference integration

Implement Runtime reference lookup for Artifact IDs and inject it through the existing ArtifactRepository external-reference checker.

Test purge blocking for Project Active and Stage Approved references.

### R4 — Binding resolver + StageView

Implement:

```text
resolve exact Stage inputs
validate Artifact slot cardinality/type where available
derive runnable/blocked state
derive review state
derive freshness/stale state
```

Ambiguity must fail explicitly.

### R5 — Project lifecycle / resume / recovery

Implement:

```text
create
open
resume
archive
delete/restore
running -> interrupted recovery after restart
```

### R6 — Task execution boundary

Implement local Task manager sufficient for:

```text
queued
running
succeeded
failed
cancel_requested
cancelled
interrupted
retry
```

Do not require Celery/Redis.

### R7 — First executor migration

Connect the already-existing logical analysis adapter through Runtime without changing the legacy path.

Persist exact Task inputs/outputs and producer provenance.

### R8 — Active / approval / stale vertical behavior

Prove:

```text
save revision
set active
approve
switch active
stale descendants
switch back
freshness restored when exact dependencies match
```

### R9 — Compatibility smoke tests

Run old tests plus new Runtime/Artifact integration tests before any larger Stage/payload migration.

---

## 23. Required tests / acceptance scenarios

At minimum add tests covering these scenarios.

### A. Workflow pinning

```text
Create Project on workflow W@1
reopen after W@2 exists
→ Project still uses W@1
```

### B. New Revision does not silently replace Active

```text
U1 Active + Approved
generate U2
→ U1 remains Active + Approved
```

### C. Active/Approved mismatch

```text
Approved U1
Set Active U2
→ review = needs_review
```

### D. Stale descendants

```text
U3 → P4 → T2
switch Active U3 → U7
→ P4/T2 stale
→ no Artifact deleted
```

### E. Reversible freshness

```text
switch Active U7 → U3
→ old P4/T2 fresh again when exact dependencies match
```

### F. Retry history

```text
attempt 1 failed
retry
attempt 2 succeeded
→ both attempts remain queryable
```

### G. Crash recovery

```text
persist running task
simulate process restart
→ task becomes interrupted
→ explicit retry allowed
```

### H. Reuse

```text
existing Asset-owned U3 matches source/dependency context
new Project reuses U3
→ operation=reuse
→ no duplicate Artifact required
```

### I. Runtime reference blocks purge

```text
Project Active references U3
or Stage Approved references U3
→ ArtifactRepository.purge_revision(U3) blocked
```

### J. Project deletion preserves reusable source analysis

```text
Project deleted
Asset-owned source understanding remains live/reusable
```

### K. Legacy safety

```text
existing launcher / scripts/run.py tests still pass
existing Workflow/Artifact tests still pass
```

---

## 24. Important anti-patterns

Do not implement any of these:

### Artifact/Runtime coupling

```text
artifacts.repository imports runtime
ArtifactRevision stores project_active
ArtifactRevision stores stage_approved
Runtime edits Artifact repository JSON directly
```

### Silent selection

```text
new Revision → silently Active
new Preferred → silently Active
latest Revision chosen on resume
```

### Destructive migration

```text
move all legacy output folders now
rewrite old outputs into Artifact records automatically
remove scripts/run.py before staged parity
replace working Agents with new Runtime-specific duplicates
```

### False stale logic

```text
one persisted dirty boolean is the only freshness truth
upstream switch permanently destroys old valid downstream chain
```

### Retry/history loss

```text
retry overwrites previous TaskAttempt
failure status erased when later attempt succeeds
```

### GUI orchestration

```text
Gradio click handler directly sequences model → FFmpeg → Artifact → state changes
```

GUI should call service/Runtime APIs.

---

## 25. Explicit non-goals for this Runtime task

Do not implement/finalize yet unless strictly required by a real blocker:

```text
distributed scheduler
Celery / Redis queue
distributed worker leases
WorkflowRun abstraction
event sourcing
PostgreSQL migration
cloud object-store migration
advanced resource groups
automatic parallel scheduler
conditional-stage DSL
ComfyUI-style workflow editor
video_understanding payload schema
edit_plan payload schema
timeline payload schema
Rhythm Cut end-to-end
Match Cut
```

Do not let these defer the local staged Runtime.

---

## 26. Why WorkflowRun is intentionally absent in v1

A Project can be edited and resumed repeatedly:

```text
Analyze today
review later
regenerate Plan
close app
reopen tomorrow
render Preview
```

There is no single obvious "whole workflow run" boundary yet.

Current hierarchy is sufficient:

```text
Project
  → StageRun
      → TaskAttempt
```

If future batch/cloud execution needs a WorkflowRun grouping, it can be added later without changing Artifact identity or the core Runtime pointers.

Do not add it preemptively.

---

## 27. Runtime invariants summary

Treat these as implementation invariants:

1. A Project pins exactly one `workflow_id + workflow_version`.
2. Existing Projects never silently upgrade Workflow versions.
3. Runtime references stable IDs, not primary file paths.
4. Project Active references exact Artifact IDs.
5. Stage Approved references exact Artifact IDs.
6. Family Preferred does not mutate Project Active.
7. New Artifact Revision does not replace existing Active automatically.
8. Save, Set Active and Approve are separate domain operations.
9. StageRun is one long-lived object per Project+Stage in v1.
10. Every execution/retry/reuse creates a TaskAttempt record.
11. Terminal TaskAttempt history is not rewritten to hide prior outcomes.
12. Formal Artifact output dependencies are recorded in `input_refs`.
13. Freshness is derived from exact bindings/dependencies.
14. Stale results remain stored and can become fresh again when bindings match.
15. Resume uses the same pinned Workflow and selected source/Artifact context.
16. Runtime does not depend on private Artifact filesystem layout.
17. Artifact package does not depend on Runtime package.
18. Runtime references participate in Artifact purge safety via the external reference seam.
19. Project deletion does not cascade-purge reusable Asset-owned analysis.
20. GUI navigation is never canonical execution truth.

---

## 28. Current repository connection points

At the baseline commit for this guide, the repository already contains:

```text
workflow.models
workflow.executors
workflow.adapters
artifacts.models
artifacts.drafts
artifacts.storage
artifacts.repository
```

Important existing seams to reuse:

- `WorkflowDefinition.topological_stage_ids()` for DAG validation/order;
- `ExecutorRegistry` for logical executor resolution;
- the existing lazy `video_grounded.analyze_source` adapter;
- `ArtifactRepository` public methods for Family/Revision/content operations;
- `ArtifactRepository(external_reference_checker=...)` for Runtime-aware purge safety;
- `ArtifactProducer.project_id` / `stage_run_id` for optional provenance only;
- Artifact `input_refs` for durable data dependency identity.

Do not duplicate these responsibilities in Runtime.

---

## 29. Handoff to the implementation Work

Before coding:

1. read this file fully;
2. read `ARTIFACT_SCHEMA_V1.md`;
3. read `WORKFLOW_ARTIFACT_FOUNDATION_IMPLEMENTED.md`;
4. inspect the current `workflow/`, `artifacts/`, tests and legacy entry points;
5. confirm `main` has not materially changed these public seams since this guide was written.

Then implement R1 → R9 incrementally.

If file/module placement differs from the suggestions above, adapt the structure while preserving the contracts.

Do **not** spend the Runtime implementation task re-evaluating the architecture. The reasoning/examples above exist so that a Work session without prior chat context understands the intended semantics.

When finished, produce/update an implementation note that records:

```text
what Runtime modules were added
what public APIs were introduced
which legacy code was adapted
what existing behavior remains untouched
which tests pass
what compatibility assumptions were made
what remains before video_understanding.v1
```

---

## 30. Continuation point after Runtime

Once ProjectRuntime v1, StageRun v1, TaskAttempt v1 and their core lifecycle/binding/freshness behavior are implemented and tested, stop expanding Runtime architecture.

The next design/implementation sequence is:

```text
video_understanding.v1
→ Understand / Review vertical slice
→ director_control.v1
→ edit_plan.v1
→ Planner / Plan Review
→ timeline.v1
→ Preview / Final pipeline migration
```

Runtime should be the stable execution substrate for those later Stage payloads, not redesigned separately for each Stage.
