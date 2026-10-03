# Video Understanding v1 — Implementation Status

This change implements U1–U9 from
`VIDEO_UNDERSTANDING_IMPLEMENTATION_GUIDE_V1.md` as an additive staged layer.
The legacy one-shot Mode 1/Mode 2 path remains available and unchanged.

## Implemented phases

- **U1 — Payload models:** strict immutable `transcript.v1` and
  `video_understanding.v1` models, nested validation, unknown-field rejection,
  deterministic UTF-8 JSON, finite-number checks and segment/overlay invariants.
- **U2 — Effective review:** lock/override/baseline precedence, derived effective
  summary/segments and typed add/remove/restore/split/merge review drafts.
- **U3 — Workflow/source:** pinned `video_grounded.understand@1.0` Workflow,
  exact `primary_video` Project binding and atomic canonical Source Asset records.
- **U4 — Transcription:** injected transcription executor plus a lazy
  faster-whisper source capability that produces validated `TranscriptV1`.
- **U5 — Analysis:** transcript-grounded adapter over the existing legacy video
  analyzer, source-time transcript joins, new opaque Semantic Segment IDs and
  Asset-owned formal output.
- **U6 — Review save:** immutable human child revisions with inherited exact
  dependencies and no implicit Active or Approved movement.
- **U7 — Regeneration:** normal, fresh and partial full-revision operations;
  topology/IDs/overlay are preserved where required and candidate revisions do
  not replace Runtime selections.
- **U8 — Runtime/reuse:** exact-context transcript and Understanding reuse,
  durable reuse TaskAttempts, reversible freshness and the existing exact
  approval gate for `plan_source`.
- **U9 — Compatibility:** focused acceptance coverage plus the complete existing
  repository test suite.

## Modules added

```text
source_assets/
├── models.py
└── repository.py

understanding/
├── models.py
├── review.py
├── capabilities.py
├── executors.py
├── workflow.py
└── service.py
```

The new layer uses public `ArtifactRepository`, `WorkflowRuntime`,
`ProjectService`, `StageStateResolver` and `ExecutorRegistry` APIs. Artifact and
Runtime foundations do not import Understanding.

## Runtime behavior

The implemented vertical flow is:

```text
SourceAssetV1
→ transcribe_source
→ transcript.v1 Artifact
→ analyze_source
→ video_understanding.v1 Artifact
→ UnderstandingDraft / Save Version
→ explicit Set Active
→ explicit Approve
→ plan_source gate
```

Formal transcript and Understanding Families are Asset-owned. Initial successful
outputs fill an empty Active slot but never auto-approve Understanding. Human
saves, normal/partial regeneration and later Fresh Analysis produce candidates
without replacing a populated Active selection or approval snapshot.

## Legacy compatibility

The existing `agents.video_analyst_agent.analyze_video` callable and legacy
dataclasses are adapted rather than replaced. Formal payloads discard legacy
URL, local path, topic and cache details. Existing `scripts/run.py`,
`orchestrator.py`, launcher behavior, rendering, TTS, QA and publishing code are
unchanged.

## Verification

Final local verification on 2026-10-02:

```text
154 passed in 3.22s
git diff --check: clean
```

The suite includes payload, review, Source Asset, staged executor, immutable
save, regeneration, stale/fresh gate, exact cross-Project reuse and existing
legacy compatibility tests.

No live Bilibili, browser-cookie, Whisper model-download or Qwen/VLM smoke test
was run. Those require explicit media/provider credentials and are intentionally
outside deterministic U9 verification.

## Compatibility decision

The guide's generic dependency examples name the source Artifact input slot
`source`, while its pinned Workflow and current Runtime binding contract use
`primary_video`. The implementation consistently persists `primary_video`.
This preserves `BindingResolver` exact-input comparison, reversible freshness
and Runtime reuse without introducing a Workflow-specific remapping exception.

## Remaining product work

This implementation stops at the locked Understanding boundary. It does not
implement `director_control.v1`, `edit_plan.v1`, Planner behavior, timeline,
rendering migration or the Project Workspace UI. A production smoke run also
still requires a resolver/acquisition composition that supplies the ASR
capability with a safe media handle and never accesses browser cookies without
explicit authorization.
