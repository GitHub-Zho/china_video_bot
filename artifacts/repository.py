"""Filesystem-backed repository for immutable Artifact Schema v1 revisions."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Mapping

from .drafts import WorkingDraft
from .models import (
    ArtifactFamily,
    ArtifactInputRef,
    ArtifactOwner,
    ArtifactRevision,
    InputRefKind,
    StorageMode,
    normalize_variant_key,
)
from .storage import FileContentStore, GcCandidate


class ArtifactNotFoundError(LookupError):
    """Raised when a requested Family or Revision does not exist."""


class ArtifactConflictError(RuntimeError):
    """Raised when an operation would violate the Artifact v1 contract."""


class PurgeBlockedError(ArtifactConflictError):
    """Raised when a Revision remains referenced and cannot be purged."""


@dataclass(frozen=True)
class GarbageCollectionReport:
    dry_run: bool
    candidates: tuple[GcCandidate, ...]
    candidate_bytes: int
    deleted_paths: tuple[str, ...] = ()
    deleted_bytes: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactRepository:
    """Persist Families, Revisions, drafts, dependencies, and local content."""

    _INDEX_SCHEMA = "artifact_repository_index.v1"

    def __init__(
        self,
        storage_root: Path | str,
        *,
        external_reference_checker: Callable[[str], bool] | None = None,
    ) -> None:
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._records_root = self.storage_root / ".artifact-repository"
        self._families_root = self._records_root / "families"
        self._revisions_root = self._records_root / "revisions"
        self._locks_root = self._records_root / "locks"
        for directory in (
            self._records_root,
            self._families_root,
            self._revisions_root,
            self._locks_root / "families",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._index_path = self._records_root / "index.json"
        self._global_lock_path = self._locks_root / "repository.lock"
        self._storage = FileContentStore(self.storage_root)
        self._external_reference_checker = external_reference_checker

    def get_or_create_family(
        self,
        owner: ArtifactOwner,
        artifact_type: str,
        variant_key: str | None = None,
        display_name: str | None = None,
    ) -> ArtifactFamily:
        if not isinstance(owner, ArtifactOwner):
            raise ValueError("owner must be an ArtifactOwner")
        if not isinstance(artifact_type, str) or not artifact_type.strip():
            raise ValueError("artifact_type must be a non-empty string")
        normalized_variant = normalize_variant_key(variant_key)
        identity = self._identity_key(owner, artifact_type, normalized_variant)
        with self._lock(self._global_lock_path):
            index = self._read_index()
            existing_id = index["families"].get(identity)
            if existing_id is not None:
                return self.get_family(existing_id)

            timestamp = _now()
            family = ArtifactFamily(
                family_id=self._new_id("af"),
                owner=owner,
                artifact_type=artifact_type,
                variant_key=normalized_variant,
                display_name=display_name or artifact_type.replace("_", " ").title(),
                preferred_artifact_id=None,
                next_revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._write_json(self._family_path(family.family_id), family.to_dict())
            index["families"][identity] = family.family_id
            self._write_json(self._index_path, index)
            return family

    def get_family(self, family_id: str) -> ArtifactFamily:
        path = self._family_path(family_id)
        if not path.is_file():
            raise ArtifactNotFoundError(f"Family not found: {family_id}")
        return ArtifactFamily.from_dict(self._read_json(path))

    def find_family(
        self,
        owner: ArtifactOwner,
        artifact_type: str,
        variant_key: str | None = None,
    ) -> ArtifactFamily | None:
        identity = self._identity_key(owner, artifact_type, normalize_variant_key(variant_key))
        family_id = self._read_index()["families"].get(identity)
        return None if family_id is None else self.get_family(family_id)

    def list_families(self, owner: ArtifactOwner) -> tuple[ArtifactFamily, ...]:
        if not isinstance(owner, ArtifactOwner):
            raise ValueError("owner must be an ArtifactOwner")
        families = [
            self.get_family(family_id)
            for family_id in self._read_index()["families"].values()
        ]
        matching = [family for family in families if family.owner == owner]
        return tuple(sorted(matching, key=lambda family: (family.created_at, family.family_id)))

    def create_draft(
        self, family_id: str, base_artifact_id: str | None = None
    ) -> WorkingDraft:
        self.get_family(family_id)
        if base_artifact_id is None:
            return WorkingDraft(family_id=family_id)
        revision = self.get_revision(base_artifact_id)
        if revision.family_id != family_id:
            raise ArtifactConflictError("base Revision must belong to the same Family")
        if revision.deleted_at is not None or revision.purged_at is not None:
            raise ArtifactConflictError("base Revision must be live")
        content = self._storage.resolve(revision.content_ref).read_bytes()
        return WorkingDraft.from_revision(revision, content)

    def save_revision(self, draft: WorkingDraft) -> ArtifactRevision:
        if not isinstance(draft, WorkingDraft):
            raise ValueError("draft must be a WorkingDraft")
        snapshot = draft.snapshot()
        if snapshot.content is None:
            raise ArtifactConflictError("draft content is required before Save Version")

        with self._lock(self._global_lock_path):
            with self._lock(self._family_lock_path(snapshot.family_id)):
                family = self.get_family(snapshot.family_id)
                self._validate_revision_links(
                    family.family_id, snapshot.base_artifact_id, snapshot.input_refs
                )
                revision_number = family.next_revision
                reserved_family = replace(
                    family,
                    next_revision=revision_number + 1,
                    updated_at=_now(),
                )
                # Reserve first: a failed save may skip a number but never reuse it.
                self._write_json(
                    self._family_path(family.family_id), reserved_family.to_dict()
                )

                if snapshot.storage is StorageMode.FILE:
                    file_path = self._file_content_path(
                        family.family_id, revision_number, snapshot.media_type
                    )
                else:
                    file_path = None
                content_ref = self._storage.persist(
                    snapshot.content,
                    snapshot.storage,
                    snapshot.media_type,
                    file_path=file_path,
                )
                revision = ArtifactRevision(
                    artifact_id=self._new_id("art"),
                    family_id=family.family_id,
                    artifact_type=family.artifact_type,
                    revision=revision_number,
                    payload_schema_version=snapshot.payload_schema_version,
                    parent_artifact_id=snapshot.base_artifact_id,
                    created_at=_now(),
                    producer=snapshot.producer,
                    input_refs=snapshot.input_refs,
                    content_ref=content_ref,
                    metadata=snapshot.metadata,
                )
                self._write_json(
                    self._revision_path(revision.artifact_id), revision.to_dict()
                )
                return revision

    def get_revision(self, artifact_id: str) -> ArtifactRevision:
        path = self._revision_path(artifact_id)
        if not path.is_file():
            raise ArtifactNotFoundError(f"Revision not found: {artifact_id}")
        return ArtifactRevision.from_dict(self._read_json(path))

    def list_revisions(
        self, family_id: str, include_deleted: bool = False
    ) -> tuple[ArtifactRevision, ...]:
        self.get_family(family_id)
        revisions = [
            ArtifactRevision.from_dict(self._read_json(path))
            for path in self._revisions_root.glob("art_*.json")
        ]
        matching = [
            revision
            for revision in revisions
            if revision.family_id == family_id
            and revision.purged_at is None
            and (include_deleted or revision.deleted_at is None)
        ]
        return tuple(sorted(matching, key=lambda revision: revision.revision))

    def set_preferred(
        self, family_id: str, artifact_id_or_null: str | None
    ) -> ArtifactFamily:
        with self._lock(self._family_lock_path(family_id)):
            family = self.get_family(family_id)
            if artifact_id_or_null is not None:
                revision = self.get_revision(artifact_id_or_null)
                if revision.family_id != family_id:
                    raise ArtifactConflictError(
                        "preferred Revision must belong to the same Family"
                    )
                if revision.deleted_at is not None or revision.purged_at is not None:
                    raise ArtifactConflictError("preferred Revision must be live")
            updated = replace(
                family,
                preferred_artifact_id=artifact_id_or_null,
                updated_at=_now(),
            )
            self._write_json(self._family_path(family_id), updated.to_dict())
            return updated

    def soft_delete_revision(self, artifact_id: str) -> ArtifactRevision:
        revision = self.get_revision(artifact_id)
        with self._lock(self._family_lock_path(revision.family_id)):
            revision = self.get_revision(artifact_id)
            if revision.purged_at is not None:
                raise ArtifactConflictError("purged Revision cannot be deleted")
            if revision.deleted_at is None:
                revision = replace(revision, deleted_at=_now())
                self._write_json(self._revision_path(artifact_id), revision.to_dict())
            family = self.get_family(revision.family_id)
            if family.preferred_artifact_id == artifact_id:
                family = replace(
                    family, preferred_artifact_id=None, updated_at=_now()
                )
                self._write_json(self._family_path(family.family_id), family.to_dict())
            return revision

    def restore_revision(self, artifact_id: str) -> ArtifactRevision:
        revision = self.get_revision(artifact_id)
        with self._lock(self._family_lock_path(revision.family_id)):
            revision = self.get_revision(artifact_id)
            if revision.purged_at is not None:
                raise ArtifactConflictError("purged Revision cannot be restored")
            if revision.deleted_at is not None:
                revision = replace(revision, deleted_at=None)
                self._write_json(self._revision_path(artifact_id), revision.to_dict())
            return revision

    def purge_revision(self, artifact_id: str) -> ArtifactRevision:
        with self._lock(self._global_lock_path):
            revision = self.get_revision(artifact_id)
            with self._lock(self._family_lock_path(revision.family_id)):
                revision = self.get_revision(artifact_id)
                if revision.purged_at is not None:
                    raise ArtifactConflictError("Revision is already purged")
                if revision.deleted_at is None:
                    raise ArtifactConflictError("Revision must be soft deleted before purge")
                family = self.get_family(revision.family_id)
                if family.preferred_artifact_id == artifact_id:
                    raise PurgeBlockedError("preferred Revision cannot be purged")
                dependents = self.list_dependents(artifact_id)
                if dependents:
                    raise PurgeBlockedError(
                        "Revision has dependent Artifacts and cannot be purged"
                    )
                if (
                    self._external_reference_checker is not None
                    and self._external_reference_checker(artifact_id)
                ):
                    raise PurgeBlockedError(
                        "Revision has an external reference and cannot be purged"
                    )
                revision = replace(revision, purged_at=_now())
                self._write_json(self._revision_path(artifact_id), revision.to_dict())
                return revision

    def resolve_content(self, artifact_id: str) -> Path:
        revision = self.get_revision(artifact_id)
        if revision.deleted_at is not None or revision.purged_at is not None:
            raise ArtifactConflictError("Revision must be live to resolve content")
        return self._storage.resolve(revision.content_ref)

    def list_dependencies(self, artifact_id: str) -> tuple[str, ...]:
        revision = self.get_revision(artifact_id)
        dependencies: list[str] = []
        if revision.parent_artifact_id is not None:
            dependencies.append(revision.parent_artifact_id)
        dependencies.extend(
            ref.id for ref in revision.input_refs if ref.kind is InputRefKind.ARTIFACT
        )
        return tuple(dict.fromkeys(dependencies))

    def list_dependents(self, artifact_id: str) -> tuple[str, ...]:
        dependents: list[str] = []
        for path in self._revisions_root.glob("art_*.json"):
            revision = ArtifactRevision.from_dict(self._read_json(path))
            if revision.purged_at is None and artifact_id in self.list_dependencies(
                revision.artifact_id
            ):
                dependents.append(revision.artifact_id)
        return tuple(sorted(dependents))

    def gc(self, dry_run: bool = True) -> GarbageCollectionReport:
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        with self._lock(self._global_lock_path):
            reachable = self._reachable_content_paths()
            candidates = tuple(
                candidate
                for candidate in self._storage.list_managed_content()
                if candidate.path not in reachable
            )
            candidate_bytes = sum(candidate.size_bytes for candidate in candidates)
            if dry_run:
                return GarbageCollectionReport(True, candidates, candidate_bytes)

            deleted_paths: list[str] = []
            deleted_bytes = 0
            for candidate in candidates:
                path = self._candidate_path(candidate)
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                deleted_paths.append(candidate.path)
                deleted_bytes += candidate.size_bytes
                self._remove_empty_parents(path.parent)
            return GarbageCollectionReport(
                False,
                candidates,
                candidate_bytes,
                tuple(deleted_paths),
                deleted_bytes,
            )

    def _validate_revision_links(
        self,
        family_id: str,
        parent_artifact_id: str | None,
        input_refs: tuple[ArtifactInputRef, ...],
    ) -> None:
        if parent_artifact_id is not None:
            parent = self.get_revision(parent_artifact_id)
            if parent.family_id != family_id:
                raise ArtifactConflictError("parent Revision must belong to the same Family")
            if parent.purged_at is not None:
                raise ArtifactConflictError("parent Revision is purged")
        for ref in input_refs:
            if ref.kind is not InputRefKind.ARTIFACT:
                continue
            referenced = self.get_revision(ref.id)
            if referenced.purged_at is not None:
                raise ArtifactConflictError(f"input Artifact is purged: {ref.id}")

    def _reachable_content_paths(self) -> set[str]:
        reachable: set[str] = set()
        for path in self._revisions_root.glob("art_*.json"):
            revision = ArtifactRevision.from_dict(self._read_json(path))
            if revision.purged_at is not None:
                continue
            if revision.content_ref.storage is StorageMode.FILE:
                reachable.add(revision.content_ref.path)
            else:
                digest = revision.content_ref.checksum.value
                reachable.add(f"blobs/sha256/{digest[:2]}/{digest}")
        return reachable

    def _candidate_path(self, candidate: GcCandidate) -> Path:
        portable = PurePosixPath(candidate.path)
        path = self.storage_root.joinpath(*portable.parts)
        try:
            path.resolve(strict=False).relative_to(self.storage_root)
        except ValueError as error:
            raise ArtifactConflictError("GC candidate escapes storage root") from error
        if path.is_symlink():
            raise ArtifactConflictError("GC candidate cannot be a symlink")
        return path

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self.storage_root:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    def _read_index(self) -> dict[str, object]:
        if not self._index_path.is_file():
            return {"schema_version": self._INDEX_SCHEMA, "families": {}}
        value = self._read_json(self._index_path)
        if value.get("schema_version") != self._INDEX_SCHEMA:
            raise ArtifactConflictError("unsupported repository index schema")
        families = value.get("families")
        if not isinstance(families, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in families.items()
        ):
            raise ArtifactConflictError("repository Family index is invalid")
        return value

    @staticmethod
    def _identity_key(
        owner: ArtifactOwner, artifact_type: str, variant_key: str | None
    ) -> str:
        return json.dumps(
            [owner.kind.value, owner.id, artifact_type, variant_key],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    def _family_path(self, family_id: str) -> Path:
        self._validate_record_id(family_id, "af")
        return self._families_root / f"{family_id}.json"

    def _revision_path(self, artifact_id: str) -> Path:
        self._validate_record_id(artifact_id, "art")
        return self._revisions_root / f"{artifact_id}.json"

    def _family_lock_path(self, family_id: str) -> Path:
        self._validate_record_id(family_id, "af")
        return self._locks_root / "families" / f"{family_id}.lock"

    @staticmethod
    def _validate_record_id(value: str, prefix: str) -> None:
        if (
            not isinstance(value, str)
            or not value.startswith(f"{prefix}_")
            or not value[len(prefix) + 1 :]
            or any(character not in "0123456789abcdef_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-" for character in value)
        ):
            raise ValueError(f"invalid {prefix} identifier")

    @staticmethod
    def _extension_for_media_type(media_type: str) -> str:
        return {
            "application/json": ".json",
            "video/mp4": ".mp4",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "audio/mpeg": ".mp3",
        }.get(media_type, ".bin")

    def _file_content_path(
        self, family_id: str, revision: int, media_type: str
    ) -> str:
        extension = self._extension_for_media_type(media_type)
        return f"files/families/{family_id}/v{revision:06d}{extension}"

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ArtifactConflictError(f"cannot read repository record: {path.name}") from error
        if not isinstance(value, dict):
            raise ArtifactConflictError(f"repository record is not an object: {path.name}")
        return value

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, object]) -> None:
        data = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    @contextmanager
    def _lock(path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
