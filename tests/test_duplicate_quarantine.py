# SPDX-License-Identifier: GPL-3.0-or-later
"""Real local-filesystem quarantine journal and recovery regressions."""
import hashlib
import json
from pathlib import Path
import os

import pytest

from cps import duplicate_quarantine as q


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "calibre"
    book = root / "Author" / "Novel (42)"
    book.mkdir(parents=True)
    (book / "novel.epub").write_bytes(b"original EPUB bytes")
    (book / "cover.jpg").write_bytes(b"original cover bytes")
    (book / "metadata.opf").write_bytes(b"<package>metadata</package>")
    qroot = tmp_path / "maintenance" / "potentialDupes"
    qroot.mkdir(parents=True)
    monkeypatch.setenv(q.ENV, str(qroot))
    return root, book, qroot


def _published(library):
    root, _, _ = library
    return q.publish(root, "Author/Novel (42)", 42, "source-uuid", "keeper-uuid")


def test_duplicate_quarantine_retains_every_original_file(library):
    root, original, destination = library
    receipt = _published(library)
    final = Path(receipt["destination"])
    assert original.exists()  # DB has not committed yet
    assert sorted(x.name for x in final.iterdir()) == [
        q.MARKER, "cover.jpg", "metadata.opf", "novel.epub"
    ]
    expected = hashlib.sha256(b"original EPUB bytes").hexdigest()
    assert receipt["inventory"]["novel.epub"]["sha256"] == expected
    result = q.finish(receipt)
    assert result["state"] == "complete"
    assert not original.exists()
    assert final.joinpath("novel.epub").read_bytes() == b"original EPUB bytes"
    assert final.joinpath("cover.jpg").read_bytes() == b"original cover bytes"
    journal = json.loads(Path(receipt["journal"]).read_text())
    assert journal["state"] == "complete"


def test_read_only_quarantine_attestation_rejects_uncommitted_and_corrupt(library):
    root, original, _ = library
    receipt = _published(library)
    unpublished = q.inspect(root, 42, receipt["key"])
    assert unpublished["committed"] is False
    assert unpublished["state"] == "published"
    assert unpublished["files_verified"] == 3
    assert unpublished["source_uuid"] == "source-uuid"
    assert unpublished["keeper_uuid"] == "keeper-uuid"

    finished = q.finish(receipt)
    proof = q.inspect(root, 42, finished["quarantine_id"])
    assert proof["committed"] is True
    assert proof["state"] == "complete"
    assert proof["files_verified"] == 3
    with pytest.raises(q.QuarantineError, match="journal not found"):
        q.inspect(root, 999, receipt["key"])
    with pytest.raises(q.QuarantineError, match="invalid"):
        q.inspect(root, 42, "../bad")

    Path(receipt["destination"], "novel.epub").write_bytes(b"not-original")
    with pytest.raises(q.QuarantineError, match="differs"):
        q.inspect(root, 42, receipt["key"])


def test_crash_after_publishing_reuses_owned_quarantine(library):
    receipt = _published(library)
    again = _published(library)
    assert again["key"] == receipt["key"]
    assert again["destination"] == receipt["destination"]
    q.finish(again)
    q.finish(again)  # exact recorded cleanup is idempotent


def test_rollback_before_db_commit_preserves_original(library):
    _, original, _ = library
    receipt = _published(library)
    q.cancel(receipt)
    assert original.joinpath("novel.epub").read_bytes() == b"original EPUB bytes"
    assert not Path(receipt["destination"]).exists()
    assert json.loads(Path(receipt["journal"]).read_text())["state"] == "aborted"


def test_modified_source_before_cleanup_never_removed(library):
    _, original, _ = library
    receipt = _published(library)
    original.joinpath("novel.epub").write_bytes(b"changed data")
    with pytest.raises(q.QuarantineError, match="changed"):
        q.finish(receipt)
    assert original.exists()
    assert Path(receipt["destination"]).exists()


def test_quarantined_bytes_corruption_stops_cleanup(library):
    _, original, _ = library
    receipt = _published(library)
    Path(receipt["destination"], "novel.epub").write_bytes(b"corrupted")
    with pytest.raises(q.QuarantineError, match="differs"):
        q.finish(receipt)
    assert original.exists()


def test_rejects_path_traversal_and_nested_quarantine(library, monkeypatch):
    root, _, _ = library
    with pytest.raises(q.QuarantineError, match="path"):
        q.publish(root, "../Author/Novel (42)", 42, "source", "keeper")
    with pytest.raises(q.QuarantineError, match="path"):
        q.publish(root, "/Author/Novel (42)", 42, "source", "keeper")
    monkeypatch.setenv(q.ENV, str(root / "maintenance" / "potentialDupes"))
    with pytest.raises(q.QuarantineError, match="outside"):
        q.publish(root, "Author/Novel (42)", 42, "source", "keeper")


def test_missing_quarantine_mount_never_created_inside_container(library, monkeypatch):
    root, original, configured = library
    import shutil
    shutil.rmtree(configured)
    with pytest.raises(q.QuarantineError, match="existing persistent"):
        _published(library)
    assert not configured.exists()
    assert original.joinpath("novel.epub").is_file()

    link = configured.parent / "symlinked-potentialDupes"
    external = configured.parent / "other-volume"
    external.mkdir()
    link.symlink_to(external, target_is_directory=True)
    monkeypatch.setenv(q.ENV, str(link))
    with pytest.raises(q.QuarantineError, match="existing persistent"):
        _published(library)


def test_rejects_symlinked_external_file(library):
    root, book, _ = library
    extra = root.parent / "outside.txt"
    extra.write_text("private")
    book.joinpath("outside.txt").symlink_to(extra)
    with pytest.raises(q.QuarantineError, match="symlink"):
        _published(library)
    assert book.exists()


def test_unowned_destination_collision_is_never_overwritten(library):
    _, original, destination = library
    digest = hashlib.sha256("42|source-uuid|keeper-uuid|Author/Novel (42)".encode()).hexdigest()[:32]
    collision = destination / f"42-{digest}"
    collision.mkdir(parents=True)
    (collision / "unrelated.epub").write_bytes(b"DO NOT OVERWRITE")
    with pytest.raises(q.QuarantineError, match="unowned"):
        _published(library)
    assert (collision / "unrelated.epub").read_bytes() == b"DO NOT OVERWRITE"
    assert original.exists()


def test_conflicting_snapshot_refuses_resuming_old_journal(library):
    _, original, _ = library
    _published(library)
    original.joinpath("novel.epub").write_bytes(b"changed")
    with pytest.raises(q.QuarantineError, match="conflict"):
        _published(library)


def test_reconcile_after_db_commit_crash_removes_only_orphan(library):
    _, original, _ = library
    ticket = _published(library)
    result = q.reconcile(library[0], lambda _book_id, _book_path: None)
    assert result["finished"] == [ticket["key"]], result
    assert not original.exists()
    assert Path(ticket["destination"]).joinpath("cover.jpg").is_file()
    assert q.reconcile(library[0], lambda _book_id, _book_path: None)["finished"] == []


def test_resume_after_partial_original_cleanup(library):
    _, original, _ = library
    receipt = _published(library)
    (original / "novel.epub").unlink()
    (original / "cover.jpg").unlink()
    # Simulate crash after two original files were removed but before last one.
    q.finish(receipt)
    assert not original.exists()
    assert Path(receipt["destination"], "metadata.opf").is_file()


def test_reconcile_when_book_still_active_does_not_remove_source(library):
    _, original, _ = library
    ticket = _published(library)
    result = q.reconcile(library[0], lambda _book_id, _book_path: object())
    assert result["finished"] == []
    assert result["pending"] == [
        {"key": ticket["key"], "reason": "book_id_currently_occupied"}]
    assert original.joinpath("novel.epub").is_file()
    assert Path(ticket["destination"]).is_dir()
