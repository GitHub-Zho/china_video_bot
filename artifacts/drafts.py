"""Mutable in-memory drafts that are saved as new immutable Artifact revisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import (
    ArtifactInputRef,
    ArtifactProducer,
    ArtifactRevision,
    ProducerKind,
    StorageMode,
    copy_metadata,
    freeze_metadata,
)


def _require_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _copy_content(value: bytes | bytearray | memoryview | None) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("content must be bytes")
    return bytes(value)


@dataclass(frozen=True)
class DraftSnapshot:
    """Detached state a repository can consume to create one formal Revision."""

    family_id: str
    base_artifact_id: str | None
    payload_schema_version: str
    producer: ArtifactProducer
    input_refs: tuple[ArtifactInputRef, ...]
    metadata: Mapping[str, Any]
    content: bytes | None
    media_type: str
    storage: StorageMode


@dataclass
class WorkingDraft:
    """Editable payload and provenance; it never mutates a saved Revision."""

    family_id: str
    base_artifact_id: str | None = None
    payload_schema_version: str = "artifact.v1"
    producer: ArtifactProducer = field(
        default_factory=lambda: ArtifactProducer(kind=ProducerKind.HUMAN)
    )
    input_refs: tuple[ArtifactInputRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    content: bytes | bytearray | memoryview | None = None
    media_type: str = "application/octet-stream"
    storage: StorageMode = StorageMode.BLOB

    def __post_init__(self) -> None:
        _require_id(self.family_id, "family_id")
        if self.base_artifact_id is not None:
            _require_id(self.base_artifact_id, "base_artifact_id")
        _require_id(self.payload_schema_version, "payload_schema_version")
        if not isinstance(self.producer, ArtifactProducer):
            raise ValueError("producer must be an ArtifactProducer")
        refs = tuple(self.input_refs)
        if any(not isinstance(ref, ArtifactInputRef) for ref in refs):
            raise ValueError("input_refs must contain ArtifactInputRef values")
        self.input_refs = refs
        self.metadata = copy_metadata(self.metadata)
        self.content = _copy_content(self.content)
        _require_id(self.media_type, "media_type")
        if not isinstance(self.storage, StorageMode):
            raise ValueError("storage must be a StorageMode")

    @classmethod
    def from_revision(
        cls, revision: ArtifactRevision, content: bytes | bytearray | memoryview | None = None
    ) -> "WorkingDraft":
        """Start an editable successor without changing the supplied Revision."""
        if not isinstance(revision, ArtifactRevision):
            raise ValueError("revision must be an ArtifactRevision")
        return cls(
            family_id=revision.family_id,
            base_artifact_id=revision.artifact_id,
            payload_schema_version=revision.payload_schema_version,
            producer=revision.producer,
            input_refs=revision.input_refs,
            metadata=revision.metadata,
            content=content,
            media_type=revision.content_ref.media_type,
            storage=revision.content_ref.storage,
        )

    def replace_content(
        self,
        content: bytes | bytearray | memoryview,
        media_type: str | None = None,
        storage: StorageMode | None = None,
    ) -> "WorkingDraft":
        """Replace only this draft's bytes and optionally its content preferences."""
        self.content = _copy_content(content)
        if media_type is not None:
            _require_id(media_type, "media_type")
            self.media_type = media_type
        if storage is not None:
            if not isinstance(storage, StorageMode):
                raise ValueError("storage must be a StorageMode")
            self.storage = storage
        return self

    def set_metadata(self, metadata: Mapping[str, Any]) -> "WorkingDraft":
        """Replace draft metadata with a detached JSON-safe mapping."""
        self.metadata = copy_metadata(metadata)
        return self

    def set_input_refs(self, input_refs: tuple[ArtifactInputRef, ...] | list[ArtifactInputRef]) -> "WorkingDraft":
        """Replace draft input references with a detached immutable sequence."""
        refs = tuple(input_refs)
        if any(not isinstance(ref, ArtifactInputRef) for ref in refs):
            raise ValueError("input_refs must contain ArtifactInputRef values")
        self.input_refs = refs
        return self

    def snapshot(self) -> DraftSnapshot:
        """Return detached, immutable state for a single repository save attempt."""
        return DraftSnapshot(
            family_id=self.family_id,
            base_artifact_id=self.base_artifact_id,
            payload_schema_version=self.payload_schema_version,
            producer=self.producer,
            input_refs=tuple(self.input_refs),
            metadata=freeze_metadata(self.metadata),
            content=_copy_content(self.content),
            media_type=self.media_type,
            storage=self.storage,
        )
