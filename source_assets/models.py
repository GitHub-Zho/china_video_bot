"""Stable local Source Asset records for staged media workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping
from urllib.parse import SplitResult, urlsplit, urlunsplit
import re


_BILIBILI_VIDEO = re.compile(r"^/video/(BV[0-9A-Za-z]+)(?:/.*)?$")


def canonicalize_source_uri(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_uri must be a non-empty HTTP(S) URI")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_uri must be a valid HTTP(S) URI")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_uri must not contain credentials")

    hostname = parsed.hostname.lower()
    bili_match = _BILIBILI_VIDEO.match(parsed.path)
    if hostname in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"} and bili_match:
        return f"https://www.bilibili.com/video/{bili_match.group(1)}/"

    port = parsed.port
    if port is None or (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        netloc = hostname
    else:
        netloc = f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit(
        SplitResult(parsed.scheme.lower(), netloc, path, parsed.query, "")
    )


def _text(value: object, field_name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class SourceAssetV1:
    asset_id: str
    asset_kind: str
    source_uri: str
    display_name: str | None
    created_at: str
    updated_at: str

    SCHEMA_VERSION: ClassVar[str] = "source_asset.v1"

    def __post_init__(self) -> None:
        asset_id = _text(self.asset_id, "asset_id")
        if not asset_id.startswith("asset_"):
            raise ValueError("asset_id must be an opaque asset_ identifier")
        if self.asset_kind != "remote_video":
            raise ValueError("asset_kind must be remote_video")
        object.__setattr__(self, "asset_id", asset_id)
        object.__setattr__(self, "source_uri", canonicalize_source_uri(self.source_uri))
        object.__setattr__(
            self, "display_name", _text(self.display_name, "display_name", nullable=True)
        )
        object.__setattr__(self, "created_at", _text(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _text(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "asset_id": self.asset_id,
            "asset_kind": self.asset_kind,
            "source_uri": self.source_uri,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SourceAssetV1":
        if not isinstance(value, Mapping):
            raise ValueError("source_asset.v1 must be a mapping")
        expected = {
            "schema_version",
            "asset_id",
            "asset_kind",
            "source_uri",
            "display_name",
            "created_at",
            "updated_at",
        }
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        if missing:
            raise ValueError(f"source_asset.v1 missing required fields: {', '.join(missing)}")
        if unknown:
            raise ValueError(f"source_asset.v1 has unknown fields: {', '.join(unknown)}")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {cls.SCHEMA_VERSION}")
        return cls(
            asset_id=value["asset_id"],
            asset_kind=value["asset_kind"],
            source_uri=value["source_uri"],
            display_name=value["display_name"],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
        )

