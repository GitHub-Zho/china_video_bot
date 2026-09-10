"""Checksummed local content storage for Artifact Schema v1."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from .models import Checksum, ContentRef, StorageMode


class ContentIntegrityError(RuntimeError):
    """Raised when persisted content is missing or differs from its reference."""


@dataclass(frozen=True)
class GcCandidate:
    """A managed content file represented relative to its storage root."""

    path: str
    size_bytes: int

    def __post_init__(self) -> None:
        ContentRef._validate_relative_path(self.path)
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")


class ContentStore(Protocol):
    """The content layer consumed by the filesystem artifact repository."""

    def persist(
        self,
        data: bytes,
        storage: StorageMode,
        media_type: str,
        file_path: Path | str | None = None,
    ) -> ContentRef: ...

    def resolve(self, content_ref: ContentRef) -> Path: ...

    def list_managed_content(self) -> list[GcCandidate]: ...


class FileContentStore:
    """Persist exact bytes below one dedicated, local storage root."""

    _BLOB_PREFIX = "blob_sha256_"

    def __init__(self, storage_root: Path | str) -> None:
        self.root = Path(storage_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("storage_root must be a directory")

    def persist(
        self,
        data: bytes,
        storage: StorageMode,
        media_type: str,
        file_path: Path | str | None = None,
    ) -> ContentRef:
        """Atomically persist exact bytes and return the corresponding content reference."""
        if not isinstance(data, bytes):
            raise ValueError("data must be bytes")
        if not isinstance(storage, StorageMode):
            raise ValueError("storage must be a StorageMode")

        checksum = Checksum("sha256", hashlib.sha256(data).hexdigest())
        if storage is StorageMode.FILE:
            if file_path is None:
                raise ValueError("file storage requires file_path")
            content_ref = ContentRef.file(file_path, media_type, len(data), checksum)
            destination = self._path_for_relative(content_ref.path)
        else:
            if file_path is not None:
                raise ValueError("blob storage does not accept file_path")
            blob_id = f"{self._BLOB_PREFIX}{checksum.value}"
            content_ref = ContentRef.blob(blob_id, media_type, len(data), checksum)
            destination = self._blob_path(checksum.value)

        self._atomic_write(destination, data)
        return content_ref

    def resolve(self, content_ref: ContentRef) -> Path:
        """Return a content path only after checking its exact size and SHA-256 digest."""
        if not isinstance(content_ref, ContentRef):
            raise ValueError("content_ref must be a ContentRef")

        if content_ref.storage is StorageMode.FILE:
            path = self._path_for_relative(content_ref.path)
        else:
            expected_blob_id = f"{self._BLOB_PREFIX}{content_ref.checksum.value}"
            if content_ref.blob_id != expected_blob_id:
                raise ContentIntegrityError("blob identifier does not match checksum")
            path = self._blob_path(content_ref.checksum.value)

        self._verify(path, content_ref)
        return path

    def list_managed_content(self) -> list[GcCandidate]:
        """List regular content files as portable paths relative to ``storage_root``."""
        candidates: list[GcCandidate] = []
        for path in self.root.rglob("*"):
            if path.is_symlink() or not path.is_file() or path.name.endswith(".tmp"):
                continue
            relative = path.relative_to(self.root).as_posix()
            candidates.append(GcCandidate(relative, path.stat().st_size))
        return sorted(candidates, key=lambda candidate: candidate.path)

    def _blob_path(self, digest: str) -> Path:
        return self._path_for_relative(f"blobs/sha256/{digest[:2]}/{digest}")

    def _path_for_relative(self, relative_path: str | Path | None) -> Path:
        if relative_path is None:
            raise ValueError("path must be relative")
        portable_path = ContentRef._validate_relative_path(relative_path)
        path = self.root.joinpath(*PurePosixPath(portable_path).parts)
        self._ensure_under_root(path)
        return path

    def _ensure_under_root(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.root)
        except ValueError as error:
            raise ValueError("path must stay under storage_root") from error

    def _atomic_write(self, destination: Path, data: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_under_root(destination.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent, prefix=".content-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, destination)
            self._fsync_directory(destination.parent)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _verify(path: Path, content_ref: ContentRef) -> None:
        if path.is_symlink() or not path.is_file():
            raise ContentIntegrityError("managed content is missing")
        if path.stat().st_size != content_ref.size_bytes:
            raise ContentIntegrityError("managed content size does not match content reference")

        digest = hashlib.sha256()
        with path.open("rb") as content_file:
            for chunk in iter(lambda: content_file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != content_ref.checksum.value:
            raise ContentIntegrityError("managed content checksum does not match content reference")
