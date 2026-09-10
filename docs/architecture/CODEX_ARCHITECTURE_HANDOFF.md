# China Video Bot — Architecture Handoff for Codex

> This document explains the product goal, why the architecture is changing, what is already locked, and which interfaces future runtime work must preserve. It is intentionally focused on the big picture and contracts rather than every internal Artifact implementation detail.

## 1. Product direction

`china_video_bot` is evolving from a mostly one-shot AI video-generation pipeline into a staged, inspectable, editable AI video editor/workflow system.

The intent is **not** to rewrite the repository from zero. Existing agents, FFmpeg code, Qwen/VLM calls, TTS, media acquisition, QA, rendering and publishing code should be reused through adapters/services/executors where practical.

The target system should support multiple clearly different editing goals without creating several duplicated pipelines.

Current product modes:

```text
topic_generation
video_grounded
rough_cut
rhythm_cut
```

### Topic Generation

```text
topic / prompt
→ creative understanding
→ director planning
→ stock / generated media
→ voice / subtitles
→ timeline
→ render
```

Creative invention and restructuring are allowed.

### Video Grounded

```text
source video
→ source understanding
→ human review
→ grounded director
→ source clip matching
→ timeline
→ render
```

The output must remain grounded in source material.

### Rough Cut

```text
long source video
→ analyze / ASR / vision
→ highlight selection
→ preserve original meaning
→ select source ranges
→ timeline
→ render
```

This is closer to real editing than regeneration and should normally favor original footage/audio.

### Rhythm Cut

```text
media + music
→ beat / motion / visual analysis
→ rhythm planning
→ clip matching
→ beat-aligned timeline
→ render
```

Future capabilities may include beat/downbeat/onset analysis, motion features, optical flow, pose, visual embeddings and composition/match-cut features.

---

## 2. Why the architecture is changing

The repository currently behaves roughly like:

```text
GUI form
  ↓
scripts/run.py
  ↓
mode-specific orchestration
  ↓
agents / FFmpeg / APIs
```

This is workable for one-shot generation, but becomes fragile when the product needs:

- multiple editing goals;
- pause / review / edit / approve;
- re-run only one stage;
- switch to an older analysis or plan revision;
- resume a project later;
- reuse source analysis in another project;
- detect stale downstream results;
- avoid unnecessary model/API recomputation;
- keep the GUI from becoming another orchestrator;
- eventually support conditional and parallel execution.

Target architecture:

```text
GUI / CLI
  ↓
Project / Workflow API
  ↓
Workflow Engine
  ↓
Stage DAG
  ↓
Executor / Strategy
  ↓
Reusable Capabilities
  ↓
FFmpeg / AI models / files / APIs
```

This should be introduced incrementally, preserving current behavior while moving responsibilities into explicit layers.

---

## 3. Current repository baseline that matters

The current local Gradio launcher is a safe CLI wrapper rather than a project editor.

Conceptually it is:

```text
Form
→ LaunchRequest
→ build_command
→ scripts/run.py
→ one subprocess
→ logs + final files
```

The current launcher exposes topic generation and reference-video generation and forces dry-run behavior.

`scripts/run.py` and `orchestrator.py` already contain working logic that should be adapted rather than discarded.

The current Mode 2 / video-grounded path roughly does:

```text
analyze source video
→ create brief using video understanding
→ save brief / metadata / video_understanding
→ optional review stop
→ TTS/subtitles
→ extract source clips for brief
→ stock fallback
→ assemble
→ QA / publish
```

### Important existing correctness issue

The review path may stop after producing `video_understanding.json` and `brief.json`, then resume later through a generic `--from-brief` path. The generic continuation path may no longer carry the same source-video grounding context and clip-extraction semantics as uninterrupted Mode 2.

The staged architecture must prevent this class of bug:

> Resuming a paused job must continue the same Project, Workflow version, Stage dependency graph and exact approved Artifact inputs. It must never silently switch pipeline semantics.

---

## 4. Locked concept hierarchy

```text
Mode
→ Workflow
→ Phase
→ Stage DAG
→ Executor / Strategy
→ Capability
→ execution layer
```

### Mode

Mode is product-level user intent.

Examples:

```text
rough_cut
video_grounded
```

It answers: **what result does the user want?**

### Workflow

Workflow is the actual executable definition.

Initial defaults:

```text
topic_generation → topic_generation.default
video_grounded   → video_grounded.default
rough_cut        → rough_cut.default
rhythm_cut       → rhythm_cut.default
```

A Mode may later have multiple workflow presets, e.g.:

```text
rough_cut.default
rough_cut.interview
rough_cut.vlog
```

A Project must pin:

```text
workflow_id
workflow_version
```

so old Projects do not silently change behavior when Workflow definitions evolve.

### Phase

Phase is a coarse grouping for the GUI and human mental model.

Current logical phases:

```text
input
analyze
plan
timeline
preview
qa_export
```

Phase is not the execution unit.

### Stage

Stage is the actual executable node in a Workflow DAG.

Examples:

```text
source_input
analyze_source
transcribe_source
analyze_music
plan_highlights
grounded_director
resolve_media
build_timeline
render_preview
run_qa
render_final
```

One Phase may contain multiple Stages.

Example:

```text
ANALYZE
 ├── analyze_music
 ├── analyze_visual
 └── analyze_motion
```

---

## 5. Workflow Engine contract

Workflow Engine is orchestration infrastructure, not an AI model.

It should eventually be able to:

- load a Workflow Definition;
- resolve Stage dependencies;
- determine runnable Stages;
- execute a Stage through a logical executor;
- persist Project/Stage runtime state;
- persist produced Artifacts;
- pause at human review points;
- continue after approval;
- resume the same Project later;
- preserve the same Workflow semantics on resume;
- mark downstream Stages stale when upstream input bindings change;
- support retry / cancellation / conditional / parallel execution later.

The engine should **not** contain implementation details for ASR, video understanding, model calls, media search, TTS or FFmpeg rendering.

---

## 6. Workflow is a DAG

Workflow must not be modeled as one fixed linear loop.

Stages express dependencies by logical IDs such as `depends_on`.

Example:

```text
             ┌─ analyze_music
INPUT ───────┤
             ├─ analyze_visual
             └─ analyze_motion
                     │
                     ▼
                rhythm_plan
                     │
                     ▼
                 timeline
```

The first implementation may execute conservatively/sequentially while preserving DAG semantics in the model. Do not choose data structures that prevent later parallel execution.

---

## 7. Executor / Strategy / Capability boundary

A Stage references a logical executor ID, e.g.:

```json
{
  "stage_id": "plan_highlights",
  "executor": "rough_cut.plan_highlights"
}
```

Workflow definitions must not directly bind to:

- Qwen/model names;
- Python file paths;
- agent class locations.

Resolve executors through a registry/adaptor layer.

Executors then compose reusable capabilities such as:

```text
scene detection
ASR
OCR
keyframe extraction
vision analysis
embeddings
media search
TTS
render
QA
```

Prefer adapting existing repository code rather than duplicating it inside each Workflow.

---

## 8. Human review model

Human review is part of a Stage lifecycle rather than a separate DAG node.

Typical Stage state:

```text
not_started
→ running
→ needs_review
→ approved
```

Additional runtime states may include:

```text
stale
failed
cancelled
```

Important semantic rule:

```text
Save ≠ Approve
```

A user may:

```text
run analyze
→ inspect result
→ edit/save a new revision
→ choose an older/newer revision
→ approve one exact revision
→ continue
```

---

## 9. GUI boundary

GUI is a workflow-driven control surface, not the workflow brain.

Correct dependency:

```text
GUI
→ Project / Workflow API
→ Workflow Engine
```

Avoid event handlers that directly orchestrate model + FFmpeg + media agents.

The Gradio launcher should evolve from a form/CLI wrapper into a Project Workspace:

```text
New Project
→ choose Mode / Workflow
→ Project Workspace
→ Stage navigation
→ Artifact review/version selection
→ Preview / Timeline / QA / Export
```

Workflow Stages may declare reusable UI component types such as:

```text
source_input
understanding_editor
edit_plan_editor
timeline_editor
preview_player
qa_export
```

A new Workflow using existing components should not require rewriting the GUI shell. A genuinely new interaction, such as a beat editor, may add one new reusable component.

Do not build a free-form ComfyUI-style canvas in the first refactor. The backend may be DAG-based while the user-facing GUI exposes stable workflow presets.

---

## 10. Why Project must be a first-class object

Project is **not merely an output directory**.

Project is the persistent context that answers:

```text
What is the user trying to do?
Which Workflow/version defines this job?
Which source assets belong to this job?
Which Artifact revisions is this job currently using?
Which Stages are complete / under review / stale / failed?
Which exact inputs produced existing downstream results?
Can the job safely continue without recomputing?
```

A Project should make the workflow resumable and reproducible.

Conceptually Project will eventually own/reference:

```text
Project
├── identity / metadata
├── pinned workflow
├── user/project settings
├── source asset references
├── active artifact bindings
├── stage runtime references/state
├── variant/branch context if needed
└── project lifecycle metadata
```

A Project must not embed every large analysis / plan / timeline payload directly. Those are Artifacts referenced by ID.

**Project Runtime Schema has not yet been designed. Do not invent a final Project schema during the current Artifact-foundation implementation.**

---

## 11. Artifact interface the rest of the system depends on

Artifact is a persisted result worth reusing, reviewing, versioning, caching, comparing, debugging, or consuming downstream.

Examples:

```text
video_understanding
transcript
scene_detection
ocr
visual_features
motion_features
edit_plan
timeline
preview_video
qa_report
final_video
```

Temporary internal values are not automatically Artifacts.

### Artifact Family

Artifact Family is a version collection for one semantic artifact under one owner/scope.

For reusable source analysis, a typical Family is:

```text
asset_video_001 / video_understanding
```

with:

```text
v001
v002
v003
```

Source-level Families are reusable across Projects and Workflows.

Different semantic artifact types use different Families:

```text
asset_video_001
├── video_understanding
├── transcript
├── scene_detection
├── visual_features
└── motion_features
```

Different prompts or AI/Human regeneration do not automatically create a new Family if the payload still represents the same Artifact Type.

### Revision and Artifact ID

Every formal saved Revision has a unique `artifact_id`.

Example:

```text
v001 → art_A
v002 → art_B
v003 → art_C
```

Revision is family-local save order. Artifact ID identifies the exact saved Revision.

Revision numbers are monotonic and never reused after deletion.

### Working Draft

Editing must not create a revision on every keystroke:

```text
saved revision
→ mutable Working Draft
→ Save Version
→ new formal Revision
```

Formal saved Revision payloads are immutable through normal APIs so other Projects can safely reference them.

---

## 12. Preferred / Active / Approved are different

These are intentionally separated:

```text
Family Preferred → Artifact Family
Project Active    → Project Runtime
Stage Approved    → Stage Runtime
```

### Family Preferred

Default reusable recommendation for a Family.

### Project Active

The exact Revision currently selected for one Project.

Different Projects may select different Revisions from the same Family.

Creating a new Revision must not silently move existing Projects to it.

### Stage Approved Output

The exact Revision approved for one Stage in one Project.

A Stage may approve an older Revision even when newer ones exist.

This separation is important for reproducibility and human-controlled editing.

---

## 13. Cross-Project reuse

Source-level analysis should survive Project deletion and be reusable by future Projects.

Conceptually:

```text
assets/
└── asset_video_001/
    └── reusable artifact families

projects/
└── proj_001/
    └── runtime + references
```

Example:

```text
asset_video_001
        │
        ▼
video_understanding Family
        │
   v001 v002 v003
        │
   ┌────┴───────────┐
   ▼                ▼
Rough Cut Project   Video Grounded Project
```

This is important both for UX and to avoid repeated AI/model cost.

---

## 14. Artifact storage contract required by higher layers

The exact implementation is documented separately, but higher layers may rely on these contracts:

- Artifact Family identity includes owner, artifact type and optional variant.
- Source-level owner is an Asset; project-specific plans/timelines may be Project-owned.
- every formal Revision has a unique `artifact_id`;
- formal Revision content is not silently overwritten;
- Project/Stage runtime references exact Artifact IDs;
- large media is referenced, not copied for every Revision;
- content has SHA-256 identity/checksum;
- deleted Revisions go through Trash/soft-delete before purge;
- physical blobs are reclaimed only when unreachable;
- Project Runtime must not depend on raw artifact file paths.

### Workflow I/O contract

Workflow definition declares semantic Slot + Artifact Type + cardinality.

Example:

```json
{
  "inputs": {
    "understanding": {
      "type": "video_understanding",
      "cardinality": "one"
    }
  }
}
```

Runtime binds the Slot to exact Artifact IDs:

```json
{
  "input_bindings": {
    "understanding": "art_8b31fa"
  }
}
```

Supported cardinalities:

```text
one
optional
many
```

One Stage may produce multiple formal Artifacts.

---

## 15. Stale-result principle

Downstream results must remember the exact Artifact IDs used as inputs.

Example:

```text
Understanding art_U3
        ↓
Plan art_P4
        ↓
Timeline art_T2
```

If Project Active Understanding changes:

```text
art_U3 → art_U7
```

then Plan/Timeline/Preview results that were created from U3 are stale for the current Project state.

The Workflow Engine should eventually compare expected/current bindings against the exact recorded input bindings of existing results.

If the user later switches back to the exact old input Artifact IDs, the engine may be able to recognize an old result as fresh again rather than recomputing.

This is a central reason Runtime must store exact bindings instead of vague booleans.

---

## 16. Artifact Version Manager UX contract

The GUI should expose reusable version management so the user does not manually open JSON/media files in Finder.

Minimum intended operations:

```text
Preview
Compare
Set Active       (Project context)
Set Preferred    (Family context)
Approve for Stage
Delete to Trash
Restore
Purge when safe
Show Dependencies
Show Producer / Provenance
```

Type-specific previewers may include:

```text
video_understanding → segment/understanding viewer
transcript          → transcript viewer
edit_plan           → scene cards
timeline            → timeline UI
video               → video player
image               → image viewer
audio               → audio player
```

---

## 17. Implementation philosophy

Prefer **deterministic engineering and caching** wherever possible to reduce token/API cost.

Examples:

```text
scene detection
ASR
OCR
visual embeddings
keyframe extraction
beat analysis
checksum/content cache
```

Use VLM/LLM calls where semantic reasoning is actually required rather than for every low-level step.

Keep source fingerprints/caches reusable across Projects.

---

## 18. Incremental migration strategy

Do not migrate every mode at once.

Recommended sequence:

```text
1. Introduce schema/model packages while preserving current behavior.
2. Introduce WorkflowDefinition / StageDefinition / Executor Registry.
3. Introduce Asset + Artifact repository abstractions.
4. Wrap existing Mode 2 source analysis as the first staged executor.
5. DESIGN Project Runtime + Stage Runtime before implementing their final persistence shape.
6. Add Project Workspace GUI and stage status.
7. Expose Understanding versions in GUI.
8. Add Save / Approve / Select Version.
9. Replace generic Mode 2 review-resume with Project/Stage continuation.
10. Migrate Topic Generation into the workflow shell.
11. Implement Rough Cut.
12. Implement Rhythm Cut.
```

The current implementation task on this branch covers only the already-locked foundation. Runtime schema is intentionally deferred to the next design round.

---

## 19. Locked vs not yet designed

### Locked enough to implement now

```text
Mode / Workflow separation
WorkflowDefinition concept
Phase vs Stage
DAG dependency model
logical Executor Registry
GUI boundary
Artifact Family / Revision contract
Artifact repository abstraction
Workflow Slot + Type + cardinality I/O model
Working Draft → formal Revision behavior
cross-Project source analysis reuse
Preferred / Active / Approved conceptual separation
blob/checksum/soft-delete/GC behavior
```

### Do not finalize yet

```text
Project Runtime Schema
Stage Runtime Schema
Task/attempt execution schema
exact conditional-stage policy
exact parallel scheduling/resource policy
video_understanding payload schema
edit_plan payload schema
timeline payload schema
advanced workflow canvas
```

---

## 20. Context for the next architecture chat

The next design task is **Project Runtime Schema**.

That schema should focus on the persistent state required to resume and reproduce one concrete Project, while keeping Workflow Definition and Artifact payloads external.

It will need to answer, at minimum:

```text
How is Project identity stored?
How is workflow_id + workflow_version pinned?
How are source assets referenced?
Where do project-level settings/instructions live?
How are Active Artifact bindings represented?
How are Stage Runtime objects referenced/stored?
How is current navigation/progress represented without making it the source of truth?
How are variants represented?
What happens when Active inputs change?
How are stale/fresh states derived or persisted?
How should Project cloning/forking work later?
What may be safely deleted with a Project vs retained as reusable source analysis?
```

The guiding principle is:

> Project Runtime should be a compact persistent index/state model referencing Workflow and Artifact IDs, not a giant container that duplicates every payload.

Do not reopen already locked architecture unless actual repository constraints reveal a concrete conflict.
