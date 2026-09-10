import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from artifacts.models import (
    ArtifactFamily,
    ArtifactInputRef,
    ArtifactOwner,
    ArtifactProducer,
    ArtifactRevision,
    Checksum,
    ContentRef,
    InputRefKind,
    OwnerKind,
    ProducerKind,
    StorageMode,
    normalize_variant_key,
)
from artifacts.drafts import WorkingDraft


def checksum():
    return Checksum("sha256", hashlib.sha256(b"{}").hexdigest())


def make_family(**overrides):
    values = {
        "family_id": "af_1",
        "owner": ArtifactOwner.asset("asset_video_001"),
        "artifact_type": "video_understanding",
        "variant_key": "  Ｇrowth  ",
        "display_name": "Video Understanding",
        "preferred_artifact_id": None,
        "next_revision": 1,
        "created_at": "2026-09-10T12:20:00-04:00",
        "updated_at": "2026-09-10T12:21:00-04:00",
    }
    values.update(overrides)
    return ArtifactFamily(**values)


def make_revision(**overrides):
    values = {
        "artifact_id": "art_1",
        "family_id": "af_1",
        "artifact_type": "video_understanding",
        "revision": 1,
        "payload_schema_version": "video_understanding.v1",
        "parent_artifact_id": None,
        "created_at": "2026-09-10T12:20:30-04:00",
        "producer": ArtifactProducer(
            kind=ProducerKind.EXECUTOR,
            executor_id="video_grounded.analyze_source",
            project_id="proj_123",
        ),
        "input_refs": (
            ArtifactInputRef("source", InputRefKind.ASSET, "asset_video_001"),
        ),
        "content_ref": ContentRef.file(
            Path("assets/asset_video_001/content/v001.json"),
            "application/json",
            2,
            checksum(),
        ),
        "metadata": {"change_kind": "human_edit", "nested": {"tags": ["a"]}},
        "deleted_at": None,
        "purged_at": None,
    }
    values.update(overrides)
    return ArtifactRevision(**values)


def test_variant_normalization_trims_unicode_but_preserves_case():
    """Catch family identity keys that silently retain whitespace or width variants."""
    assert normalize_variant_key("  growth  ") == "growth"
    assert normalize_variant_key("Ｇrowth") == "Growth"
    assert normalize_variant_key("   ") is None
    assert make_family().variant_key == "Growth"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ArtifactOwner("asset", ""),
        lambda: ArtifactOwner("unknown", "owner_1"),
        lambda: ArtifactOwner.asset(""),
        lambda: ArtifactOwner.project(""),
        lambda: ArtifactInputRef("", InputRefKind.ASSET, "asset_1"),
        lambda: ArtifactInputRef("source", "unknown", "asset_1"),
        lambda: ArtifactInputRef("source", InputRefKind.ASSET, ""),
    ],
)
def test_owner_and_input_references_reject_invalid_enum_or_id_values(factory):
    """Catch malformed stable identifiers entering family ownership or dependencies."""
    with pytest.raises(ValueError):
        factory()


def test_executor_producer_requires_executor_id():
    """Catch executor revisions that cannot be traced to a logical executor."""
    with pytest.raises(ValueError, match="executor_id"):
        ArtifactProducer(kind=ProducerKind.EXECUTOR)


@pytest.mark.parametrize(
    "candidate",
    [
        lambda: Checksum("sha256", "A" * 64),
        lambda: Checksum("sha256", "a" * 63),
        lambda: Checksum("md5", "a" * 64),
    ],
)
def test_checksum_rejects_any_value_outside_lowercase_sha256(candidate):
    """Catch content checksums that cannot represent the locked byte-integrity algorithm."""
    with pytest.raises(ValueError):
        candidate()


def test_file_content_ref_rejects_absolute_path():
    """Catch machine-specific paths escaping the repository storage root."""
    with pytest.raises(ValueError, match="relative"):
        ContentRef.file(Path("/tmp/result.json"), "application/json", 2, checksum())


@pytest.mark.parametrize("path", ["", "   ", Path(".")])
def test_file_content_ref_requires_a_concrete_relative_path(path):
    """Catch an empty storage location being normalized into the storage root itself."""
    with pytest.raises(ValueError, match="path"):
        ContentRef.file(path, "application/json", 2, checksum())


@pytest.mark.parametrize("path", [Path("../result.json"), Path("safe/../../result.json")])
def test_file_content_ref_rejects_parent_traversal(path):
    """Catch relative-looking paths that still leave the managed storage root."""
    with pytest.raises(ValueError, match="\\.\\."):
        ContentRef.file(path, "application/json", 2, checksum())


@pytest.mark.parametrize(
    "path",
    [r"C:\\repo\\result.json", r"\\\\server\\share\\result.json", r"..\\result.json", r"safe\\..\\result.json"],
)
def test_file_content_ref_rejects_nonportable_absolute_or_traversal_paths(path):
    """Catch Windows path forms that POSIX pathlib would otherwise serialize unchanged."""
    with pytest.raises(ValueError, match="path"):
        ContentRef.file(path, "application/json", 2, checksum())


def test_content_ref_requires_only_the_identifier_for_its_storage_mode():
    """Catch file/blob references that are ambiguous about where bytes are stored."""
    with pytest.raises(ValueError, match="path"):
        ContentRef(StorageMode.FILE, "application/json", 2, checksum())
    with pytest.raises(ValueError, match="blob_id"):
        ContentRef(StorageMode.BLOB, "video/mp4", 2, checksum())
    with pytest.raises(ValueError, match="path"):
        ContentRef(
            StorageMode.BLOB,
            "video/mp4",
            2,
            checksum(),
            path="also/a/path.mp4",
            blob_id="blob_sha256_value",
        )


def test_formal_models_round_trip_through_versioned_dicts():
    """Catch a wire representation that loses a Family or Revision contract field."""
    family = make_family()
    revision = make_revision()

    assert family.to_dict()["schema_version"] == "artifact_family.v1"
    assert revision.to_dict()["schema_version"] == "artifact_revision.v1"
    assert ArtifactFamily.from_dict(family.to_dict()) == family
    assert ArtifactRevision.from_dict(revision.to_dict()) == revision


def test_formal_revision_is_structurally_immutable():
    """Catch externally mutable metadata or dataclass fields on a saved revision."""
    metadata = {"change_kind": "human_edit", "nested": {"tags": ["a"]}}
    revision = make_revision(metadata=metadata)
    metadata["nested"]["tags"].append("later")

    with pytest.raises(FrozenInstanceError):
        revision.revision = 9
    with pytest.raises(TypeError):
        revision.metadata["change_kind"] = "overwrite"
    with pytest.raises(TypeError):
        revision.metadata["nested"]["tags"] = ("overwrite",)
    assert revision.metadata == {"change_kind": "human_edit", "nested": {"tags": ("a",)}}


def test_formal_revision_rejects_non_json_metadata():
    """Catch metadata that cannot be stored in a revision's JSON representation."""
    with pytest.raises(ValueError, match="JSON"):
        make_revision(metadata={"unsupported": {"a", "set"}})


def test_working_draft_changes_do_not_mutate_its_base_revision():
    """Catch edit state leaking back into the immutable revision selected as a base."""
    base = make_revision()
    draft = WorkingDraft.from_revision(base)

    assert draft.replace_content(
        bytearray(b"edited"), media_type="text/plain", storage=StorageMode.BLOB
    ) is draft
    draft.set_metadata({"change_kind": "rewrite", "nested": {"tags": ["b"]}})

    assert base.metadata == {"change_kind": "human_edit", "nested": {"tags": ("a",)}}
    assert base.content_ref.media_type == "application/json"
    assert draft.base_artifact_id == base.artifact_id


def test_working_draft_snapshot_owns_copied_content_and_metadata():
    """Catch a repository save observing later in-memory draft changes through a snapshot."""
    metadata = {"change_kind": "human_edit", "nested": {"tags": ["a"]}}
    draft = WorkingDraft("af_1", metadata=metadata, content=bytearray(b"initial"))
    snapshot = draft.snapshot()

    metadata["nested"]["tags"].append("outside")
    draft.metadata["nested"]["tags"].append("later")
    draft.replace_content(b"replacement")

    assert snapshot.content == b"initial"
    assert snapshot.metadata == {"change_kind": "human_edit", "nested": {"tags": ("a",)}}


@pytest.mark.parametrize(
    "family_id, base_artifact_id",
    [("", None), ("af_1", "")],
)
def test_working_draft_rejects_invalid_family_or_base_artifact_ids(
    family_id, base_artifact_id
):
    """Catch drafts that cannot be associated with a valid Family or base revision."""
    with pytest.raises(ValueError):
        WorkingDraft(family_id, base_artifact_id=base_artifact_id)
