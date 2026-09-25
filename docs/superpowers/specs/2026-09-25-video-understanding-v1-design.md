# Video Understanding v1 — Superseded Design Note

**Status:** Superseded by the locked architecture guide

**Normative document:**
`docs/architecture/VIDEO_UNDERSTANDING_IMPLEMENTATION_GUIDE_V1.md`

This file records the earlier conversational design. It is retained so the
decision trail remains understandable, but it is no longer an implementation
contract. Where this note and the architecture guide differ, the architecture
guide controls.

## Why it was superseded

The first design proved the intended Runtime-to-Artifact vertical seam, but it
was narrower than the subsequently locked Understanding architecture. In
particular, it described:

- a single formal `analyze_source` Stage;
- a simplified key-step/dense-timeline payload;
- a Project-owned `analysis_prompt` as a required V1 input;
- direct adaptation of the legacy `VideoUnderstanding` result.

The final handoff requires a formal transcript Stage/Artifact, Semantic
Segments as the canonical unit, sparse human review overlays, field-level lock
snapshots, and three distinct regeneration modes. Those later decisions are
now reflected below and specified exactly in the architecture guide.

## Preserved intent

The following original choices remain valid:

- build a durable Runtime-connected slice rather than a demo-only wrapper;
- preserve Workflow, Artifact, and Runtime foundations;
- preserve legacy launcher, scripts, orchestrator, Agents, output directories,
  rendering, review, and publishing paths;
- keep source identity as a stable Asset ID rather than a URL/path binding;
- save reusable source analysis in Asset-owned Artifact Families;
- keep Save Version, Set Active, and Approve separate;
- never auto-approve Understanding;
- use exact dependency matching for reuse;
- keep provider/model/cache provenance out of semantic payload fields;
- use injected analyzers/transcribers for deterministic automated tests;
- stop before Planner, EditPlan, Timeline, rendering, publishing, and final UI.

## Corrected Analyze flow

The normative flow is:

```text
source Asset
    ↓
transcribe_source
    ↓
transcript.v1 Artifact

source Asset + exact transcript Artifact
    ↓
analyze_source
    ↓
video_understanding.v1 Artifact
    ↓
Understanding Review
```

Visual sampling, keyframes, dense frame observations, VLM batching, and scene
heuristics remain capabilities/internal caches behind `analyze_source`; they
are not additional formal V1 Stages.

The immutable Workflow is `video_grounded.understand@1.0`:

```text
transcribe_source
  output transcript: transcript / one
  approval_required: false

analyze_source
  input transcript: transcript / one
  output understanding: video_understanding / one
  approval_required: true

plan_source
  input understanding: video_understanding / one
  output plan: edit_plan / one
  implementation deferred; used only to prove the Runtime gate
```

The Project binds `primary_video` to the stable source Asset ID. Workflow v1 is
not redesigned to introduce Asset slots.

## Corrected payload direction

### `transcript.v1`

Transcript is a separate Asset-owned Artifact containing source duration,
language, speech/no-speech status, and chronological source-time speech spans.
It does not use persistent utterance IDs as a cross-Artifact contract.

### `video_understanding.v1`

Understanding contains:

```text
source duration
AI global summary
AI Semantic Segment baseline
sparse human review overlay
```

A Semantic Segment is one coherent source idea/action/event/state and may span
multiple shots, frames, and transcript spans. Segments are chronological,
non-overlapping, may have gaps, and use opaque UUIDv4-based IDs.

The earlier `steps` and dense `timeline` lists are not the formal schema. They
may remain legacy/private analysis inputs that are coalesced into semantic
segments.

The review model is:

```text
AI baseline
+ sparse human review overlay
= derived effective Understanding
```

The overlay supports:

```text
global_overrides
global_locks
removed_segment_ids
added_segments
segment_overrides
segment_locks
```

No duplicate effective tree is persisted. Lock entries store confirmed value
snapshots, not booleans.

## General analysis profile

The approved first behavior remains **general video understanding**. In
Workflow `@1.0`, this is a versioned executor analysis profile recorded in
Artifact metadata/private cache identity, not a semantic `topic` field or a
required Project prompt Artifact.

If arbitrary content-changing instructions are added later, they require a
formal versioned input and a new compatible Workflow definition/version. They
must not be silently added to `@1.0`.

## Corrected regeneration behavior

Three operations are required:

1. **Regenerate Analysis** preserves reviewed topology, stable IDs, human
   structural corrections, overrides, and locks while refreshing permitted AI
   observations.
2. **Fresh Analysis** may generate new topology/IDs but creates a candidate and
   leaves existing Active/Approved state unchanged.
3. **Partial regeneration** refreshes selected baseline segments and saves a
   complete immutable revision with all other content/review copied.

Every operation saves a new ArtifactRevision. None mutates a prior revision,
silently selects a new Active revision when one exists, or silently approves.

## Runtime and reuse behavior

Understanding records exact input references:

```text
source     → source Asset ID
transcript → exact transcript Artifact ID
```

Changing Active transcript `T1 → T2` makes an Understanding based on T1 stale
through existing Runtime derivation. Switching `T2 → T1` may restore freshness.

Future planning is runnable only when the current Active Understanding is:

```text
fresh
and
exactly equal to analyze_source's Approved output snapshot
```

Cross-Project reuse is recorded as `TaskAttempt.operation = reuse` and requires
the exact source/transcript/profile/schema context. Approval remains
Project-specific.

## Source and provider notes

The first demonstration source remains:

```text
https://www.bilibili.com/video/BV1Lfau6iEYQ/
```

The intended primary provider remains Qwen through DashScope, with configured
fallbacks allowed. Actual provider/model trace belongs in Artifact metadata.
Secrets, cookies, downloaded paths, and cache paths never belong in Asset,
Artifact payload, Runtime records, tests, or commits.

Anonymous Bilibili acquisition is attempted first. Browser-cookie access still
requires explicit authorization.

## Verification pointer

The exact schemas, edit operations, derivation precedence, regeneration
algorithm, validation invariants, migration phases, acceptance tests, and
non-goals are defined only in:

```text
docs/architecture/VIDEO_UNDERSTANDING_IMPLEMENTATION_GUIDE_V1.md
```
