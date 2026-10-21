# SPDX-License-Identifier: GPL-3.0-or-later
"""Safety boundaries for the guarded Bookhelper duplicate-delete API."""
from types import SimpleNamespace
import pytest

pytestmark = pytest.mark.unit


def _book(book_id=5, tags=(), title="銀河英雄伝説"):
    return SimpleNamespace(
        id=book_id, uuid="book-uuid", title=title,
        authors=[SimpleNamespace(name="田中芳樹")], series=[],
        series_index=1.0, tags=[SimpleNamespace(name=x) for x in tags],
        identifiers=[SimpleNamespace(type="isbn", val="9780000000001")],
        data=[SimpleNamespace(format="EPUB", name="novel", uncompressed_size=300)],
        last_modified="2026-10-21", pubdate="1982-01-01", path="Tanaka/Novel",
    )


def test_snapshot_token_tracks_book_identity_and_installed_bytes_metadata():
    from cps.api.duplicate_delete import snapshot_token
    original = _book()
    token = snapshot_token(original)
    assert len(token) == 64
    assert token == snapshot_token(_book())
    original.data[0].uncompressed_size += 1
    assert snapshot_token(original) != token
    original.data[0].uncompressed_size -= 1
    original.title = "別の本"
    assert snapshot_token(original) != token


def test_all_users_are_scanned_and_original_filename_is_not_user_state(monkeypatch):
    from cps.api import duplicate_delete as module
    class Bookmark:
        book_id = 12
    class BookOriginalFilename:
        book_id = 12
    class UserLibraryBook:
        book_id = 12
    fake_mappers = [SimpleNamespace(class_=x, columns={"book_id": object()})
                    for x in (Bookmark, BookOriginalFilename, UserLibraryBook)]
    fake_mappers.append(SimpleNamespace(class_=SimpleNamespace, columns={}))
    class Query:
        def __init__(self, cls):
            self.cls = cls
        def filter(self, *_):
            return self
        def limit(self, *_):
            return self
        def first(self):
            return object() if self.cls in (Bookmark, UserLibraryBook) else None
    class Session:
        def query(self, cls):
            if cls == module.ub.MagicShelfCache.book_ids:
                return SimpleNamespace(limit=lambda _: SimpleNamespace(all=lambda: []))
            return Query(cls)
    monkeypatch.setattr(module.ub, "Base", SimpleNamespace(
        registry=SimpleNamespace(mappers=fake_mappers)))
    monkeypatch.setattr(module.ub, "session", Session())
    assert module.user_book_references(12) == ["Bookmark", "UserLibraryBook"]


def test_json_book_ids_in_magic_shelf_cache_block_delete(monkeypatch):
    from cps.api import duplicate_delete as module
    monkeypatch.setattr(module.ub, "Base", SimpleNamespace(
        registry=SimpleNamespace(mappers=[])))

    class CacheSession:
        def __init__(self, data):
            self.data = data
        def query(self, column):
            assert column == module.ub.MagicShelfCache.book_ids
            return SimpleNamespace(limit=lambda _: SimpleNamespace(
                all=lambda: [(ids,) for ids in self.data]))

    monkeypatch.setattr(module.ub, "session", CacheSession([[7, 12], [18]]))
    assert module.user_book_references(12) == ["MagicShelfCache"]
    assert module.user_book_references(11) == []
    # Corrupted/incomplete JSON cannot certify that all references are absent.
    monkeypatch.setattr(module.ub, "session", CacheSession([{"unexpected": 12}]))
    with pytest.raises(ValueError, match="malformed magic-shelf"):
        module.user_book_references(12)



def test_max_id_guard_and_cross_media_tag_veto(monkeypatch):
    from cps.api import duplicate_delete as module
    class Query:
        def scalar(self):
            return 20
    monkeypatch.setattr(module.calibre_db.session, "query", lambda *_: Query())
    monkeypatch.setattr(module, "user_book_references", lambda *_: [])
    blocked = module.preflight(_book(20), _book(1))
    assert "id_reuse_risk_maximum_book_id" in blocked["blockers"]
    tagged = module.preflight(_book(19, tags=["pNext"]), _book(1))
    assert "protected_book_sync_tag" in tagged["blockers"]
    clean = module.preflight(_book(19), _book(1))
    assert clean["allowed"] is True
    assert clean["source_uuid"] == "book-uuid"


def test_real_bookhelper_restore_reference_is_accepted():
    from cps.api.duplicate_delete import valid_restore_reference
    from hashlib import sha256
    receipt_hash = sha256(b"approved-plan:2").hexdigest()[:32]
    valid = f"bookhelper-undo-{receipt_hash}-2.epub"
    assert valid_restore_reference(valid)
    assert not valid_restore_reference(valid.replace(".epub", "Xepub"))
    assert not valid_restore_reference(valid.replace(".epub", ".zip"))
    assert not valid_restore_reference("../" + valid)
    assert not valid_restore_reference(valid + ".bak")


def test_metadata_remove_tags_is_exact_and_preserves_unrelated_values():
    from cps.api.edit import _remove_tags
    stored = _book(tags=("hasAudiobook", "User note", "pNext", "Other"))
    assert _remove_tags(stored, "hasAudiobook") == "User note, pNext, Other"
    assert _remove_tags(stored, "HASaudiobook, Other") == "User note, pNext"
    assert _remove_tags(stored, "not-installed-tag") is None
    assert _remove_tags(stored, "") is None
    assert [tag.name for tag in stored.tags] == [
        "hasAudiobook", "User note", "pNext", "Other"
    ]



def test_real_metadata_route_removes_only_selected_tags(monkeypatch):
    """Exercise the actual handler logic, not a fake permissive CWA server."""
    from flask import Flask
    from cps.api import edit as module
    item = _book(book_id=42, tags=("hasAudiobook", "pNext", "Manual"))
    calls = []

    def edit_field(field, values):
        calls.append((field, values["value"]))
        item.tags = [SimpleNamespace(name=name) for name in values["value"].split(", ")]
        return ""

    monkeypatch.setattr(module, "_require_edit", lambda: None)
    monkeypatch.setattr(module, "_editable_book", lambda book_id: item if book_id == 42 else None)
    monkeypatch.setattr(module, "edit_book_param", edit_field)
    monkeypatch.setattr(module, "_editable_metadata", lambda book: {
        "tags": ", ".join(tag.name for tag in book.tags)
    })
    app = Flask("bookhelper-contract-test")
    with app.test_request_context("/api/v1/books/42/metadata",
                                  method="POST",
                                  json={"list_mode": "remove", "tags": "hasAudiobook"}):
        response = module.update_metadata.__wrapped__(42)
        assert response.status_code == 200
        assert response.get_json()["tags"] == "pNext, Manual"
    assert calls == [("tags", "pNext, Manual")]

    with app.test_request_context("/api/v1/books/42/metadata",
                                  method="POST",
                                  json={"list_mode": "remove", "tags": "not-installed"}):
        response = module.update_metadata.__wrapped__(42)
        assert response.status_code == 200
        assert calls == [("tags", "pNext, Manual")]  # no extra mutation

    with app.test_request_context("/api/v1/books/42/metadata",
                                  method="POST",
                                  json={"list_mode": "remove", "authors": "bad"}):
        response, code = module.update_metadata.__wrapped__(42)
        assert code == 400
        assert response.get_json()["error"]["code"] == "invalid_request"


def test_real_restore_ref_handler_accepts_matching_marker(monkeypatch):
    from flask import Flask
    from cps.api import duplicate_delete as module
    from hashlib import sha256
    marker = f"bookhelper-undo-{sha256(b'plan:4').hexdigest()[:32]}-4.epub"

    class NoImportRows:
        def filter(self, *_):
            return self
        def limit(self, *_):
            return self
        def all(self):
            return []

    class ImportSession:
        def query(self, *_):
            return NoImportRows()

    monkeypatch.setattr(module, "_require_book_delete", lambda: None)
    monkeypatch.setattr(module.ub, "session", ImportSession())
    app = Flask("bookhelper-restore-ref-test")
    with app.test_request_context("/api/v1/books/duplicates/restore-ref/" + marker):
        response, code = module.lookup_restored_book.__wrapped__(marker)
        assert code == 200
        assert response.get_json() == {"book_id": None}

    with app.test_request_context("/api/v1/books/duplicates/restore-ref/bad-name"):
        response, code = module.lookup_restored_book.__wrapped__("bad-name")
        assert code == 400
        assert response.get_json()["error"]["code"] == "invalid_reference"

def test_deletion_rechecks_user_references_after_approved_preflight(monkeypatch):
    """A clean old token is not permission to delete after a new user link."""
    from flask import Flask
    from cps.api import duplicate_delete as module

    class MaxIdQuery:
        def scalar(self):
            return 500

    monkeypatch.setattr(module.calibre_db.session, "query", lambda *_: MaxIdQuery())
    live = {"references": []}
    monkeypatch.setattr(module, "user_book_references",
                        lambda *_: list(live["references"]))
    original, keeper = _book(5), _book(1)
    initial = module.preflight(original, keeper)
    assert initial["allowed"]
    live["references"] = ["Bookmark"]
    monkeypatch.setattr(module, "_require_book_delete", lambda: None)
    monkeypatch.setattr(module, "resolve_candidates", lambda *_: (original, keeper))

    def unexpected_delete(*_args, **_kwargs):
        raise AssertionError("must not delete after a user reference appears")

    monkeypatch.setattr(module, "delete_book_from_table", unexpected_delete)
    app = Flask("guarded-delete-current-references")
    with app.test_request_context("/api/v1/books/5/duplicates/delete",
                                  method="POST",
                                  json={"keeper_id": 1, "token": initial["token"]}):
        reply, code = module.duplicate_delete.__wrapped__(5)
        assert code == 409
        assert "all_user_book_references" in reply.get_json()["blockers"]
