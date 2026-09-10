# Codex Implementation Task — Workflow / Artifact Foundation

## Objective

Implement the already-locked architectural foundation in `china_video_bot` without inventing the not-yet-designed Project Runtime schema.

Read first:

```text
docs/architecture/CODEX_ARCHITECTURE_HANDOFF.md
docs/architecture/ARTIFACT_SCHEMA_V1.md
```

## Implement now

### 1. Workflow definition models

Add explicit models for:

```text
WorkflowDefinition
StageDefinition
Stage I/O Slot definition
cardinality: one / optional / many
phase
executor logical ID
depends_on
approval_required
ui_component
```

Preserve DAG semantics even if execution remains sequential initially.

### 2. Executor registry / adapter boundary

Introduce a logical executor registry so Workflow definitions reference IDs such as:

```text
rough_cut.analyze_source
video_grounded.analyze_source
render.proxy
```

Do not bind Workflow definitions directly to Python module paths or model names.

Do not migrate every existing Agent now. Add enough registry/adaptor structure to prove the boundary and to wrap at least one existing safe/read-only or analysis path where practical.

### 3. Artifact models

Implement the locked:

```text
ArtifactFamily v1
ArtifactRevision v1
owner
producer
input refs
content refs
checksum metadata
soft-delete lifecycle
```

Follow `ARTIFACT_SCHEMA_V1.md`.

### 4. Artifact repository abstraction

Implement a local filesystem-backed repository/service behind a clean interface.

Must support at least:

```text
get/create/find Family
list/get Revisions
atomic Revision allocation
save formal Revision
set/clear Preferred
soft delete
restore
purge policy checks
resolve content
list dependency/dependent references
gc dry-run
```

Avoid scattering raw directory traversal throughout GUI/workflow code.

### 5. Working Draft boundary

Implement or define a clean API for:

```text
saved Revision
→ mutable Working Draft
→ Save Version
→ new Revision
```

Do not allow normal APIs to overwrite a formal saved Revision payload.

### 6. Content storage abstraction

Support:

```text
file
blob
```

Use repository/storage-root-relative paths.

Use SHA-256 over persisted bytes.

Do not copy the same large source media for every Revision.

### 7. Tests

Add unit tests for at least:

- Family uniqueness identity;
- monotonically increasing Revision numbers;
- deleted numbers not reused;
- preferred pointer rules;
- immutable formal Revision behavior;
- parent must be in same Family;
- input-ref validation;
- soft delete / restore;
- purge safety;
- checksum/content resolution;
- Workflow Slot cardinality validation;
- DAG dependency validation / cycle rejection if engine/model layer supports it.

## Do not finalize yet

Do **not** invent and freeze:

```text
Project Runtime Schema
Stage Runtime Schema
Task/attempt execution schema
video_understanding payload schema
edit_plan payload schema
timeline payload schema
advanced conditional-stage language
parallel resource scheduler
ComfyUI-style canvas
```

Stubs/interfaces are acceptable where needed, but mark them clearly as provisional.

## Migration constraints

- Preserve existing `scripts/run.py`, `orchestrator.py`, launcher, agents, and working functionality unless a small compatibility adapter is necessary.
- Prefer additive/refactor-safe changes.
- Do not remove working legacy paths in this task.
- Do not silently change Mode 1/Mode 2 output semantics.
- Do not make the GUI directly orchestrate model/FFmpeg calls.
- Do not move old Projects/outputs into a destructive new format yet.

## Repository review before coding

Before editing, inspect at minimum:

```text
launcher/app.py
launcher/core.py
scripts/run.py
orchestrator.py
agents/
current config / dependency files
current output folder conventions
current tests
```

Then adapt module names/locations to the repository rather than blindly imposing the suggested directory layout.

## Expected output

Produce a focused implementation branch that contains:

1. workflow schema/models;
2. executor registry boundary;
3. Artifact models;
4. filesystem Artifact repository/storage abstraction;
5. tests;
6. short migration notes explaining how existing Mode 1/2 code will connect next.

Do not implement Rough Cut or Rhythm Cut end-to-end in this task.

## Acceptance criteria

The implementation is successful if:

- existing behavior/tests remain working;
- Artifact Family and Revision history can be created/listed/retrieved safely;
- formal Revisions are not silently overwritten;
- revision allocation remains monotonic through deletion;
- source-level Artifact Families can exist independently of a Project;
- Workflow models describe Stage DAG + Slot/Type/cardinality without runtime Artifact IDs;
- code has a clean seam for Project Runtime to be added next;
- the implementation does not force a specific future DB/object-store backend.

## After this task

Stop at the architecture seam and report:

```text
what was added
which legacy code was adapted
what tests pass
what assumptions were necessary
what Project Runtime interfaces are now needed
```

The next design chat will define Project Runtime Schema before that persistence layer is finalized.