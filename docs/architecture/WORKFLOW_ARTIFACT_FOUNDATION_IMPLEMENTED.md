# Workflow / Artifact Foundation — Implementation Notes

This branch implements only the locked foundation described in
`CODEX_ARCHITECTURE_HANDOFF.md`, `ARTIFACT_SCHEMA_V1.md`, and
`CODEX_IMPLEMENTATION_TASK.md`.

## Added

- `workflow.models`: immutable Workflow, Stage, Slot, phase, dependency and
  cardinality definitions with deterministic DAG validation.
- `workflow.executors`: logical executor registry, independent of module paths
  and model/provider names.
- `workflow.adapters`: a lazy `video_grounded.analyze_source` adapter around the
  existing `agents.video_analyst_agent.analyze_video` analysis path.
- `artifacts.models`: Artifact Family/Revision v1, owner, producer, input,
  content and checksum models.
- `artifacts.drafts`: mutable Working Drafts that save as new immutable formal
  Revisions rather than overwriting saved content.
- `artifacts.storage`: atomic checksummed `file` storage and content-addressed,
  deduplicated `blob` storage.
- `artifacts.repository`: filesystem Family/Revision repository with atomic
  revision reservation, Preferred selection, soft delete/restore/purge,
  dependency lookup, content resolution and mark-and-sweep GC.

Higher layers use the public Python interfaces. The `.artifact-repository/`,
`files/`, `blobs/`, lock files and JSON record layout are private local-backend
details and must not become Project or GUI contracts.

## Existing behavior preserved

No existing Mode 1/Mode 2 orchestration, CLI, launcher, Agent, FFmpeg, QA,
review or publishing path is migrated in this change. The logical analysis
adapter proves the executor seam but is not wired into `scripts/run.py` or
`orchestrator.py` yet. Existing output directories are not moved or rewritten.

## Connection point for the next migration

After Project Runtime and Stage Runtime are designed, the first migration can:

1. pin one Project to `workflow_id` and `workflow_version`;
2. bind Stage input Slots to exact Artifact IDs;
3. run `video_grounded.analyze_source` through the executor registry;
4. save its reusable source analysis in an Asset-owned Family;
5. record the Project-active and Stage-approved Artifact IDs in their proper
   Runtime owners; and
6. derive stale downstream results from exact input bindings.

## Intentionally deferred Runtime decisions

This branch does not define Project Runtime or Stage Runtime schemas. The next
design must decide:

- pinned Workflow/version representation;
- source Asset references and Project-level settings;
- Project-active Artifact bindings;
- Stage-approved output and exact Stage input bindings;
- stale/fresh derivation after active input changes;
- Project clone/fork semantics;
- external references that block Artifact purge; and
- which source analyses remain reusable after Project deletion.

Payload schemas for video understanding, edit plans and timelines also remain
deferred. No task/attempt schema, conditional language, parallel scheduler or
advanced workflow canvas is introduced here.
