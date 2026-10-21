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
            return Query(cls)
    monkeypatch.setattr(module.ub, "Base", SimpleNamespace(
        registry=SimpleNamespace(mappers=fake_mappers)))
    monkeypatch.setattr(module.ub, "session", Session())
    assert module.user_book_references(12) == ["Bookmark", "UserLibraryBook"]


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
