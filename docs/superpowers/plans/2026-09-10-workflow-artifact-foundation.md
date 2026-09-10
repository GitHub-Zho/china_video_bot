# Workflow and Artifact Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the locked workflow-definition and Artifact v1 foundation without changing existing Mode 1, Mode 2, launcher, rendering, review, or publishing behavior.

**Architecture:** Add two independent standard-library packages. `workflow` owns immutable DAG definitions and logical executor resolution; `artifacts` owns immutable revision records, mutable in-memory drafts, content storage, and a filesystem repository. Existing pipeline functions remain unchanged and are exposed only through a lazy logical adapter.

**Tech Stack:** Python 3.10+, frozen dataclasses, enums, pathlib, JSON, SHA-256, atomic `os.replace`, `fcntl.flock`, pytest.

**Spec:** `docs/architecture/CODEX_ARCHITECTURE_HANDOFF.md`, with the binding Artifact contract in `docs/architecture/ARTIFACT_SCHEMA_V1.md` and task scope in `docs/architecture/CODEX_IMPLEMENTATION_TASK.md`.

## Global Constraints

- Preserve current Mode 1, Mode 2, launcher, FFmpeg, QA, review, and publishing behavior.
- Workflow definitions reference logical executor IDs, never Python module paths or model names.
- Workflow Stage I/O declares named Artifact Type slots with cardinality exactly `one`, `optional`, or `many`; runtime bindings use exact Artifact IDs but no Project or Stage Runtime schema is introduced here.
- A Family identity is `owner.kind + owner.id + artifact_type + normalized variant_key`; owner kinds are exactly `asset` and `project`.
- Every formal Revision has a globally unique Artifact ID and a Family-local, monotonically increasing revision number that is never reused.
- Formal Revision content is immutable through normal APIs; edits flow through mutable Working Draft to Save Version to a new Revision.
- `preferred_artifact_id` is Family-owned, may be null, and can only reference a live Revision in the same Family; deleting it clears the pointer without selecting a replacement.
- `content_ref.storage` is exactly `file` or `blob`; paths are storage-root-relative and checksums are SHA-256 over exact persisted bytes.
- Delete lifecycle is live to soft-deleted to optionally restored or purged; purge refuses known dependency/dependent or external-reference violations.
- Blob cleanup uses mark-and-sweep reachability and defaults to dry-run.
- Do not finalize Project Runtime, Stage Runtime, task/attempt schemas, payload schemas, conditional language, parallel scheduling, or advanced GUI/canvas behavior.
- Use the existing virtual environment `/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python`; do not add dependencies.

---

### Task 1: Workflow Definitions and Logical Executor Registry

**Files:**
- Create: `workflow/__init__.py`
- Create: `workflow/models.py`
- Create: `workflow/executors.py`
- Create: `workflow/adapters.py`
- Test: `tests/test_workflow_foundation.py`

**Interfaces:**
- Produces: `Cardinality`, `ArtifactSlotDefinition`, `StageDefinition`, `WorkflowDefinition`, `ExecutorRegistry`, `ExecutorNotFoundError`, `register_existing_executors`.
- `ArtifactSlotDefinition.validate_binding(value)` accepts one Artifact ID, null-or-one, or a list of Artifact IDs according to cardinality.
- `WorkflowDefinition.topological_stage_ids()` returns deterministic dependency order and constructor validation rejects missing dependencies and cycles.
- `register_existing_executors(registry, analyzer=None)` binds `video_grounded.analyze_source` to the existing `agents.video_analyst_agent.analyze_video` path lazily; optional injection makes the adapter testable without network/model calls.

- [ ] **Step 1: Write failing Workflow model tests**

Create tests with literal expectations for:

```python
def test_slot_cardinality_validates_runtime_binding_shapes():
    assert ArtifactSlotDefinition("video_understanding", Cardinality.ONE).validate_binding("art_1") == "art_1"
    assert ArtifactSlotDefinition("video_understanding", Cardinality.OPTIONAL).validate_binding(None) is None
    assert ArtifactSlotDefinition("frame", Cardinality.MANY).validate_binding(["art_1", "art_2"]) == ("art_1", "art_2")

def test_workflow_rejects_unknown_dependency():
    with pytest.raises(ValueError, match="unknown dependency"):
        WorkflowDefinition(
            workflow_id="rough_cut.default",
            workflow_version="1",
            mode="rough_cut",
            stages=(StageDefinition("plan", "plan", "rough_cut.plan", depends_on=("missing",)),),
        )

def test_workflow_rejects_dependency_cycle():
    stages = (
        StageDefinition("a", "analyze", "test.a", depends_on=("b",)),
        StageDefinition("b", "plan", "test.b", depends_on=("a",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        WorkflowDefinition("test.default", "1", "rough_cut", stages)
```

Also cover duplicate Stage IDs, a deterministic topological order, invalid empty IDs, duplicate dependencies, and invalid binding shapes for all three cardinalities.

- [ ] **Step 2: Run Workflow model tests and verify RED**

Run:

```bash
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest tests/test_workflow_foundation.py -q
```

Expected: collection fails because the `workflow` package does not exist.

- [ ] **Step 3: Implement minimal immutable Workflow models**

Use frozen dataclasses and `Cardinality(str, Enum)`. `StageDefinition` fields are `stage_id`, `phase`, `executor`, `inputs`, `outputs`, `depends_on`, `approval_required`, and `ui_component`. Copy mappings to read-only mappings and sequences to tuples in `__post_init__`. `WorkflowDefinition` validates unique IDs and dependencies and uses Kahn traversal in declaration order to detect cycles and produce deterministic order.

- [ ] **Step 4: Run Workflow model tests and verify GREEN**

Run the focused command from Step 2 and require all tests to pass with no warnings.

- [ ] **Step 5: Write failing executor registry and adapter tests**

Cover duplicate logical ID rejection, unknown ID error, positional/keyword pass-through, and the existing analysis adapter:

```python
def test_existing_video_analysis_adapter_uses_logical_id():
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
```

- [ ] **Step 6: Run registry tests and verify RED**

Run the focused test file and confirm failure names the missing registry/adapter behavior.

- [ ] **Step 7: Implement registry and lazy existing-path adapter**

`ExecutorRegistry.register()` rejects empty or duplicate logical IDs, `resolve()` raises `ExecutorNotFoundError`, and `execute()` delegates without defining runtime state. The default adapter imports `analyze_video` inside registration or first execution so importing the foundation never loads model/API dependencies.

- [ ] **Step 8: Run focused and baseline tests**

Run:

```bash
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest tests/test_workflow_foundation.py -q
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest -q
```

- [ ] **Step 9: Commit Task 1**

```bash
git add workflow tests/test_workflow_foundation.py
git commit -m "feat(workflow): add DAG definitions and executor registry"
```

---

### Task 2: Artifact Value Models and Working Draft

**Files:**
- Create: `artifacts/__init__.py`
- Create: `artifacts/models.py`
- Create: `artifacts/drafts.py`
- Test: `tests/test_artifact_models.py`

**Interfaces:**
- Produces: `OwnerKind`, `ProducerKind`, `InputRefKind`, `StorageMode`, `ArtifactOwner`, `ArtifactProducer`, `ArtifactInputRef`, `Checksum`, `ContentRef`, `ArtifactFamily`, `ArtifactRevision`, `WorkingDraft`, `normalize_variant_key`.
- Every formal model supports `to_dict()` and `from_dict()` with schema versions exactly `artifact_family.v1` and `artifact_revision.v1`.
- `ArtifactOwner.asset(id)` and `ArtifactOwner.project(id)` construct the two allowed owner kinds.
- `WorkingDraft.replace_content(bytes, media_type=None, storage=None)` mutates only the draft, returns the same draft for explicit save chaining, and repository save consumes a snapshot to create a new immutable `ArtifactRevision`.

- [ ] **Step 1: Write failing Artifact model tests**

Cover Family identity normalization, enum/ID validation, producer requirements, parent/input reference shapes, content-ref mode exclusivity, relative-path enforcement, SHA-256 validation, serialization round trips, and structural immutability:

```python
def checksum():
    return Checksum("sha256", hashlib.sha256(b"{}").hexdigest())

def test_variant_normalization_trims_unicode_but_preserves_case():
    assert normalize_variant_key("  growth  ") == "growth"
    assert normalize_variant_key("Ｇrowth") == "Growth"
    assert normalize_variant_key("   ") is None

def test_file_content_ref_rejects_absolute_path():
    with pytest.raises(ValueError, match="relative"):
        ContentRef.file(Path("/tmp/result.json"), "application/json", 2, checksum())

def test_executor_producer_requires_executor_id():
    with pytest.raises(ValueError, match="executor_id"):
        ArtifactProducer(kind=ProducerKind.EXECUTOR)

def test_formal_revision_is_structurally_immutable():
    revision = make_revision()
    with pytest.raises(FrozenInstanceError):
        revision.revision = 9
    with pytest.raises(TypeError):
        revision.metadata["change_kind"] = "overwrite"
```

- [ ] **Step 2: Run Artifact model tests and verify RED**

Run the focused test file and confirm failure because the package is absent.

- [ ] **Step 3: Implement immutable Artifact v1 value models**

Use frozen dataclasses, tuples, and recursively copied read-only metadata. Normalize variants with Unicode NFKC plus whitespace trimming but preserve case. Validate that file paths are relative and contain no `..`; require exactly `path` for file or `blob_id` for blob. Validate `sha256` as 64 lowercase hexadecimal characters. Preserve unknown metadata values only when JSON serializable.

- [ ] **Step 4: Run Artifact model tests and verify GREEN**

Run the focused test file and require clean output.

- [ ] **Step 5: Write failing Working Draft tests**

Verify draft mutation leaves its base `ArtifactRevision` unchanged, `snapshot()` returns copied bytes/metadata, and invalid base Family IDs are rejected by construction.

- [ ] **Step 6: Run draft tests and verify RED**

Confirm failure names the missing draft implementation.

- [ ] **Step 7: Implement Working Draft**

Keep the draft in memory with `family_id`, optional `base_artifact_id`, payload schema version, producer, input references, metadata, content bytes, media type, and storage preference. Draft setters copy mutable inputs; no method mutates a formal Revision.

- [ ] **Step 8: Run focused and baseline tests**

Run:

```bash
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest tests/test_artifact_models.py -q
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest -q
```

- [ ] **Step 9: Commit Task 2**

```bash
git add artifacts/__init__.py artifacts/models.py artifacts/drafts.py tests/test_artifact_models.py
git commit -m "feat(artifacts): add immutable schema v1 models and drafts"
```

---

### Task 3: Content Storage Abstraction

**Files:**
- Create: `artifacts/storage.py`
- Test: `tests/test_artifact_storage.py`

**Interfaces:**
- Consumes: `StorageMode`, `Checksum`, `ContentRef` from Task 2.
- Produces: `ContentStore` protocol, `FileContentStore`, `GcCandidate`.
- `FileContentStore.persist(data, storage, media_type, file_path=None)` atomically writes exact bytes and returns a validated `ContentRef`.
- Blob paths are content-addressed by SHA-256 and identical bytes deduplicate; file storage requires an explicit repository-relative target.
- `resolve(content_ref)` returns a verified path and raises `ContentIntegrityError` if size/checksum no longer matches.
- `list_managed_content()` returns storage-root-relative managed content paths for repository GC.

- [ ] **Step 1: Write failing storage tests**

Cover exact-byte SHA-256, atomic file persistence, blob deduplication, relative-path enforcement, file/blob resolution, missing content, and tamper detection:

```python
def test_blob_storage_deduplicates_identical_exact_bytes(tmp_path):
    store = FileContentStore(tmp_path)
    first = store.persist(b"same bytes", StorageMode.BLOB, "video/mp4")
    second = store.persist(b"same bytes", StorageMode.BLOB, "video/mp4")
    assert first.blob_id == second.blob_id
    assert first.checksum.value == hashlib.sha256(b"same bytes").hexdigest()
    assert store.resolve(first).read_bytes() == b"same bytes"

def test_resolve_detects_tampered_content(tmp_path):
    store = FileContentStore(tmp_path)
    ref = store.persist(b"original", StorageMode.FILE, "application/json", Path("families/af_1/content/v001.json"))
    store.resolve(ref).write_bytes(b"changed")
    with pytest.raises(ContentIntegrityError):
        store.resolve(ref)
```

- [ ] **Step 2: Run storage tests and verify RED**

Run the focused test file and confirm the missing storage module is the cause.

- [ ] **Step 3: Implement file and blob storage**

Write temporary files in the destination directory, flush/fsync, and atomically replace. Blob IDs use `blob_sha256_<digest>` and paths use `blobs/sha256/<first-two>/<digest>`. File paths stay exactly under the storage root. Never accept absolute paths or traversal. Integrity checking compares both size and digest.

- [ ] **Step 4: Run focused and baseline tests**

Run:

```bash
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest tests/test_artifact_storage.py -q
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest -q
```

- [ ] **Step 5: Commit Task 3**

```bash
git add artifacts/storage.py tests/test_artifact_storage.py
git commit -m "feat(artifacts): add checksummed file and blob storage"
```

---

### Task 4: Filesystem Artifact Repository and Migration Notes

**Files:**
- Create: `artifacts/repository.py`
- Modify: `artifacts/__init__.py`
- Create: `tests/test_artifact_repository.py`
- Create: `docs/architecture/WORKFLOW_ARTIFACT_FOUNDATION_IMPLEMENTED.md`

**Interfaces:**
- Consumes: all Task 2 models/drafts and Task 3 storage.
- Produces: `ArtifactRepository`, `ArtifactNotFoundError`, `ArtifactConflictError`, `PurgeBlockedError`, `GarbageCollectionReport`.
- Repository layout is private implementation detail under its storage root: atomic index and Family/Revision JSON records plus managed `files/` and `blobs/` content.
- `ArtifactRepository` exposes the minimum interface in `ARTIFACT_SCHEMA_V1.md`; Project/Stage state never depends on its physical paths.
- Constructor accepts optional `external_reference_checker(artifact_id) -> bool` so later Runtime persistence can block purge without defining Runtime schemas now.

- [ ] **Step 1: Write failing Family/revision repository tests**

Cover identity uniqueness, get/find/list, exact Artifact lookup, revision monotonicity, deleted-number non-reuse, parent same-Family enforcement, Artifact input existence, and blob reuse:

```python
def test_revision_numbers_are_monotonic_and_deleted_numbers_are_not_reused(repo):
    family = repo.get_or_create_family(ArtifactOwner.asset("asset_1"), "video_understanding")
    first = repo.save_revision(repo.create_draft(family.family_id).replace_content(b"one"))
    repo.soft_delete_revision(first.artifact_id)
    repo.purge_revision(first.artifact_id)
    second = repo.save_revision(repo.create_draft(family.family_id).replace_content(b"two"))
    assert (first.revision, second.revision) == (1, 2)

def test_new_revision_does_not_move_existing_preferred_pointer(repo):
    family = repo.get_or_create_family(ArtifactOwner.asset("asset_1"), "video_understanding")
    first = repo.save_revision(repo.create_draft(family.family_id).replace_content(b"original"))
    repo.set_preferred(family.family_id, first.artifact_id)
    second = repo.save_revision(repo.create_draft(family.family_id, first.artifact_id).replace_content(b"edit"))
    assert repo.get_family(family.family_id).preferred_artifact_id == first.artifact_id
    assert second.parent_artifact_id == first.artifact_id
```

- [ ] **Step 2: Run repository creation tests and verify RED**

Run the focused file and confirm failure names the missing repository behavior.

- [ ] **Step 3: Implement atomic Family and Revision persistence**

Use a global repository lock for Family identity creation and per-Family `fcntl.flock` files for revision allocation/save. Persist `next_revision + 1` before content/Revision creation so a failed save may consume but can never reuse a number. Use atomic JSON writes. Formal Revision files have no update API; lifecycle changes atomically replace metadata records while never replacing content bytes.

- [ ] **Step 4: Run repository creation tests and verify GREEN**

Run the focused tests and require clean output.

- [ ] **Step 5: Write failing Preferred/delete/purge/dependency tests**

Cover same-Family/live Preferred rules, clear Preferred, deleting Preferred clears only the pointer, soft-delete/restore, restore-after-purge rejection, dependent Revision discovery through parent and input refs, purge refusal when dependents exist, and the external reference hook.

- [ ] **Step 6: Run lifecycle tests and verify RED**

Confirm each new behavior fails for the expected missing implementation path.

- [ ] **Step 7: Implement lifecycle and dependency APIs**

Normal lists hide soft-deleted and purged records; `include_deleted=True` includes soft-deleted tombstones but never purged records. `get_revision()` can read a tombstone for auditing but `resolve_content()` rejects deleted or purged content. Purge requires prior soft deletion, no Preferred pointer, no unpurged dependents, and no external reference. Purge sets `purged_at`; it does not delete physical bytes directly.

- [ ] **Step 8: Write failing checksum/content resolution and GC tests**

Cover exact content resolution, integrity failure, dry-run candidate reporting without deletion, non-dry sweep of unreachable file/blob data, shared blob preservation while one live Revision still references it, and purged-only blob reclamation.

- [ ] **Step 9: Run resolution/GC tests and verify RED**

Confirm the missing GC/resolution behavior is the failure cause.

- [ ] **Step 10: Implement content resolution and mark-and-sweep GC**

Mark every `content_ref` owned by a non-purged Revision, including soft-deleted Revisions. Compare marks with `ContentStore.list_managed_content()`. Dry-run returns `GarbageCollectionReport` with relative paths and byte totals without mutation; sweep deletes only candidates below the repository root and removes empty managed directories.

- [ ] **Step 11: Run focused and full tests**

Run:

```bash
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest tests/test_artifact_repository.py -q
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest -q
```

- [ ] **Step 12: Write implementation/migration notes**

Document exact packages and public interfaces, storage layout as private, the wrapped `video_grounded.analyze_source` adapter, Mode 1/2 non-integration in this step, and the explicit next Runtime questions: pinned Workflow/version, active bindings, Stage approved Artifact ID, Stage input bindings, stale derivation, project cloning, external purge references, and retained source analysis ownership. State that no Runtime schema is implemented.

- [ ] **Step 13: Verify scope and repository hygiene**

Run:

```bash
git diff --check
rg -n "ProjectRuntime|StageRuntime|task_attempt|video_understanding\.v1|edit_plan\.v1|timeline\.v1" workflow artifacts tests docs/architecture/WORKFLOW_ARTIFACT_FOUNDATION_IMPLEMENTED.md
git status --short
```

Expected: no whitespace errors; any Runtime-name matches appear only in explanatory text/tests that assert deferral, not implemented schemas.

- [ ] **Step 14: Commit Task 4**

```bash
git add artifacts tests/test_artifact_repository.py docs/architecture/WORKFLOW_ARTIFACT_FOUNDATION_IMPLEMENTED.md
git commit -m "feat(artifacts): add filesystem revision repository"
```

---

### Task 5: End-to-End Foundation Verification

**Files:**
- Modify only if a verification defect requires a reviewed fix.

**Interfaces:**
- Consumes the public `workflow` and `artifacts` packages from Tasks 1-4.
- Produces fresh evidence that the locked foundation works while the legacy suite remains green.

- [ ] **Step 1: Run the full automated suite**

```bash
/Users/jo/codes/claude/china_video_bot-ui/.venv/bin/python -m pytest -q
```

Require zero failures and no warnings.

- [ ] **Step 2: Run a temporary-directory Artifact integration probe**

Use a one-shot Python command that creates an Asset-owned Family, saves a file Revision and blob Revision through drafts, selects Preferred, soft-deletes/restores one Revision, lists dependencies, resolves exact bytes, and runs `gc(dry_run=True)`. Assert every returned Artifact ID and revision number rather than printing secrets or external paths.

- [ ] **Step 3: Run a Workflow integration probe**

Instantiate a three-Stage branched DAG, assert deterministic topological order, register a local deterministic executor, execute it through its logical ID, and verify no model/module path appears in the Workflow definition.

- [ ] **Step 4: Verify no legacy orchestration changes**

```bash
git diff origin/codex/workflow-artifact-architecture-v1...HEAD -- scripts/run.py orchestrator.py launcher agents config
```

Expected: empty diff.

- [ ] **Step 5: Verify final diff and commit history**

```bash
git diff --check
git status --short --branch
git log --oneline origin/codex/workflow-artifact-architecture-v1..HEAD
```

Document exact test counts and any deliberate implementation rulings in the final handoff.
