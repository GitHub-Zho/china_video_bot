# Artifact Schema v1 — Locked Contract

This file contains the Artifact interfaces that are frozen enough to implement now.

## 1. Artifact Family identity

A Family is uniquely identified by:

```text
owner.kind + owner.id + artifact_type + normalized variant_key
```

v1 owner kinds:

```text
asset
project
```

Examples:

```text
asset/asset_video_001 + video_understanding + null
project/proj_001      + edit_plan          + growth
project/proj_001      + edit_plan          + info
```

Prompt/model/executor/change method do not participate in Family identity.

## 2. ArtifactFamily v1

```json
{
  "schema_version": "artifact_family.v1",
  "family_id": "af_7f91c2",
  "owner": {
    "kind": "asset",
    "id": "asset_video_001"
  },
  "artifact_type": "video_understanding",
  "variant_key": null,
  "display_name": "Video Understanding",
  "preferred_artifact_id": "art_83bd12",
  "next_revision": 5,
  "created_at": "2026-09-10T12:20:00-04:00",
  "updated_at": "2026-09-10T12:21:00-04:00"
}
```

Rules:

- `family_id` is globally unique.
- `variant_key` may be null but participates in Family identity.
- `preferred_artifact_id` may be null and, when set, must point to a live Revision in the same Family.
- deleting the preferred Revision clears the preferred pointer; do not silently pick a replacement.
- `next_revision` is monotonic and never moves backward.
- deleted/purged revision numbers are never reused.
- allocation/save must be atomic under a per-Family repository lock/transaction.

## 3. ArtifactRevision v1

```json
{
  "schema_version": "artifact_revision.v1",
  "artifact_id": "art_83bd12",
  "family_id": "af_7f91c2",
  "artifact_type": "video_understanding",
  "revision": 3,
  "payload_schema_version": "video_understanding.v1",
  "parent_artifact_id": "art_61ca27",
  "created_at": "2026-09-10T12:20:30-04:00",
  "producer": {
    "kind": "executor",
    "executor_id": "video_grounded.analyze_source",
    "project_id": "proj_123",
    "stage_run_id": "stage_run_456"
  },
  "input_refs": [
    {
      "slot": "source",
      "kind": "asset",
      "id": "asset_video_001"
    }
  ],
  "content_ref": {
    "storage": "file",
    "path": "assets/asset_video_001/artifacts/video_understanding/content/v003.json",
    "media_type": "application/json",
    "size_bytes": 84211,
    "checksum": {
      "algorithm": "sha256",
      "value": "..."
    }
  },
  "metadata": {
    "change_kind": "human_edit",
    "analysis_profile": "general"
  },
  "deleted_at": null,
  "purged_at": null
}
```

Rules:

- every formal Revision receives a globally unique `artifact_id`.
- `revision` is Family-local save order.
- formal Revision payload/content is immutable through normal APIs.
- changes occur through mutable Working Draft → Save Version → new Revision.
- `parent_artifact_id` is optional and, when present, must belong to the same Family.
- v1 lineage is single-parent; multi-source dependencies are expressed via `input_refs`.
- `artifact_type` must match the owning Family.
- Artifact metadata schema and payload schema version independently.

## 4. Producer

Allowed producer kinds:

```text
executor
human
import
migration
```

For executor-produced revisions, `executor_id` is required.

`project_id` and `stage_run_id` are optional because reusable source-level analyses may be created outside a Project.

Optional provenance such as prompt/model/analysis profile/change kind belongs in `metadata` and does not define identity or revision numbering.

## 5. Input references

Shape:

```json
{
  "slot": "understanding",
  "kind": "artifact",
  "id": "art_..."
}
```

Allowed v1 kinds:

```text
asset
artifact
```

Use stable IDs, never file paths, for dependency identity.

## 6. Unified content_ref

Supported storage modes in v1:

```text
file
blob
```

File example:

```json
{
  "storage": "file",
  "path": "relative/path/from/storage/root",
  "media_type": "application/json",
  "size_bytes": 12345,
  "checksum": {
    "algorithm": "sha256",
    "value": "..."
  }
}
```

Blob example:

```json
{
  "storage": "blob",
  "blob_id": "blob_sha256_...",
  "media_type": "video/mp4",
  "size_bytes": 123456789,
  "checksum": {
    "algorithm": "sha256",
    "value": "..."
  }
}
```

Do not add media-specific fields such as `video_path`, `json_path`, `audio_path`.

Store repository/storage-root-relative paths rather than machine-specific absolute paths.

## 7. Checksum

v1 uses:

```text
SHA-256 over exact persisted bytes
```

Checksum belongs to `content_ref`, not Artifact identity.

For JSON, deterministic serialization is recommended, but v1 does not require semantic/canonical JSON hashing.

## 8. Delete lifecycle

Use:

```text
live
→ soft delete / Trash
→ optional Restore
→ Purge
```

Lifecycle fields:

```text
deleted_at
purged_at
```

Normal live lists hide soft-deleted Revisions.

Purge is irreversible and must respect dependency/reference policy.

## 9. Blob garbage collection

Initial local implementation uses mark-and-sweep / reachability scanning rather than mutable ref counters.

Mark content reachable from:

- live Source Assets;
- live Artifact Revisions;
- soft-deleted but not purged Revisions;
- other explicitly retained repository records.

Sweep only unreachable content.

GC should support dry-run/report mode.

## 10. Workflow I/O contract

Workflow Stage inputs/outputs use named Slots with Artifact Type + cardinality.

Cardinality:

```text
one
optional
many
```

Definition example:

```json
{
  "inputs": {
    "understanding": {
      "type": "video_understanding",
      "cardinality": "one"
    }
  },
  "outputs": {
    "plan": {
      "type": "edit_plan",
      "cardinality": "one"
    }
  }
}
```

Runtime binding shape:

```text
one      → artifact_id
optional → null | artifact_id
many     → [artifact_id, ...]
```

Runtime must reference exact Artifact IDs.

## 11. State boundary

Do not store these states on ArtifactRevision:

```text
Project Active
Stage Approved
```

Ownership is:

```text
Family Preferred → ArtifactFamily
Project Active    → Project Runtime
Stage Approved    → Stage Runtime
```

Project Runtime and Stage Runtime are intentionally not finalized in this implementation step.

## 12. Minimum Artifact Repository interface

Provide an abstraction approximately equivalent to:

```text
get_or_create_family(...)
get_family(family_id)
find_family(owner, artifact_type, variant_key)
list_families(owner)

list_revisions(family_id, include_deleted=False)
get_revision(artifact_id)
resolve_content(artifact_id)

create_draft(family_id, base_artifact_id=None)
save_revision(...)

set_preferred(family_id, artifact_id_or_null)

soft_delete_revision(artifact_id)
restore_revision(artifact_id)
purge_revision(artifact_id)

list_dependencies(artifact_id)
list_dependents(artifact_id)

gc(dry_run=True)
```

Exact method names may differ, but higher layers must depend on a repository/service abstraction rather than scattered directory scans.