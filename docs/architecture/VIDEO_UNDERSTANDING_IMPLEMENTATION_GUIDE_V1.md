# Video Understanding v1 — Implementation Guide

> Status: **IMPLEMENTATION CONTRACT / LOCKED UNDERSTANDING BASELINE**
>
> Repository baseline when this guide was written: local branch
> `codex/workflow-artifact-architecture-v1` after the Runtime v1 implementation
> and the earlier Understanding design commit.
>
> Read completely with:
>
> ```text
> docs/architecture/CODEX_ARCHITECTURE_HANDOFF.md
> docs/architecture/ARTIFACT_SCHEMA_V1.md
> docs/architecture/WORKFLOW_ARTIFACT_FOUNDATION_IMPLEMENTED.md
> docs/architecture/PROJECT_STAGE_RUNTIME_IMPLEMENTATION_GUIDE_V1.md
> ```

This document defines the first staged-editor Understanding implementation.
It supersedes the narrower design in
`docs/superpowers/specs/2026-09-25-video-understanding-v1-design.md` where the
two documents differ.

The contracts marked **[LOCKED CONTRACT]** are implementation constraints, not
design prompts. If the repository makes one literally impossible, record the
conflict and use the smallest compatibility-preserving deviation. Do not
silently redesign Workflow, Artifact, or Runtime foundations.

---

## 1. Objective

Implement one real staged-editor vertical slice:

```text
source Asset
    ↓
transcribe_source
    ↓
transcript Artifact

source Asset + exact transcript Artifact
    ↓
analyze_source
    ↓
video_understanding Artifact
    ↓
Understanding Review
    ↓
Working Draft → Save Version
    ↓
Set Active → Approve
    ↓
future planning Stage becomes runnable
```

The result must be:

- resumable through the existing Project / Stage Runtime;
- reusable across Projects when the exact dependency context matches;
- reviewable without overwriting AI observations;
- editable through immutable Artifact revisions;
- stale-aware when the active transcript changes;
- safe beside the existing one-shot Mode 1 / Mode 2 paths.

This slice ends at the planning gate. It does not implement a Planner,
`director_control.v1`, `edit_plan.v1`, or `timeline.v1`.

---

## 2. Repository reality and compatibility target

The repository already provides:

```text
workflow.models
workflow.executors.ExecutorRegistry
workflow.adapters
artifacts.models / drafts / repository
runtime.models / repository / services / bindings / views / execution
agents.video_analyst_agent
```

The current `video_analyst_agent` exposes legacy structures:

```text
VideoUnderstanding
VideoStep
TimelineFrame
analyze_video()
```

Its dense sampled-frame timeline is useful internal evidence/cache for legacy
clip matching. It is not the formal `video_understanding.v1` schema defined
here.

The repository does not currently expose a reusable source-video ASR Artifact
stage. This implementation may add a small transcription capability and
executor adapter. It must not repurpose TTS alignment code as if it were a
source transcript.

### [LOCKED CONTRACT] Additive migration

Preserve all of the following:

```text
scripts/run.py
orchestrator.py
agents/
launcher/
legacy Mode 1
legacy Mode 2
existing output directories
existing video analyst public behavior
```

Do not require legacy callers to create Projects or Artifacts. Do not move or
rewrite existing outputs. The staged and legacy paths may coexist until staged
parity is explicitly accepted.

---

## 3. Locked foundation boundary

### [LOCKED CONTRACT] Layering

```text
Mode
→ Workflow
→ Phase
→ Stage DAG
→ Executor / Strategy
→ Capability
→ FFmpeg / ASR / VLM / files / APIs
```

The Understanding service calls Runtime and Artifact public APIs. It does not
become a second workflow engine.

### [LOCKED CONTRACT] Immutable formal revisions

```text
saved ArtifactRevision
→ mutable Working Draft
→ Save Version
→ new ArtifactRevision
```

No review operation edits bytes belonging to an existing formal revision.

### [LOCKED CONTRACT] State ownership

```text
Family Preferred → ArtifactFamily
Project Active    → ProjectRuntime
Stage Approved    → StageRun
Task execution    → TaskAttempt
```

`transcript.v1` and `video_understanding.v1` payloads must not contain Active,
Approved, freshness, task status, or Project lifecycle state.

### [LOCKED CONTRACT] Separate user actions

```text
Save Version != Set Active != Approve
```

The UI may later offer convenience buttons, but the domain operations remain
separate and independently testable.

---

## 4. Analyze structure

### [LOCKED CONTRACT] Two formal Analyze stages

V1 uses exactly these formal source-analysis stages:

```text
source Asset
    ↓
transcribe_source
    ↓
transcript Artifact

source Asset + transcript Artifact
    ↓
analyze_source
    ↓
video_understanding Artifact
```

Do not fragment V1 into formal stages such as:

```text
analyze_visual
detect_shots
extract_keyframes
semantic_synthesis
summary_generation
```

Frame extraction, shot heuristics, VLM batching, dense observations, source
probing, OCR, and caches remain reusable capabilities behind `analyze_source`.
They may have private cache records but are not formal Workflow outputs in V1.

### Why transcript is formal

Transcript is independently useful, expensive enough to reuse, human
inspectable, and a real semantic dependency of Understanding. Making it a
separate Artifact allows:

- corrected or alternative transcripts;
- exact stale derivation;
- cross-Project reuse;
- source-time joins without copying transcript text;
- later transcript-specific review without changing Understanding identity.

---

## 5. Time and identity conventions

### Source time

All persisted times are seconds from the beginning of the source media.

```text
0.0 == first source instant
```

Ranges use half-open semantics:

```text
[start_sec, end_sec)
```

This removes ambiguity at adjacent boundaries:

```text
[0.0, 4.0)
[4.0, 9.5)
```

Times are JSON numbers, finite, and not booleans. Implementations may use
floats internally but must reject NaN and infinity.

### Stable semantic segment IDs

Formal Understanding segment IDs use:

```text
seg_<UUIDv4>
```

Example:

```text
seg_2d6ce0d4-2d18-4fb1-bc81-a826df031572
```

The UUID must be random/opaque. It must not encode source timestamp, order,
array index, shot number, or transcript span identity. Array order is not an
identity contract.

Transcript spans deliberately have no persistent IDs in V1. Understanding
joins transcript content by source time rather than retaining fragile ASR
utterance identifiers.

---

## 6. Artifact ownership and exact dependencies

### Source Asset seam

The current repository has no formal Source Asset repository. Add the smallest
public local-first seam needed by this vertical slice rather than storing URLs
in Project Runtime.

Exact V1 record:

```json
{
  "schema_version": "source_asset.v1",
  "asset_id": "asset_video_001",
  "asset_kind": "remote_video",
  "source_uri": "https://www.bilibili.com/video/BV1Lfau6iEYQ/",
  "display_name": null,
  "created_at": "2026-09-25T12:00:00-04:00",
  "updated_at": "2026-09-25T12:00:00-04:00"
}
```

Rules:

- unknown fields and schema versions are rejected;
- v1 `asset_kind` is the literal `remote_video`;
- `asset_id` is a stable opaque identifier;
- `source_uri` is canonicalized before identity lookup/save;
- Bilibili canonicalization retains the BV identifier and removes query,
  fragment, tracking, and trailing route noise;
- registering the same canonical URI returns the existing Asset;
- repository writes are atomic and layout is private;
- the public resolver accepts an Asset ID and returns its canonical URI or a
  safe local media handle;
- credentials, cookies, downloaded media paths, frame caches, and provider
  settings are never persisted in the Asset record;
- local upload/content-fingerprint identity is a later Asset-schema extension,
  not an ad-hoc absolute path in Runtime.

The Project and Artifact layers reference only `asset_id`. Acquisition code is
the only layer that needs the URI.

### [LOCKED CONTRACT] Asset-owned families

Both formal outputs are Asset-owned:

```text
asset/<source_asset_id> + transcript + null
asset/<source_asset_id> + video_understanding + null
```

They survive Project deletion and may be reused by another Project.

Prompt, model, executor, Project, and change method do not alter Family
identity. They may affect candidate compatibility and belong in revision
metadata where appropriate.

### Transcript input references

A `transcript` revision records its source:

```json
[
  {"slot": "source", "kind": "asset", "id": "asset_video_001"}
]
```

### Understanding input references

A `video_understanding` revision records both exact inputs:

```json
[
  {"slot": "source", "kind": "asset", "id": "asset_video_001"},
  {"slot": "transcript", "kind": "artifact", "id": "art_T1"}
]
```

Do not embed the source URL, local media path, or transcript body in the
Understanding payload.

### Owner/dependency consistency

Before saving either output:

1. resolve the output Family owner;
2. require `owner.kind == asset`;
3. require `owner.id` equals the exact source Asset input ID;
4. for Understanding, resolve the transcript revision;
5. require the transcript Family is owned by the same source Asset;
6. require the transcript revision has its own exact source dependency;
7. reject deleted or purged inputs.

This prevents a valid-looking Understanding from being saved under the wrong
source Family.

---

## 7. Exact `transcript.v1` payload

### [LOCKED CONTRACT] Shape

Unknown fields are rejected. All fields shown below are required; nullable
fields use explicit `null`.

```json
{
  "schema_version": "transcript.v1",
  "source_duration_sec": 123.4,
  "language": "zh-CN",
  "speech_status": "speech",
  "spans": [
    {
      "start_sec": 1.2,
      "end_sec": 4.8,
      "text": "先把鸭皮和肉分离。",
      "speaker_label": null,
      "confidence": 0.94
    }
  ]
}
```

No-speech result:

```json
{
  "schema_version": "transcript.v1",
  "source_duration_sec": 41.0,
  "language": null,
  "speech_status": "no_speech",
  "spans": []
}
```

### Field contract

| Field | Exact meaning |
|---|---|
| `schema_version` | Literal `transcript.v1`. |
| `source_duration_sec` | Finite number greater than zero. |
| `language` | BCP-47-style language tag string or `null` when unavailable/no speech. |
| `speech_status` | Literal `speech` or `no_speech`. |
| `spans` | Chronological recognized speech spans. |
| `start_sec` | Inclusive source-time start, finite and `>= 0`. |
| `end_sec` | Exclusive source-time end, finite and `> start_sec`. |
| `text` | Non-empty recognized text after trimming. |
| `speaker_label` | Optional non-empty diarization label or `null`; not a person identity. |
| `confidence` | Optional ASR observation in `[0, 1]` or `null`. |

### Transcript invariants

1. `speech_status == speech` requires at least one span.
2. `speech_status == no_speech` requires `spans == []`.
3. Spans are sorted by `(start_sec, end_sec)`.
4. Span overlap is allowed because diarization/ASR may contain simultaneous
   speech; consumers must not assume transcript spans partition source time.
5. Every `end_sec <= source_duration_sec` after media-probe normalization.
6. Empty transcript text is never represented as a successful speech span.
7. Provider/model/language-detection provenance belongs in Artifact metadata,
   not this semantic payload.
8. No source URL/path, Project ID, StageRun ID, prompt, or task status appears
   in the payload.

When non-null, `language` must match:

```text
^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$
```

An empty ASR response is not automatically `no_speech`. The transcription
capability must distinguish a successful no-speech determination from a model,
decode, or transport failure.

---

## 8. Canonical Understanding unit

### [LOCKED CONTRACT] Semantic Segment

The canonical review/edit unit is a **Semantic Segment**:

> one coherent source idea, action, event, or meaningful state over a source
> time range.

A Semantic Segment may contain:

```text
multiple shots
multiple sampled frames
multiple transcript spans
```

It is not a frame, shot, ASR utterance, or final edit clip.

Formal effective segments are:

- chronological;
- non-overlapping;
- allowed to have gaps;
- bounded by source duration;
- identified independently of their order and time.

Understanding answers:

> What exists in the source?

It does not answer:

> What should the final edit do?

---

## 9. Exact `video_understanding.v1` payload

### [LOCKED CONTRACT] Shape

Unknown fields are rejected. All outer fields shown below are required. Sparse
override/lock objects may omit reviewable field keys.

```json
{
  "schema_version": "video_understanding.v1",
  "source_duration_sec": 123.4,
  "ai_baseline": {
    "global_summary": "The source demonstrates the complete preparation process.",
    "segments": [
      {
        "segment_id": "seg_2d6ce0d4-2d18-4fb1-bc81-a826df031572",
        "start_sec": 0.0,
        "end_sec": 18.5,
        "semantic_summary": "The cook separates the skin from the meat.",
        "visual_description": "A hand works between the skin and breast with a narrow tool.",
        "source_salience": 0.86,
        "confidence": 0.91
      }
    ]
  },
  "human_review": {
    "global_overrides": {},
    "global_locks": {},
    "removed_segment_ids": [],
    "added_segments": [],
    "segment_overrides": {},
    "segment_locks": {}
  }
}
```

### Top-level fields

| Field | Exact meaning |
|---|---|
| `schema_version` | Literal `video_understanding.v1`. |
| `source_duration_sec` | Finite source duration greater than zero. |
| `ai_baseline` | AI observations for this immutable revision. |
| `human_review` | Sparse structural edits, value overrides, and confirmed lock snapshots. |

### AI baseline

```json
{
  "global_summary": "...",
  "segments": []
}
```

`global_summary` is a non-empty source-level summary. `segments` contains one or
more baseline Semantic Segments.

### Semantic Segment fields

| Field | Rule |
|---|---|
| `segment_id` | `seg_<UUIDv4>`, globally unique within the payload. |
| `start_sec` | Finite number `>= 0`. |
| `end_sec` | Finite number `> start_sec` and `<= source_duration_sec`. |
| `semantic_summary` | Non-empty statement of the source idea/action/state. |
| `visual_description` | Non-empty literal description of relevant visible evidence. |
| `source_salience` | AI-estimated source-level prominence in `[0,1]` or `null`. |
| `confidence` | AI confidence in the observation in `[0,1]` or `null`. |

`source_salience` is not project-specific importance or a final clip-selection
score. `confidence` is an AI observation, not human truth. Neither field is
human-reviewable in V1.

### Human-added segment

`added_segments` uses the same complete Segment shape, with these additional
rules:

```text
source_salience = null
confidence      = null
```

Human-added segments are structural review evidence, not AI claims. Their IDs
must be newly allocated opaque UUIDv4 IDs and may not collide with baseline or
other added IDs.

### Reviewable fields

Global:

```text
global_summary
```

Per segment:

```text
start_sec
end_sec
semantic_summary
visual_description
```

Only these names may appear in override/lock objects.

### Sparse human review overlay

```json
{
  "global_overrides": {
    "global_summary": "Human-corrected source summary."
  },
  "global_locks": {
    "global_summary": "Human-corrected source summary."
  },
  "removed_segment_ids": [
    "seg_2d6ce0d4-2d18-4fb1-bc81-a826df031572"
  ],
  "added_segments": [
    {
      "segment_id": "seg_9cc18bba-42d8-4fe1-a7db-bfe640f04b65",
      "start_sec": 0.0,
      "end_sec": 9.0,
      "semantic_summary": "The cook begins separating the skin.",
      "visual_description": "A tool enters beneath the breast skin.",
      "source_salience": null,
      "confidence": null
    }
  ],
  "segment_overrides": {
    "seg_9cc18bba-42d8-4fe1-a7db-bfe640f04b65": {
      "semantic_summary": "The cook carefully loosens the skin."
    }
  },
  "segment_locks": {
    "seg_9cc18bba-42d8-4fe1-a7db-bfe640f04b65": {
      "semantic_summary": "The cook carefully loosens the skin."
    }
  }
}
```

The maps are keyed by segment ID. Lock values are confirmed value snapshots,
not booleans.

### [LOCKED CONTRACT] Excluded editorial fields

The payload must not contain:

```text
final clip order
selected final clips
output duration
transition choices
music timing
hook choice
narration script
B-roll choices
timeline tracks
project-specific relevance/importance
Project Active / Stage Approved / stale state
```

These belong to later planning/timeline artifacts or Runtime state.

### [LOCKED CONTRACT] Excluded generic provenance

Do not require payload fields for:

```text
source URL/path
source Asset ID
transcript Artifact ID/body
project_id
stage_run_id
task_id
executor_id
model/provider name
prompt text
cache path
```

Use Artifact owner, `producer`, `input_refs`, and `metadata`.

### Deterministic serialization

Both V1 payloads use UTF-8 JSON with:

```text
sorted object keys
compact separators
allow_nan = false
one trailing newline optional but consistent per serializer
```

Array order remains semantically significant for baseline/transcript
chronology. Map key sorting is only a byte-stability rule. Artifact checksum is
still SHA-256 over the exact persisted bytes, as required by Artifact v1.

---

## 10. AI baseline, review overlay, and effective Understanding

### [LOCKED CONTRACT] No duplicate effective tree

```text
AI baseline
+ sparse human review overlay
= effective Understanding
```

The effective Understanding is derived. Do not persist a second
`effective_segments` or `effective_summary` tree inside the formal payload.
Duplicate effective trees can disagree with locks/overrides and are forbidden.

### Effective global summary

For the key `global_summary`:

```text
if global_locks contains key:
    effective = lock snapshot
else if global_overrides contains key:
    effective = override value
else:
    effective = ai_baseline value
```

If both override and lock exist, their normalized values must be equal.

### Effective segments

Derive as follows:

1. Index baseline segments by `segment_id`.
2. Exclude baseline IDs in `removed_segment_ids`.
3. Add every item in `added_segments`.
4. For each reviewable field of every retained segment:
   - use the lock snapshot when present;
   - otherwise use the override when present;
   - otherwise use the segment's stored value.
5. Preserve `source_salience` and `confidence` from the AI baseline for AI
   segments; keep both null for human-added segments.
6. Sort the derived segments by `(start_sec, end_sec, segment_id)`.
7. Validate chronology, non-overlap, and source-duration bounds.

Lock has highest effective precedence because a later regenerated AI baseline
may differ from the value a human explicitly confirmed.

Overlays/locks may remain attached to a removed baseline segment so that AI
history and a possible restore operation are not silently erased. They have no
effect while that segment remains removed.

---

## 11. Override and lock semantics

### Override

Override means:

> The human intentionally changed this value.

Example:

```text
AI:       boiled
override: blanched
effective: blanched
```

Overrides survive normal and partial regeneration.

### Lock

Lock means:

> Future automatic regeneration must preserve this human-confirmed effective
> value.

Example:

```text
old AI:        duck is air dried
lock snapshot: duck is air dried
new AI:        duck is oven dried
effective:     duck is air dried
```

Lock does not mean:

```text
ArtifactRevision becomes mutable
field becomes permanently read-only
Stage becomes Approved
Artifact becomes Project Active
```

Human users may manually edit a locked field. Saving that edit must:

1. set/update the override to the new value; and
2. update the lock snapshot to the same new effective value.

If override and lock exist for one field, they must agree exactly after normal
type normalization. A contradictory payload is invalid.

UI may expose **Lock Segment** as a convenience operation that locks all four
reviewable segment fields to their current effective values. The formal model
remains field-level.

---

## 12. Working Draft edit operations

The implementation should expose a typed Understanding draft/service rather
than asking GUI code to mutate arbitrary JSON dictionaries.

Minimum operations:

```text
set_global_summary(value)
clear_global_summary_override()
lock_global_summary()
unlock_global_summary()

set_segment_field(segment_id, field, value)
clear_segment_override(segment_id, field)
lock_segment_field(segment_id, field)
unlock_segment_field(segment_id, field)
lock_segment(segment_id)
unlock_segment(segment_id)

add_segment(start_sec, end_sec, semantic_summary, visual_description)
remove_segment(segment_id)
restore_baseline_segment(segment_id)
split_segment(segment_id, split_sec)
merge_segments(segment_ids)

validate()
to_payload()
```

### Edit operation rules

- Editing a baseline field writes a sparse override.
- Editing a locked field also updates its lock snapshot.
- Editing a human-added segment writes an override against that added segment;
  the original addition remains visible in that revision's overlay.
- Removing a baseline segment adds its ID to `removed_segment_ids`; it does not
  erase the baseline entry.
- Removing a human-added segment removes it and its associated maps from the
  new draft; the parent revision still preserves its history.
- Restoring a baseline segment removes its ID from `removed_segment_ids`.
- Every operation validates field names/types immediately where practical.
- Saving validates the complete derived payload.

### Split

For baseline `seg_A`:

```text
remove seg_A
+ add seg_B
+ add seg_C
```

`seg_A` remains in `ai_baseline.segments`. `seg_B` and `seg_C` receive new
opaque IDs. The split point must lie strictly inside the source effective
range.

For a human-added segment, the new draft removes that added entry and creates
two new added entries. Its parent Artifact revision preserves the prior entry.

### Merge

Merging segments:

1. requires two or more effective segments;
2. requires them to be consecutive in effective source-time order;
3. removes baseline inputs through `removed_segment_ids` and removes
   human-added inputs from `added_segments`;
4. creates one new human-added segment with a new opaque ID;
5. uses `[minimum start_sec, maximum end_sec)`;
6. requires an explicit human semantic summary and visual description.

Gaps may be included in a human merge because gaps are allowed in the semantic
model, but the UI must show the resulting range rather than hiding it.

### Save Version

Saving a review draft:

```text
base U1
→ Working Draft
→ validate complete payload
→ ArtifactRepository.save_revision(...)
→ U2 with parent_artifact_id = U1
```

The new revision:

- remains in the same Asset-owned `video_understanding` Family;
- uses `producer.kind = human`;
- retains exact source/transcript `input_refs` from the base unless the user
  explicitly initiated regeneration against new current dependencies;
- records a small `change_kind = human_edit` metadata value;
- does not become Active or Approved automatically.

---

## 13. Validation invariants

Validators must reject unknown fields and aggregate/report useful field paths
where practical.

### Payload structure

1. Schema version is exact.
2. Required fields exist.
3. Unknown fields are rejected.
4. Strings are trimmed and required semantic strings are non-empty.
5. JSON numbers are finite and booleans are rejected as numbers.

### Segment identity

6. Every baseline and added segment ID is a valid `seg_<UUIDv4>`.
7. Baseline IDs are unique.
8. Added IDs are unique.
9. Baseline and added ID sets are disjoint.
10. IDs carry no ordering/source-time meaning.

### Ranges

11. `start_sec >= 0`.
12. `end_sec > start_sec`.
13. `end_sec <= source_duration_sec`.
14. Baseline segments are chronological and non-overlapping.
15. Effective segments are chronological and non-overlapping.
16. Gaps are allowed.

### Overlay references

17. `removed_segment_ids` are unique and reference baseline IDs only.
18. Override/lock map keys reference a baseline or added segment.
19. Override/lock objects contain reviewable fields only.
20. Values match the exact field type and field validation.
21. An override and lock for the same field agree.
22. Added-segment salience/confidence are null.

### Scores

23. `source_salience` is null or in `[0,1]`.
24. `confidence` is null or in `[0,1]`.
25. Human operations cannot set/override/lock confidence.

### Artifact/Runtime integration

26. Transcript and Understanding Families are Asset-owned.
27. Family owner matches the exact source input Asset.
28. Understanding has exactly one current `source` Asset input and one exact
    `transcript` Artifact input for this Workflow.
29. Transcript owner/source dependency matches Understanding source.
30. Understanding `source_duration_sec` equals the exact transcript payload's
    `source_duration_sec`; analysis copies the canonical probed duration rather
    than introducing a second probe value.
31. Input revisions are live, not deleted/purged.
32. Human edits and regenerations create new immutable revisions.
33. New revisions do not replace an existing Active selection.
34. Approval records the exact selected output snapshot in StageRun.
35. No Active/Approved/stale/task state appears in payloads.

---

## 14. Regeneration contracts

There are two intentionally different full-analysis operations plus a partial
variant.

### [LOCKED CONTRACT] Regenerate Analysis

Normal regeneration begins from one reviewed Understanding revision and the
Project's current exact source/transcript bindings.

It must:

```text
preserve reviewed effective topology
preserve stable baseline segment IDs
preserve human-added segment IDs
preserve removed baseline IDs
preserve overrides
preserve lock snapshots
refresh permitted AI baseline fields
save a complete new immutable ArtifactRevision
```

V1 deliberately avoids a semantic merge engine. The concrete algorithm is:

1. Load and validate the selected base Understanding.
2. Derive its effective topology and source-time ranges.
3. Keep the original baseline segment array and IDs.
4. Keep all structural review overlay entries.
5. For each unremoved baseline segment, analyze the current source/transcript
   over its current effective time range.
6. Refresh AI baseline `semantic_summary`, `visual_description`,
   `source_salience`, and `confidence` when requested.
7. Preserve baseline time values; existing time overrides/locks continue to
   define effective ranges.
8. Keep removed baseline observations as lineage/history rather than spending
   model cost refreshing invisible content.
9. Keep human-added segment content unchanged in V1; its topology and text are
   human review facts, not AI baseline fields.
10. Refresh AI baseline global summary, then apply existing global
    override/lock during effective derivation.
11. Save the result with `parent_artifact_id` set to the selected base and
    current exact source/transcript `input_refs`.

Lock and override precedence guarantees reviewed values survive refreshed AI
observations.

Normal regeneration does not:

```text
mutate the base revision
invent a new topology
silently Set Active when Active is already populated
silently Approve
```

### [LOCKED CONTRACT] Fresh Analysis

Fresh Analysis ignores prior topology for generation and may create entirely
new baseline segmentation and new segment IDs.

It must:

- produce an empty review overlay;
- use new opaque IDs;
- save a complete candidate revision;
- leave the reviewed prior revision untouched;
- leave Project Active unchanged when already populated;
- leave Stage approval unchanged;
- avoid mapping old locks across unrelated topology.

It may use the selected prior revision as `parent_artifact_id` to show user
lineage, but that does not transfer its review overlay.

### [LOCKED CONTRACT] Partial regeneration

Partial regeneration accepts a set of baseline segment IDs, for example:

```text
{seg_A, seg_C}
```

It produces one complete new revision:

```text
A refreshed
B copied
C refreshed
D copied
human review overlay preserved
```

Rules:

- requested IDs must identify effective, unremoved baseline segments;
- duplicate/unknown/removed/human-added IDs are rejected in V1;
- non-requested baseline entries are copied byte-for-semantic-value;
- human-added entries and all overlays/locks are copied;
- the complete derived output is validated before save;
- a mutable patch is never stored as the formal Artifact content;
- Active and Approved remain unchanged unless separately changed.

---

## 15. Workflow definition

### [LOCKED CONTRACT] `video_grounded.understand@1.0`

The staged slice uses one immutable Workflow definition:

```python
WorkflowDefinition(
    workflow_id="video_grounded.understand",
    workflow_version="1.0",
    mode="video_grounded",
    stages=(
        StageDefinition(
            stage_id="transcribe_source",
            phase="analyze",
            executor="video_grounded.transcribe_source",
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
            executor="video_grounded.analyze_source",
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
            executor="video_grounded.plan_source",
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
```

`plan_source` exists only to prove the Runtime gate. Its executor and payload
are not implemented in this slice and must not be invoked. This definition
does not finalize `edit_plan.v1`.

### Source binding

Workflow v1 has Artifact slots but no first-class Asset slots. Preserve that
model. The Project binds:

```text
source_bindings.primary_video = asset_video_001
```

Projects pinned to Workflow `@1.0` must bind exactly this one source role.
Additional source roles require a later Workflow version so conservative
Runtime dependency recording cannot silently change existing freshness.

The Runtime's conservative binding resolver supplies the source role to each
Stage execution context and records it in Artifact `input_refs` where it is a
true dependency.

### General analysis profile

The user-selected V1 behavior is `general video understanding`. Treat it as a
versioned executor analysis profile, for example:

```text
analysis_profile = general_video_understanding.v1
```

Record the profile and actual provider/model trace in Artifact metadata and
private cache identity. Do not put a user-facing topic or model name in the
semantic payload. If a future product allows arbitrary content-changing
instructions, add a formal versioned Artifact input in a later Workflow
version rather than silently changing `@1.0`.

---

## 16. Runtime binding, freshness, and review

### Exact Stage inputs

Expected bindings are:

```text
transcribe_source:
  primary_video → RuntimeRef(asset, asset_video_001)

analyze_source:
  primary_video → RuntimeRef(asset, asset_video_001)
  transcript    → RuntimeRef(artifact, art_T1)

plan_source:
  primary_video → RuntimeRef(asset, asset_video_001)
  understanding → RuntimeRef(artifact, art_U1)
```

TaskAttempt records the same effective inputs. Formal outputs record matching
Artifact `input_refs`.

### Initial successful outputs

Existing Runtime behavior applies:

```text
Active slot empty
→ first successful output may become Active
```

For Understanding:

```text
first U1 succeeds
→ U1 may become Active
→ Approved remains null
→ review = needs_review
```

Understanding is review-required and must never be auto-approved.

### Transcript change and reversible freshness

```text
U2 input_refs transcript = T1
Project Active transcript T1 → T2
→ expected inputs no longer match U2
→ U2 is stale
```

Switching back:

```text
T2 → T1
→ exact dependencies match again
→ U2 may be fresh again
```

Use the existing `StageStateResolver` and Artifact `input_refs`. Do not add an
Understanding-specific stale boolean.

### Planning gate

`plan_source` is runnable only when all existing Runtime conditions hold,
including:

```text
Active Understanding exists
Active Understanding is fresh
analyze_source approval snapshot exactly equals Active outputs
no active task blocks execution
Project lifecycle is active
```

Examples:

```text
Active U2 + Approved U2 + U2 depends current source/T1
→ gate satisfied

Active U2 + Approved U1
→ needs_review
→ gate blocked

Active U2 + Approved U2 + U2 depends T1 + current transcript T2
→ stale
→ gate blocked
```

---

## 17. Save / Active / Approve examples

### Human edit

```text
U1 Active
Approved null

edit U1 Working Draft
Save Version → U2

Active remains U1
Approved remains null
```

### Select and approve

```text
Set Active U2
→ Active U2
→ Approved null/old
→ needs_review

Approve analyze_source
→ Approved outputs = {understanding: U2}
→ review = approved
```

### Save after approval

```text
Active U2
Approved U2

Save Version → U3

Active remains U2
Approved remains U2
```

### Fresh candidate safety

```text
U2 Active + Approved
Fresh Analysis → U3

U3 is candidate only
Active remains U2
Approved remains U2
```

---

## 18. Reuse contract

Reuse is a Runtime TaskAttempt:

```text
operation = reuse
```

### Transcript reuse match

Candidate selection must match:

```text
Artifact type = transcript
Family owner = exact source Asset
input_refs = exact source Asset binding
payload schema = transcript.v1
compatible transcription profile/schema metadata
revision live
```

### Understanding reuse match

Candidate selection must match:

```text
Artifact type = video_understanding
Family owner = exact source Asset
input_refs = exact source Asset + exact transcript Artifact
payload schema = video_understanding.v1
compatible analysis profile/schema metadata
revision live
```

Do not reuse merely because a revision is latest, newest, or Family Preferred.

After candidate selection, call existing Runtime reuse APIs so a TaskAttempt
records why execution did not rerun. Do not clone the Artifact into a
Project-owned Family.

Project B receives its own Active selection and its own Stage approval. Project
A's approval does not transfer.

---

## 19. Executor and capability boundary

### Transcription executor

Logical ID:

```text
video_grounded.transcribe_source
```

It receives only `RuntimeExecutionContext`, resolves the exact Asset ID through
an injected public Asset resolver, invokes a source-transcription capability,
validates `transcript.v1`, and returns an `ArtifactOutputSpec` for the
Asset-owned transcript Family.

### Analysis executor

Logical ID:

```text
video_grounded.analyze_source
```

It receives only `RuntimeExecutionContext`, resolves:

```text
primary_video Asset ID → source URI/media handle
transcript Artifact ID → validated transcript.v1
```

It may reuse existing video analyst capabilities for download, probing, frame
sampling, and VLM calls. It then adapts output into semantic segments and the
exact `video_understanding.v1` payload.

The currently implemented `adapt_video_analysis_executor` expects a source and
topic Artifact. Preserve that helper for its existing callers/tests. Add a
transcript-grounded adapter for the `video_grounded.understand@1.0`
composition and register it under the same logical executor ID in that
Workflow's application registry. Executor IDs identify logical behavior at a
pinned Workflow boundary; they do not require every Workflow version to share
one Python callable signature.

### [LOCKED CONTRACT] Legacy adapter is not the payload schema

Do not serialize the legacy dataclass directly. In particular:

- discard `url` and `video_path` from formal payload content;
- do not persist `topic` as semantic source truth;
- do not expose dense `TimelineFrame` entries as canonical segments;
- map/coalesce useful `VideoStep` and frame observations into non-overlapping
  semantic ranges;
- incorporate transcript text by overlapping source time;
- produce new opaque semantic segment IDs;
- validate the final payload before Artifact save.

The dense legacy timeline may remain a private capability cache and continue
serving legacy clip matching.

### Provider and cache provenance

Artifact metadata may record JSON-safe values such as:

```text
change_kind
analysis_profile
analysis_schema_revision
transcription_profile
provider/model trace
sample interval
prompt template version
```

Secrets, cookies, absolute local paths, raw credentials, and entire prompts are
forbidden.

Cache identity must include every content-affecting execution dimension, at
least:

```text
source Asset/content fingerprint
exact transcript Artifact ID for Understanding
analysis/transcription profile version
prompt template version
declared provider/model policy
sampling profile
payload schema version
```

Cache hits are capability optimizations. Formal reuse remains a Runtime
operation over exact Artifact IDs.

---

## 20. Failure behavior

The staged path fails closed.

### Transcription failures

Examples:

```text
source cannot be resolved/downloaded
media duration invalid
audio decode fails
ASR provider unavailable
malformed ASR result
ambiguous empty result
payload validation fails
```

Result:

```text
TaskAttempt = failed
no transcript Artifact created
no Active pointer moved
```

### Analysis failures

Examples:

```text
source/transcript owner mismatch
deleted transcript
zero usable frames/evidence
all configured providers fail
malformed model output
invalid/overlapping semantic segments
payload validation fails
```

Result:

```text
TaskAttempt = failed
no Understanding Artifact created
no Active pointer moved
no approval changed
```

### Partial-save caution

Construct and validate the complete payload before calling Artifact save. One
Stage output in this slice has cardinality one, which avoids a multi-output
partial-save problem.

### Browser cookies and secrets

Anonymous acquisition is attempted first. Access to browser cookies requires
explicit user authorization. Credentials/cookies are never copied into Asset,
Artifact, Runtime, logs, tests, or commits.

---

## 21. Migration-safe module responsibilities

Exact filenames may follow repository conventions, but keep responsibilities
separate:

```text
understanding/
├── transcript models + validation + serialization
├── understanding models + validation + serialization
├── effective-view derivation
├── review Working Draft/edit operations
├── regeneration service
├── legacy analysis adapter
├── Workflow factory/catalog registration
└── staged application service

source_assets/ (or existing public Asset abstraction)
├── stable Asset records/repository
├── canonical source identity
└── injected URI/media resolver
```

Rules:

- Higher layers use `ArtifactRepository`, never private Artifact directories.
- Higher layers use Runtime services, never private Runtime records.
- `artifacts` must not import `runtime` or `understanding`.
- GUI code eventually calls the Understanding service; it does not sequence
  ASR/VLM/Artifact/Runtime operations itself.
- Payload model code contains no provider or filesystem logic.
- Capability code contains no Active/Approved state transitions.

---

## 22. Implementation phases

### U1 — Payload models

Implement and test:

```text
TranscriptV1 / TranscriptSpan
VideoUnderstandingV1
AIBaseline
SemanticSegment
HumanReviewOverlay
strict serialization/deserialization
schema-version and unknown-field validation
```

### U2 — Effective derivation and review drafts

Implement:

```text
effective global summary
effective semantic segments
override/lock precedence
typed Working Draft operations
add/remove/split/merge
complete validation before save
```

### U3 — Workflow and source binding

Register `video_grounded.understand@1.0`, preserve Runtime's source-role seam,
and provide stable Asset lookup/resolution without putting URLs in Runtime.

### U4 — Transcription vertical stage

Adapt or add a source ASR capability, validate a complete `transcript.v1`, save
it through Artifact APIs, and prove initial Active behavior and reuse.

### U5 — Analysis adapter

Adapt the legacy visual analysis path, join transcript by source time, build
semantic segments, save exact source/transcript dependencies, and preserve the
legacy caller path.

### U6 — Review / immutable save

Load U1, edit through a Working Draft, save U2, and prove Active/Approved do not
move implicitly.

### U7 — Regeneration

Implement normal, fresh, and partial regeneration exactly as specified. Each
operation creates a full immutable revision.

### U8 — Runtime gate and cross-Project reuse

Prove stale/reversible freshness, exact approval gating, and reuse attempts for
both transcript and Understanding.

### U9 — Compatibility verification

Run focused Understanding tests, Workflow/Artifact/Runtime tests, and existing
legacy smoke tests. Do not add the Planner/UI while fixing unrelated failures.

---

## 23. Required acceptance tests

### A. Initial run

```text
S1 → T1 → U1

Active T1 because transcript slot empty
Active U1 because understanding slot empty
Approved Understanding null
Analyze review = needs_review
Planning blocked
```

### B. Human edit

```text
U1 AI says "boiled"
human override says "blanched"
Save Version → U2

Active remains U1
Approved remains null/old
U1 bytes unchanged
```

### C. Set Active / Approve separation

```text
Set Active U2
→ Active U2
→ Approved old/null
→ needs_review

Approve
→ Approved U2
→ planning gate runnable when fresh
```

### D. Save newer revision after approval

```text
Active U2
Approved U2

Save U3
→ Active U2
→ Approved U2
```

### E. Transcript change makes stale

```text
U2 depends T1
Active transcript T1 → T2
→ U2 stale
→ planning blocked
```

### F. Reversible freshness

```text
Active transcript T2 → T1
→ U2 fresh again
→ approval U2 still exact
→ planning runnable again
```

### G. Lock survives regeneration

```text
AI X
lock snapshot X
regenerated AI Y
effective X
```

### H. Override survives regeneration

```text
AI X
override Z
regenerated AI Y
effective Z
```

Also test override+lock disagreement is rejected.

### I. Human split

```text
baseline seg_A

remove seg_A
add seg_B
add seg_C

effective = B + C
same revision baseline retains A
parent lineage remains queryable
```

### J. Fresh Analysis safety

```text
reviewed U2 Active + Approved
fresh analysis creates U3 with new topology/IDs

Active remains U2
Approved remains U2
U2 review unchanged
```

### K. Partial regeneration

```text
baseline A/B/C/D
regenerate A+C

new complete revision
A/C refreshed
B/D copied
overlay/locks preserved
Active unchanged
```

### L. Cross-Project reuse

```text
Project A produces T1 + U2
Project B binds same source
Project B reuses exact T1 then exact U2

reuse TaskAttempts recorded
no duplicate Artifact revisions
Project B approval remains null until explicit approval
```

Test a near miss: U2 built from T1 may not be reused when Project B's active
transcript is T2.

### M. Transcript payload

Test speech and no-speech payloads, source-time bounds, overlap allowance,
strict unknown fields, confidence limits, and invalid empty speech output.

### N. Segment invariants

Test duplicate/non-UUID IDs, invalid bounds, overlap, allowed gaps, removed-ID
references, added-ID collisions, invalid overlay fields, and human attempts to
override confidence.

### O. Owner/dependency integrity

Attempt to save an Understanding in Asset A's Family using Asset B's
transcript. Saving must fail before a formal revision or Active move.

### P. Initial output failure safety

Force transcription, analysis, and validation failures. Each attempt must be
terminal failed with no formal output and no pointer change.

### Q. Legacy safety

Existing launcher, `scripts/run.py`, Mode 1/Mode 2, Workflow, Artifact, and
Runtime tests continue to pass. Legacy `VideoUnderstanding` callers receive the
same public dataclass behavior.

---

## 24. Important anti-patterns

Do not implement:

### Payload state leakage

```text
payload.active = true
payload.approved = true
payload.stale = true
payload.project_id = ...
```

### AI history destruction

```text
human edit overwrites ai_baseline
split deletes baseline seg_A
regeneration mutates U1 in place
```

### Duplicate effective state

```text
ai_baseline + human_review + persisted effective_segments
```

### Fragile identity

```text
segment_id = "segment_03"
segment_id = "at_12.5_seconds"
understanding references transcript array index 14
```

### False reuse

```text
pick latest transcript
pick Family Preferred Understanding
reuse same-source U2 despite different transcript input
```

### Editorial contamination

```text
segment.selected_for_final = true
segment.output_order = 3
segment.transition = "crossfade"
```

### Formalizing every internal cache

```text
one Workflow Stage per VLM batch/frame/shot heuristic
```

### GUI orchestration

```text
button handler runs ASR → VLM → writes JSON → changes pointers directly
```

---

## 25. Explicit non-goals

Do not implement or finalize in this slice:

```text
director_control.v1
edit_plan.v1
timeline.v1
Planner / Director behavior
advanced NLE
beat editor
Rhythm Cut
Match Cut
final clip selection
narration generation
music/transition decisions
distributed scheduler
collaborative semantic merge engine
cloud backend migration
full Project Workspace UI
```

Do not expand scope because the schemas could support these later.

---

## 26. Locked invariant summary

Treat these as implementation invariants:

1. Analyze has formal `transcribe_source` and `analyze_source` stages.
2. Transcript and Understanding are separate Asset-owned Artifact Families.
3. Understanding depends on the exact transcript revision through
   `ArtifactRevision.input_refs`.
4. Transcript content is not duplicated inside Understanding.
5. Source time is the V1 semantic join.
6. Semantic Segment is the canonical Understanding review/edit unit.
7. Segment IDs are opaque UUIDv4 identities independent of time/order.
8. Effective Understanding is derived from AI baseline plus sparse review.
9. AI baseline is not overwritten by human review.
10. Structural review preserves baseline history.
11. Locks are field-level confirmed value snapshots.
12. Overrides and locks are distinct; when both exist they agree.
13. Confidence remains an AI observation.
14. Formal revisions are complete immutable snapshots.
15. Human edits and every regeneration create a new revision.
16. Normal regeneration preserves reviewed topology/IDs/overlay.
17. Fresh Analysis may create new topology but cannot replace Active or
    Approved implicitly.
18. Partial regeneration saves a complete revision, never a formal patch.
19. Save Version, Set Active, and Approve are separate.
20. First successful Understanding may become Active only when the slot is
    empty and is never auto-approved.
21. Planning requires the exact Active Understanding to be approved and fresh.
22. Freshness is derived from exact Runtime bindings and Artifact dependencies.
23. Switching transcript back may restore freshness.
24. Reuse is exact-context and recorded as a reuse TaskAttempt.
25. Project deletion does not delete reusable source analysis.
26. Legacy Mode 1/Mode 2 behavior remains available.

---

## 27. Implementation handoff and stopping point

Before coding:

1. read this guide fully;
2. inspect current Workflow, Artifact, Runtime, analyzer, and tests;
3. confirm public seams have not materially changed;
4. implement U1 through U9 incrementally;
5. commit this guide separately before implementation;
6. use deterministic injected capabilities for automated tests;
7. run a live Bilibili/Qwen smoke test only after local tests pass and explicit
   credential/cookie boundaries are satisfied.

At completion, report:

```text
guide commit
implementation commits
modules/files changed
tests and live-smoke result
legacy behavior preserved
unavoidable deviations, if any
what remains before director_control.v1 and edit_plan.v1
```

Then stop. Do not continue into Planner/EditPlan implementation without an
explicit new request.
