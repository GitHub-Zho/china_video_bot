"""Atomic filesystem repository for stable Source Asset identities."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

from .models import SourceAssetV1, canonicalize_source_uri


class SourceAssetNotFoundError(LookupError):
    pass


class SourceAssetConflictError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceAssetRepository:
    _INDEX_SCHEMA = "source_asset_repository_index.v1"

    def __init__(self, storage_root: Path | str) -> None:
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._root = self.storage_root / ".source-assets"
        self._records = self._root / "records"
        self._locks = self._root / "locks"
        self._index_path = self._root / "index.json"
        self._lock_path = self._locks / "repository.lock"
        self._records.mkdir(parents=True, exist_ok=True)
        self._locks.mkdir(parents=True, exist_ok=True)

    def register_remote_video(
        self, source_uri: str, *, display_name: str | None = None
    ) -> SourceAssetV1:
        canonical = canonicalize_source_uri(source_uri)
        with self._lock(self._lock_path):
            index = self._read_index()
            existing = index["uris"].get(canonical)
            if existing is not None:
                return self.get(existing)
            timestamp = _now()
            asset = SourceAssetV1(
                asset_id=f"asset_{uuid.uuid4().hex}",
                asset_kind="remote_video",
                source_uri=canonical,
                display_name=display_name,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._write_json(self._record_path(asset.asset_id), asset.to_dict())
            index["uris"][canonical] = asset.asset_id
            self._write_json(self._index_path, index)
            return asset

    def get(self, asset_id: str) -> SourceAssetV1:
        path = self._record_path(asset_id)
        if not path.is_file():
            raise SourceAssetNotFoundError(f"Source Asset not found: {asset_id}")
        return SourceAssetV1.from_dict(self._read_json(path))

    def resolve(self, asset_id: str) -> str:
        return self.get(asset_id).source_uri

    def _read_index(self) -> dict[str, object]:
        if not self._index_path.is_file():
            return {"schema_version": self._INDEX_SCHEMA, "uris": {}}
        value = self._read_json(self._index_path)
        if value.get("schema_version") != self._INDEX_SCHEMA:
            raise SourceAssetConflictError("unsupported Source Asset index schema")
        uris = value.get("uris")
        if not isinstance(uris, dict) or any(
            not isinstance(uri, str) or not isinstance(asset_id, str)
            for uri, asset_id in uris.items()
        ):
            raise SourceAssetConflictError("Source Asset index is invalid")
        return value

    def _record_path(self, asset_id: str) -> Path:
        if (
            not isinstance(asset_id, str)
            or not asset_id.startswith("asset_")
            or not asset_id[6:]
            or not all(character.isalnum() or character in "_-" for character in asset_id)
        ):
            raise ValueError("invalid Source Asset identifier")
        return self._records / f"{asset_id}.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceAssetConflictError(
                f"cannot read Source Asset record: {path.name}"
            ) from exc
        if not isinstance(value, dict):
            raise SourceAssetConflictError("Source Asset record must be an object")
        return value

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, object]) -> None:
        data = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
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

