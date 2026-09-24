from __future__ import annotations

from pathlib import Path

import pytest

from artifacts import (
    ArtifactConflictError,
    ArtifactInputRef,
    ArtifactNotFoundError,
    ArtifactOwner,
    ArtifactRepository,
    InputRefKind,
    PurgeBlockedError,
    StorageMode,
)


@pytest.fixture
def repo(tmp_path: Path) -> ArtifactRepository:
    return ArtifactRepository(tmp_path)


def _family(repo: ArtifactRepository, asset_id: str = "asset_1", artifact_type: str = "video_understanding"):
    return repo.get_or_create_family(ArtifactOwner.asset(asset_id), artifact_type)


def _save(repo: ArtifactRepository, family_id: str, content: bytes, *, storage=StorageMode.BLOB):
    draft = repo.create_draft(family_id)
    draft.replace_content(content, "application/json", storage)
    return repo.save_revision(draft)


def test_family_identity_is_unique_and_source_owned_without_project(repo: ArtifactRepository):
    owner = ArtifactOwner.asset("asset_video_001")
    first = repo.get_or_create_family(owner, "video_understanding", "  general  ")
    same = repo.get_or_create_family(owner, "video_understanding", "general")
    other = repo.get_or_create_family(owner, "transcript", "general")

    assert same.family_id == first.family_id
    assert other.family_id != first.family_id
    assert repo.find_family(owner, "video_understanding", "general") == first
    assert repo.list_families(owner) == (first, other)
    assert first.owner.kind.value == "asset"


def test_revision_numbers_are_monotonic_after_purge(repo: ArtifactRepository):
    family = _family(repo)
    first = _save(repo, family.family_id, b"one")
    repo.soft_delete_revision(first.artifact_id)
    repo.purge_revision(first.artifact_id)
    second = _save(repo, family.family_id, b"two")

    assert (first.revision, second.revision) == (1, 2)
    assert repo.get_family(family.family_id).next_revision == 3
    assert repo.list_revisions(family.family_id) == (second,)
    assert repo.list_revisions(family.family_id, include_deleted=True) == (second,)


def test_new_revision_does_not_move_preferred_and_delete_clears_it(repo: ArtifactRepository):
    family = _family(repo)
    first = _save(repo, family.family_id, b"original")
    repo.set_preferred(family.family_id, first.artifact_id)

    draft = repo.create_draft(family.family_id, first.artifact_id)
    draft.replace_content(b"edited")
    second = repo.save_revision(draft)

    assert second.parent_artifact_id == first.artifact_id
    assert repo.get_family(family.family_id).preferred_artifact_id == first.artifact_id
    repo.soft_delete_revision(first.artifact_id)
    assert repo.get_family(family.family_id).preferred_artifact_id is None
    with pytest.raises(ArtifactConflictError, match="live"):
        repo.set_preferred(family.family_id, first.artifact_id)
    repo.set_preferred(family.family_id, second.artifact_id)
    repo.set_preferred(family.family_id, None)
    assert repo.get_family(family.family_id).preferred_artifact_id is None


def test_save_validates_parent_family_and_artifact_input_refs(repo: ArtifactRepository):
    first_family = _family(repo, "asset_1")
    second_family = _family(repo, "asset_2")
    first = _save(repo, first_family.family_id, b"one")

    wrong_parent = repo.create_draft(second_family.family_id)
    wrong_parent.base_artifact_id = first.artifact_id
    wrong_parent.replace_content(b"bad")
    with pytest.raises(ArtifactConflictError, match="same Family"):
        repo.save_revision(wrong_parent)

    missing_input = repo.create_draft(second_family.family_id)
    missing_input.set_input_refs(
        [ArtifactInputRef("understanding", InputRefKind.ARTIFACT, "art_missing")]
    ).replace_content(b"bad")
    with pytest.raises(ArtifactNotFoundError, match="art_missing"):
        repo.save_revision(missing_input)


def test_soft_delete_restore_and_content_resolution(repo: ArtifactRepository):
    family = _family(repo)
    revision = _save(repo, family.family_id, b'{"summary":"grounded"}', storage=StorageMode.FILE)

    assert repo.resolve_content(revision.artifact_id).read_bytes() == b'{"summary":"grounded"}'
    repo.soft_delete_revision(revision.artifact_id)
    assert repo.list_revisions(family.family_id) == ()
    assert repo.list_revisions(family.family_id, include_deleted=True)[0].deleted_at is not None
    with pytest.raises(ArtifactConflictError, match="live"):
        repo.resolve_content(revision.artifact_id)

    restored = repo.restore_revision(revision.artifact_id)
    assert restored.deleted_at is None
    assert repo.resolve_content(revision.artifact_id).read_bytes() == b'{"summary":"grounded"}'


def test_purge_is_blocked_by_dependents_and_external_references(tmp_path: Path):
    externally_referenced: set[str] = set()
    repo = ArtifactRepository(tmp_path, external_reference_checker=externally_referenced.__contains__)
    source_family = _family(repo, "asset_1", "video_understanding")
    plan_family = repo.get_or_create_family(ArtifactOwner.project("proj_1"), "edit_plan")
    source = _save(repo, source_family.family_id, b"source")
    plan_draft = repo.create_draft(plan_family.family_id)
    plan_draft.set_input_refs(
        [ArtifactInputRef("understanding", InputRefKind.ARTIFACT, source.artifact_id)]
    ).replace_content(b"plan")
    plan = repo.save_revision(plan_draft)

    assert repo.list_dependencies(plan.artifact_id) == (source.artifact_id,)
    assert repo.list_dependents(source.artifact_id) == (plan.artifact_id,)
    repo.soft_delete_revision(source.artifact_id)
    with pytest.raises(PurgeBlockedError, match="dependent"):
        repo.purge_revision(source.artifact_id)

    repo.soft_delete_revision(plan.artifact_id)
    repo.purge_revision(plan.artifact_id)
    externally_referenced.add(source.artifact_id)
    with pytest.raises(PurgeBlockedError, match="external"):
        repo.purge_revision(source.artifact_id)
    externally_referenced.clear()
    purged = repo.purge_revision(source.artifact_id)
    assert purged.purged_at is not None
    with pytest.raises(ArtifactConflictError, match="purged"):
        repo.restore_revision(source.artifact_id)


def test_gc_keeps_shared_and_soft_deleted_content_then_sweeps_purged_blob(repo: ArtifactRepository):
    family = _family(repo)
    first = _save(repo, family.family_id, b"shared")
    second = _save(repo, family.family_id, b"shared")
    repo.soft_delete_revision(first.artifact_id)
    repo.purge_revision(first.artifact_id)

    assert repo.gc(dry_run=True).candidates == ()
    repo.soft_delete_revision(second.artifact_id)
    assert repo.gc(dry_run=True).candidates == ()
    repo.purge_revision(second.artifact_id)

    dry_run = repo.gc(dry_run=True)
    assert len(dry_run.candidates) == 1
    candidate_path = repo.storage_root / dry_run.candidates[0].path
    assert candidate_path.exists()
    swept = repo.gc(dry_run=False)
    assert swept.deleted_paths == (dry_run.candidates[0].path,)
    assert swept.deleted_bytes == len(b"shared")
    assert not candidate_path.exists()


def test_missing_records_raise_repository_errors(repo: ArtifactRepository):
    with pytest.raises(ArtifactNotFoundError):
        repo.get_family("af_missing")
    with pytest.raises(ArtifactNotFoundError):
        repo.get_revision("art_missing")
