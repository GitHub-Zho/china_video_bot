"""Immutable value models for the locked Artifact Schema v1 contract."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class OwnerKind(str, Enum):
    ASSET = "asset"
    PROJECT = "project"


class ProducerKind(str, Enum):
    EXECUTOR = "executor"
    HUMAN = "human"
    IMPORT = "import"
    MIGRATION = "migration"


class InputRefKind(str, Enum):
    ASSET = "asset"
    ARTIFACT = "artifact"


class StorageMode(str, Enum):
    FILE = "file"
    BLOB = "blob"


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _require_optional_identifier(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_identifier(value, field_name)


def normalize_variant_key(value: str | None) -> str | None:
    """Return the v1 Family variant identity value, or ``None`` when blank."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("variant_key must be a string or None")
    normalized = unicodedata.normalize("NFKC", value).strip()
    return normalized or None


def _copy_json_metadata(value: object) -> object:
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("metadata must contain JSON-serializable values") from error


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def copy_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached JSON-safe metadata copy for mutable draft state."""
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    copied = _copy_json_metadata(_thaw_json(dict(value)))
    if not isinstance(copied, dict):  # Defensive, because the input was a mapping.
        raise ValueError("metadata must be a mapping")
    return copied


def freeze_metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a recursively immutable, detached JSON-safe metadata mapping."""
    return _freeze_json(copy_metadata(value))  # type: ignore[return-value]


@dataclass(frozen=True)
class ArtifactOwner:
    kind: OwnerKind
    id: str

    def __post_init__(self) -> None:
        _require_enum(self.kind, OwnerKind, "kind")
        _require_identifier(self.id, "owner id")

    @classmethod
    def asset(cls, id: str) -> "ArtifactOwner":
        return cls(OwnerKind.ASSET, id)

    @classmethod
    def project(cls, id: str) -> "ArtifactOwner":
        return cls(OwnerKind.PROJECT, id)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "id": self.id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ArtifactOwner":
        _require_mapping(value, "owner")
        return cls(OwnerKind(value.get("kind")), value.get("id"))


@dataclass(frozen=True)
class ArtifactProducer:
    kind: ProducerKind
    executor_id: str | None = None
    project_id: str | None = None
    stage_run_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.kind, ProducerKind, "kind")
        executor_id = _require_optional_identifier(self.executor_id, "executor_id")
        if self.kind is ProducerKind.EXECUTOR and executor_id is None:
            raise ValueError("executor_id is required for executor producers")
        _require_optional_identifier(self.project_id, "project_id")
        _require_optional_identifier(self.stage_run_id, "stage_run_id")

    def to_dict(self) -> dict[str, str]:
        result = {"kind": self.kind.value}
        if self.executor_id is not None:
            result["executor_id"] = self.executor_id
        if self.project_id is not None:
            result["project_id"] = self.project_id
        if self.stage_run_id is not None:
            result["stage_run_id"] = self.stage_run_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ArtifactProducer":
        _require_mapping(value, "producer")
        return cls(
            kind=ProducerKind(value.get("kind")),
            executor_id=value.get("executor_id"),
            project_id=value.get("project_id"),
            stage_run_id=value.get("stage_run_id"),
        )


@dataclass(frozen=True)
class ArtifactInputRef:
    slot: str
    kind: InputRefKind
    id: str

    def __post_init__(self) -> None:
        _require_identifier(self.slot, "slot")
        _require_enum(self.kind, InputRefKind, "kind")
        _require_identifier(self.id, "input reference id")

    def to_dict(self) -> dict[str, str]:
        return {"slot": self.slot, "kind": self.kind.value, "id": self.id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ArtifactInputRef":
        _require_mapping(value, "input reference")
        return cls(value.get("slot"), InputRefKind(value.get("kind")), value.get("id"))


@dataclass(frozen=True)
class Checksum:
    algorithm: str
    value: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise ValueError("algorithm must be sha256")
        if not isinstance(self.value, str) or not _SHA256_PATTERN.fullmatch(self.value):
            raise ValueError("sha256 checksum must be 64 lowercase hexadecimal characters")

    def to_dict(self) -> dict[str, str]:
        return {"algorithm": self.algorithm, "value": self.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Checksum":
        _require_mapping(value, "checksum")
        return cls(value.get("algorithm"), value.get("value"))


@dataclass(frozen=True)
class ContentRef:
    storage: StorageMode
    media_type: str
    size_bytes: int
    checksum: Checksum
    path: str | None = None
    blob_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.storage, StorageMode, "storage")
        _require_identifier(self.media_type, "media_type")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if not isinstance(self.checksum, Checksum):
            raise ValueError("checksum must be a Checksum")

        if self.storage is StorageMode.FILE:
            if self.blob_id is not None:
                raise ValueError("file storage requires exactly path, not blob_id")
            if self.path is None:
                raise ValueError("file storage requires exactly path")
            normalized_path = self._validate_relative_path(self.path)
            object.__setattr__(self, "path", normalized_path)
            return

        if self.path is not None:
            raise ValueError("blob storage requires exactly blob_id, not path")
        if self.blob_id is None:
            raise ValueError("blob storage requires exactly blob_id")
        _require_identifier(self.blob_id, "blob_id")

    @staticmethod
    def _validate_relative_path(value: object) -> str:
        if not isinstance(value, (str, Path)):
            raise ValueError("path must be a relative path")
        if isinstance(value, str) and not value.strip():
            raise ValueError("path must be a concrete relative path")
        path = Path(value)
        if path == Path(".") or path.is_absolute():
            raise ValueError("path must be relative")
        if ".." in path.parts:
            raise ValueError("path must not contain ..")
        return path.as_posix()

    @classmethod
    def file(
        cls,
        path: str | Path,
        media_type: str,
        size_bytes: int,
        checksum: Checksum,
    ) -> "ContentRef":
        return cls(StorageMode.FILE, media_type, size_bytes, checksum, path=path)

    @classmethod
    def blob(
        cls,
        blob_id: str,
        media_type: str,
        size_bytes: int,
        checksum: Checksum,
    ) -> "ContentRef":
        return cls(StorageMode.BLOB, media_type, size_bytes, checksum, blob_id=blob_id)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "storage": self.storage.value,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum.to_dict(),
        }
        if self.storage is StorageMode.FILE:
            result["path"] = self.path
        else:
            result["blob_id"] = self.blob_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ContentRef":
        _require_mapping(value, "content_ref")
        checksum_value = value.get("checksum")
        if not isinstance(checksum_value, Mapping):
            raise ValueError("checksum must be a mapping")
        return cls(
            storage=StorageMode(value.get("storage")),
            media_type=value.get("media_type"),
            size_bytes=value.get("size_bytes"),
            checksum=Checksum.from_dict(checksum_value),
            path=value.get("path"),
            blob_id=value.get("blob_id"),
        )


@dataclass(frozen=True)
class ArtifactFamily:
    family_id: str
    owner: ArtifactOwner
    artifact_type: str
    variant_key: str | None
    display_name: str
    preferred_artifact_id: str | None
    next_revision: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _require_identifier(self.family_id, "family_id")
        if not isinstance(self.owner, ArtifactOwner):
            raise ValueError("owner must be an ArtifactOwner")
        _require_identifier(self.artifact_type, "artifact_type")
        object.__setattr__(self, "variant_key", normalize_variant_key(self.variant_key))
        _require_identifier(self.display_name, "display_name")
        _require_optional_identifier(self.preferred_artifact_id, "preferred_artifact_id")
        if isinstance(self.next_revision, bool) or not isinstance(self.next_revision, int) or self.next_revision < 1:
            raise ValueError("next_revision must be a positive integer")
        _require_identifier(self.created_at, "created_at")
        _require_identifier(self.updated_at, "updated_at")

    @property
    def identity_key(self) -> tuple[OwnerKind, str, str, str | None]:
        return (self.owner.kind, self.owner.id, self.artifact_type, self.variant_key)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "artifact_family.v1",
            "family_id": self.family_id,
            "owner": self.owner.to_dict(),
            "artifact_type": self.artifact_type,
            "variant_key": self.variant_key,
            "display_name": self.display_name,
            "preferred_artifact_id": self.preferred_artifact_id,
            "next_revision": self.next_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ArtifactFamily":
        _require_schema(value, "artifact_family.v1")
        owner_value = value.get("owner")
        if not isinstance(owner_value, Mapping):
            raise ValueError("owner must be a mapping")
        return cls(
            family_id=value.get("family_id"),
            owner=ArtifactOwner.from_dict(owner_value),
            artifact_type=value.get("artifact_type"),
            variant_key=value.get("variant_key"),
            display_name=value.get("display_name"),
            preferred_artifact_id=value.get("preferred_artifact_id"),
            next_revision=value.get("next_revision"),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
        )


@dataclass(frozen=True)
class ArtifactRevision:
    artifact_id: str
    family_id: str
    artifact_type: str
    revision: int
    payload_schema_version: str
    parent_artifact_id: str | None
    created_at: str
    producer: ArtifactProducer
    input_refs: tuple[ArtifactInputRef, ...] = ()
    content_ref: ContentRef | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    deleted_at: str | None = None
    purged_at: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.artifact_id, "artifact_id")
        _require_identifier(self.family_id, "family_id")
        _require_identifier(self.artifact_type, "artifact_type")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        _require_identifier(self.payload_schema_version, "payload_schema_version")
        _require_optional_identifier(self.parent_artifact_id, "parent_artifact_id")
        _require_identifier(self.created_at, "created_at")
        if not isinstance(self.producer, ArtifactProducer):
            raise ValueError("producer must be an ArtifactProducer")
        refs = tuple(self.input_refs)
        if any(not isinstance(ref, ArtifactInputRef) for ref in refs):
            raise ValueError("input_refs must contain ArtifactInputRef values")
        object.__setattr__(self, "input_refs", refs)
        if not isinstance(self.content_ref, ContentRef):
            raise ValueError("content_ref must be a ContentRef")
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        _require_optional_identifier(self.deleted_at, "deleted_at")
        _require_optional_identifier(self.purged_at, "purged_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "artifact_revision.v1",
            "artifact_id": self.artifact_id,
            "family_id": self.family_id,
            "artifact_type": self.artifact_type,
            "revision": self.revision,
            "payload_schema_version": self.payload_schema_version,
            "parent_artifact_id": self.parent_artifact_id,
            "created_at": self.created_at,
            "producer": self.producer.to_dict(),
            "input_refs": [ref.to_dict() for ref in self.input_refs],
            "content_ref": self.content_ref.to_dict(),
            "metadata": _thaw_json(self.metadata),
            "deleted_at": self.deleted_at,
            "purged_at": self.purged_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ArtifactRevision":
        _require_schema(value, "artifact_revision.v1")
        producer_value = value.get("producer")
        content_value = value.get("content_ref")
        input_values = value.get("input_refs")
        if not isinstance(producer_value, Mapping):
            raise ValueError("producer must be a mapping")
        if not isinstance(content_value, Mapping):
            raise ValueError("content_ref must be a mapping")
        if not isinstance(input_values, list):
            raise ValueError("input_refs must be a list")
        if any(not isinstance(item, Mapping) for item in input_values):
            raise ValueError("input_refs must contain mappings")
        metadata = value.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        return cls(
            artifact_id=value.get("artifact_id"),
            family_id=value.get("family_id"),
            artifact_type=value.get("artifact_type"),
            revision=value.get("revision"),
            payload_schema_version=value.get("payload_schema_version"),
            parent_artifact_id=value.get("parent_artifact_id"),
            created_at=value.get("created_at"),
            producer=ArtifactProducer.from_dict(producer_value),
            input_refs=tuple(ArtifactInputRef.from_dict(item) for item in input_values),
            content_ref=ContentRef.from_dict(content_value),
            metadata=metadata,
            deleted_at=value.get("deleted_at"),
            purged_at=value.get("purged_at"),
        )


def _require_mapping(value: object, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")


def _require_schema(value: Mapping[str, object], schema_version: str) -> None:
    _require_mapping(value, "serialized value")
    if value.get("schema_version") != schema_version:
        raise ValueError(f"schema_version must be {schema_version}")
