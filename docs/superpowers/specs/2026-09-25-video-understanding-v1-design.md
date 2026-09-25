# Video Understanding v1 and Understand/Review Vertical Slice

**Status:** Approved in conversation on 2026-09-25

## Intent

Build the first production Runtime-connected vertical slice for China Video Bot:

```text
register source video
→ create Project pinned to the Understand Workflow
→ bind a versioned analysis prompt
→ execute the existing video analyst
→ save an Asset-owned video_understanding.v1 Artifact
→ review and explicitly approve that exact revision
→ reopen with the same state
→ reuse the revision in another Project without another model call
```

The first live demonstration uses:

- source: `https://www.bilibili.com/video/BV1Lfau6iEYQ/`
- topic: `general video understanding`
- primary provider: Qwen-VL Max through DashScope

This slice proves the backend product contract. It does not add the final UI.

## Locked constraints

- Preserve the existing Artifact and Workflow foundations.
- Use `ProjectRuntimeV1`, `StageRunV1`, `TaskAttemptV1`, `WorkflowRuntime`, and
  `ExecutorRegistry`; do not duplicate their state machines.
- Keep Save Version, Set Active, and Approve as separate operations.
- Runtime and Artifact dependency identity uses stable Asset/Artifact IDs, never
  URLs or machine-local paths.
- The understanding is an Asset-owned reusable Artifact and may outlive a
  Project.
- Later understanding revisions do not silently replace Active or Approved.
- Preserve the legacy launcher, `scripts/run.py`, `orchestrator.py`, rendering,
  review, and publishing paths.
- Stop before `director_control.v1`, `edit_plan.v1`, `timeline.v1`, planning,
  rendering, publishing, and final UI work.

## Selected approach

Implement the durable vertical slice rather than a demo-only in-memory wrapper.
The slice adds a small source-Asset repository, a production Workflow
definition, a versioned understanding payload adapter, and an application
service/CLI composition root. The existing `video_analyst_agent` remains the
analysis capability behind the logical `video_grounded.analyze_source`
executor.

A demo-only Asset dictionary was rejected because it would need replacement
before the UI could safely reopen Projects. Waiting for the UI was rejected
because it would postpone validation of the source-analysis and review
contracts until changes were more expensive.

## Components

### Source Asset repository

A focused `source_assets` package stores remote-video identity separately from
Runtime and Artifact payloads.

`SourceAssetV1` fields:

```text
schema_version = source_asset.v1
asset_id
asset_kind = remote_video
source_uri
display_name nullable
created_at
updated_at
```

Rules:

- `source_uri` is canonicalized before lookup or save.
- The Bilibili canonical form keeps the BV identifier and removes tracking
  parameters.
- Registering the same canonical URI returns the same Asset rather than making
  duplicates.
- The repository owns its filesystem layout and writes records atomically.
- API credentials, cookies, downloaded media paths, and frame-cache paths are
  not stored on the Asset.
- The resolver accepts an `asset_id` and returns its canonical source URI.
- v1 supports remote video only; local uploads are a later extension.

### Production Workflow

Register one immutable Workflow definition:

```text
workflow_id      = video_grounded.understand
workflow_version = 1
mode             = video_grounded

stage_id         = analyze_source
phase            = analyze
executor         = video_grounded.analyze_source
input topic      = analysis_prompt / one
output understanding = video_understanding / one
approval_required = true
ui_component      = understanding_editor
```

The Project also binds source role `primary_video` to one stable Asset ID.
The Workflow schema is not redesigned to add first-class Asset slots.

### Analysis prompt

The content-affecting topic is a formal Project-owned `analysis_prompt`
Artifact. Its JSON content is:

```json
{
  "schema_version": "analysis_prompt.v1",
  "topic": "general video understanding"
}
```

The exact prompt Artifact ID is bound to the Project input slot `topic` and is
recorded in the TaskAttempt and understanding Artifact dependencies.

### video_understanding.v1 payload

The formal JSON payload contains durable semantic analysis only:

```json
{
  "schema_version": "video_understanding.v1",
  "topic": "general video understanding",
  "summary": "...",
  "duration_seconds": 123.4,
  "steps": [
    {
      "timestamp": "0:08",
      "start_seconds": 8.0,
      "action": "...",
      "detail": "..."
    }
  ],
  "timeline": [
    {
      "seconds": 8.0,
      "description": "..."
    }
  ]
}
```

Validation rules:

- `topic` and `summary` are non-empty strings.
- `duration_seconds` is finite and greater than zero.
- `steps` and `timeline` are non-empty lists.
- Times are finite, non-negative, and do not exceed duration beyond a small
  media-probe tolerance.
- Step action/detail and timeline descriptions are non-empty.
- Entries remain in nondecreasing time order.
- Unknown fields and the wrong schema version are rejected.
- Serialization is deterministic UTF-8 JSON.

The source URL, Asset ID, local download path, model name, and prompt version do
not belong in this semantic payload. Stable source/prompt identity lives in
`input_refs`; provider/model/profile provenance lives in Artifact metadata.

### Understand service and demonstration CLI

An application service composes the public repositories and existing Runtime:

- source Asset repository and URI resolver;
- Workflow catalog with `video_grounded.understand@1`;
- Runtime, Artifact repository, and Executor registry;
- topic Artifact resolver;
- existing `video_analyst_agent.analyze_video` through
  `adapt_video_analysis_executor`;
- validated `video_understanding.v1` output adapter.

The staged wrapper passes a private cache namespace and disables browser-cookie
fallback unless the caller explicitly enables it. The legacy analyzer gains
only an optional browser-cookie policy argument whose default preserves its
current one-shot behavior. An additive vision-provider trace records each
provider/model that successfully contributed to the synchronous analysis;
legacy callers do not need to consume this trace.

The service exposes separate operations to register/create, analyze, inspect,
approve, reopen, and reuse. A narrow CLI calls those operations for the live
demonstration. Analyze never implies approval.

## Data flow

1. Canonicalize and register the supplied Bilibili URL as a Source Asset.
2. Create a Project pinned to `video_grounded.understand@1` with
   `primary_video` bound to the Asset ID.
3. Save the topic as `analysis_prompt.v1` and bind its exact Artifact ID.
4. Resolve Runtime inputs and create an execute TaskAttempt.
5. Resolve Asset ID to URL and prompt Artifact ID to topic text.
6. Download/cache the media, probe duration, sample frames, and ask the vision
   provider to describe each batch.
7. Synthesize the summary/key steps and validate the complete result.
8. Save one Asset-owned `video_understanding.v1` revision whose `input_refs`
   contain the exact source Asset and prompt Artifact.
9. Set the first successful output Active without approving it.
10. Explicitly approve the exact Active revision after inspection.
11. Reopen all repositories and verify the pinned Workflow, source Asset,
    Active output, Approved output, and Artifact content.
12. Create another Project and record a reuse TaskAttempt pointing to the same
    understanding Artifact without running the analyzer again.

## Provider and cache behavior

The existing provider order remains:

1. Qwen-VL Max through DashScope;
2. Gemini Vision when configured and Qwen is unavailable;
3. Groq Llama 4 Scout when configured and earlier providers are unavailable.

Summary generation prefers Qwen Max text through DashScope and falls back to
Groq. The selected provider/model, sample interval, analysis profile, and
prompt/payload versions are recorded in Artifact metadata.

The staged service gives the legacy analyzer a cache namespace derived from:

```text
source Asset ID
+ exact topic Artifact checksum/ID
+ analysis prompt/profile version
+ declared provider policy/model identity
+ sample interval
```

This prevents an analysis created for one topic/model/profile from being
silently reused for another. The resulting Artifact metadata records the actual
successful provider trace, including any configured fallback. Cache locations
remain private execution details.

## Failure behavior

- Missing usable vision credentials fail before analysis starts.
- Download failure, invalid duration, zero extracted frames, provider failure,
  invalid model JSON, empty steps, empty timeline, or payload validation failure
  produces a failed TaskAttempt.
- Failed execution creates no formal understanding Artifact and moves no Active
  or Approved pointer.
- A fallback provider is allowed only when it is configured; the actual
  provider is recorded.
- Anonymous Bilibili access is attempted first. If Chrome browser cookies are
  required, the live run stops and requests explicit user approval before
  accessing them.
- Secrets and cookies are never printed, copied into records, or committed.
- Existing formal Artifact content and terminal TaskAttempt history remain
  immutable.

## Verification

Automated tests use injected analyzers and temporary repositories. They cover:

- URL canonicalization and duplicate Asset registration;
- Source Asset persistence/reopen and resolver behavior;
- exact production Workflow definition;
- prompt payload parsing and binding;
- understanding payload round-trip and validation failures;
- successful execute → Active → explicit Approve → reopen;
- failed/empty analysis creating no Artifact or pointer changes;
- exact Asset and prompt dependencies;
- later revisions not silently replacing Active/Approved;
- cross-Project reuse without an analyzer call; and
- topic/profile-sensitive cache namespace generation.

After deterministic tests pass, one explicit network-backed smoke test uses the
approved Bilibili URL and locally configured DashScope credentials. It reports
the Asset, Project, TaskAttempt, Artifact, Active, and Approved IDs plus a short
human-readable summary. It then reopens the repositories and verifies reuse in
a second Project without another API call.

The existing full test suite must remain green. The smoke test is not part of
the default automated suite because it has network, credential, browser-cookie,
rate-limit, and model-cost variability.

## Acceptance criteria

- A real Bilibili source is represented by a stable, reopenable Asset ID.
- The production Understand Workflow runs through the current Runtime and
  logical executor registry.
- The result is a valid, immutable, Asset-owned `video_understanding.v1`
  revision with exact source/prompt dependencies.
- Active and Approved are explicit, distinct, and recover after reopen.
- A second Project can reuse the exact revision without a duplicate model run.
- No legacy execution, rendering, launcher, or publishing behavior changes.
- No UI, planning, timeline, rendering, or publishing migration is included.
