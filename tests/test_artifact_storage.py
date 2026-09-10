import hashlib
from pathlib import Path

import pytest

from artifacts.models import StorageMode
from artifacts.storage import ContentIntegrityError, FileContentStore


def test_file_persistence_stores_exact_bytes_with_a_sha256_content_ref(tmp_path):
    """Catch persistence that hashes or writes a transformed byte sequence."""
    store = FileContentStore(tmp_path)
    data = b"exact\x00bytes\r\n"

    ref = store.persist(
        data,
        StorageMode.FILE,
        "application/octet-stream",
        Path("families/af_1/content/v001.bin"),
    )

    assert ref.path == "families/af_1/content/v001.bin"
    assert ref.size_bytes == 13
    assert ref.checksum.value == hashlib.sha256(data).hexdigest()
    assert store.resolve(ref).read_bytes() == data


def test_file_persistence_replaces_existing_content_without_leaving_temp_files(tmp_path):
    """Catch non-atomic persistence that leaves partial-write temp files behind."""
    store = FileContentStore(tmp_path)
    target = Path("families/af_1/content/v001.json")
    store.persist(b"old", StorageMode.FILE, "application/json", target)

    ref = store.persist(b"replacement", StorageMode.FILE, "application/json", target)

    assert store.resolve(ref).read_bytes() == b"replacement"
    assert list((tmp_path / "families/af_1/content").glob(".*.tmp")) == []


def test_blob_storage_deduplicates_identical_exact_bytes(tmp_path):
    """Catch blob writes that duplicate content or derive IDs from non-byte metadata."""
    store = FileContentStore(tmp_path)
    first = store.persist(b"same bytes", StorageMode.BLOB, "video/mp4")
    second = store.persist(b"same bytes", StorageMode.BLOB, "video/mp4")

    digest = hashlib.sha256(b"same bytes").hexdigest()
    assert first.blob_id == second.blob_id == f"blob_sha256_{digest}"
    assert first.checksum.value == digest
    assert store.resolve(first) == tmp_path / "blobs/sha256" / digest[:2] / digest
    assert store.resolve(first).read_bytes() == b"same bytes"


@pytest.mark.parametrize(
    "file_path",
    [None, Path("/outside.json"), Path("../outside.json"), Path("safe/../../outside.json"), r"safe\\outside.json"],
)
def test_file_storage_requires_a_portable_storage_root_relative_target(tmp_path, file_path):
    """Catch file targets that are absent, machine-specific, or escape storage root."""
    store = FileContentStore(tmp_path)

    with pytest.raises(ValueError):
        store.persist(b"content", StorageMode.FILE, "application/json", file_path)


def test_resolve_file_and_blob_content_from_their_stored_locations(tmp_path):
    """Catch resolution that only supports one storage mode or chooses the wrong path."""
    store = FileContentStore(tmp_path)
    file_ref = store.persist(
        b"file content", StorageMode.FILE, "text/plain", Path("files/note.txt")
    )
    blob_ref = store.persist(b"blob content", StorageMode.BLOB, "text/plain")

    assert store.resolve(file_ref).read_bytes() == b"file content"
    assert store.resolve(blob_ref).read_bytes() == b"blob content"


def test_resolve_rejects_missing_content(tmp_path):
    """Catch resolution that returns a nonexistent path as if content were available."""
    store = FileContentStore(tmp_path)
    ref = store.persist(b"original", StorageMode.FILE, "application/json", Path("one.json"))
    store.resolve(ref).unlink()

    with pytest.raises(ContentIntegrityError, match="missing"):
        store.resolve(ref)


def test_resolve_detects_tampered_content(tmp_path):
    """Catch resolution that trusts stale size/checksum metadata after bytes change."""
    store = FileContentStore(tmp_path)
    ref = store.persist(
        b"original",
        StorageMode.FILE,
        "application/json",
        Path("families/af_1/content/v001.json"),
    )
    store.resolve(ref).write_bytes(b"changed")

    with pytest.raises(ContentIntegrityError):
        store.resolve(ref)


def test_list_managed_content_returns_root_relative_paths_and_sizes(tmp_path):
    """Catch GC enumeration that omits files, leaks absolute paths, or counts temp files."""
    store = FileContentStore(tmp_path)
    file_ref = store.persist(b"file", StorageMode.FILE, "text/plain", Path("files/note.txt"))
    blob_ref = store.persist(b"blob", StorageMode.BLOB, "text/plain")

    candidates = store.list_managed_content()

    assert {(candidate.path, candidate.size_bytes) for candidate in candidates} == {
        (file_ref.path, 4),
        (f"blobs/sha256/{blob_ref.checksum.value[:2]}/{blob_ref.checksum.value}", 4),
    }


def test_list_managed_content_excludes_unrelated_root_files_but_keeps_valid_tmp_content(tmp_path):
    """Catch GC enumeration that claims non-content files or hides valid `.tmp` content."""
    store = FileContentStore(tmp_path)
    content_ref = store.persist(
        b"content", StorageMode.FILE, "text/plain", Path("files/result.tmp")
    )
    (tmp_path / "repository-index.json").write_text("not managed")
    (tmp_path / ".content-interrupted.tmp").write_bytes(b"partial")

    candidates = store.list_managed_content()

    assert [(candidate.path, candidate.size_bytes) for candidate in candidates] == [
        (content_ref.path, 7)
    ]


def test_resolve_detects_a_same_size_checksum_mismatch(tmp_path):
    """Catch integrity checks that only compare byte counts."""
    store = FileContentStore(tmp_path)
    ref = store.persist(b"original", StorageMode.FILE, "application/json", Path("one.json"))
    store.resolve(ref).write_bytes(b"tampered")

    with pytest.raises(ContentIntegrityError, match="checksum"):
        store.resolve(ref)


def test_resolve_classifies_an_outward_pointing_symlink_as_content_tampering(tmp_path):
    """Catch symlink replacement escaping storage root as a validation error instead of tampering."""
    store = FileContentStore(tmp_path)
    ref = store.persist(b"original", StorageMode.FILE, "application/json", Path("one.json"))
    stored_path = store.resolve(ref)
    external_path = tmp_path.parent / "external.json"
    external_path.write_bytes(b"external")
    stored_path.unlink()
    stored_path.symlink_to(external_path)

    with pytest.raises(ContentIntegrityError):
        store.resolve(ref)
