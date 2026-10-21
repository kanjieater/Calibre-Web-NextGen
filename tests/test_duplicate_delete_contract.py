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
            if cls is module.ub.MagicShelfCache.book_ids:
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
            assert column is module.ub.MagicShelfCache.book_ids
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
    # The handler dispatches add/remove through the serialized helper.
    # Its transaction/race behavior is exercised independently below.
    def serialized(book_id, raw, mode):
        assert book_id == 42 and mode == "remove"
        remaining = module._remove_tags(item, raw)
        if remaining is not None:
            edit_field("tags", {"value": remaining})
        return True, ""
    monkeypatch.setattr(module, "_mutate_tags_serialized", serialized)
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


def test_serialized_tag_removal_preserves_concurrent_and_unrelated_additions(
        tmp_path, monkeypatch):
    """Read/replace runs under SQLite's actual metadata writer reservation."""
    import sqlite3
    from contextlib import nullcontext
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from cps.api import edit as module

    path = tmp_path / "metadata.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.1})
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE book_tags (book_id INTEGER, name TEXT)")
        conn.exec_driver_sql(
            "INSERT INTO book_tags VALUES (42,'remove-me'),(42,'Manual'),(42,'From another editor')")
    session = Session(engine)
    monkeypatch.setattr(module.calibre_db, "session", session)
    monkeypatch.setattr(module, "metadata_db_write_lock", lambda: nullcontext())
    attempted = []

    def fresh_book(_book_id):
        outsider = sqlite3.connect(path, timeout=0.05)
        try:
            try:
                outsider.execute("INSERT INTO book_tags VALUES (42, 'Racing')")
                outsider.commit()
                attempted.append(False)
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc)
                attempted.append(True)
        finally:
            outsider.close()
        names = session.execute(text(
            "SELECT name FROM book_tags WHERE book_id=42 ORDER BY rowid")).scalars().all()
        return SimpleNamespace(tags=[SimpleNamespace(name=n) for n in names])

    def write_tags(_field, payload):
        session.execute(text("DELETE FROM book_tags WHERE book_id=42"))
        for name in payload["value"].split(", "):
            if name:
                session.execute(text("INSERT INTO book_tags VALUES (42,:name)"), {"name": name})
        session.commit()
        return ""

    monkeypatch.setattr(module, "_editable_book", fresh_book)
    monkeypatch.setattr(module, "edit_book_param", write_tags)
    assert module._mutate_tags_serialized(42, "remove-me", "remove") == (True, "")
    assert attempted == [True]
    with engine.connect() as conn:
        names = conn.exec_driver_sql(
            "SELECT name FROM book_tags WHERE book_id=42").scalars().all()
    assert set(names) == {"Manual", "From another editor"}

    # Another editor succeeds AFTER the first transaction commits; a later
    # remove of one unrelated tag must retain their newly added membership.
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO book_tags VALUES (42,'Fresh')")
    assert module._mutate_tags_serialized(42, "Manual", "remove") == (True, "")
    with engine.connect() as conn:
        names = conn.exec_driver_sql(
            "SELECT name FROM book_tags WHERE book_id=42").scalars().all()
    assert set(names) == {"Fresh", "From another editor"}
    session.close()


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


def test_guarded_proof_tracks_both_stable_identities(monkeypatch):
    from cps.api import duplicate_delete as module

    class MaxIdQuery:
        def scalar(self):
            return 500

    monkeypatch.setattr(module.calibre_db.session, "query", lambda *_: MaxIdQuery())
    monkeypatch.setattr(module, "user_book_references", lambda *_: [])
    source, keeper = _book(5), _book(1)
    source.uuid = "source-uuid"
    keeper.uuid = "keeper-uuid"
    approved = module.preflight(source, keeper)
    assert approved["allowed"]
    assert approved["source_uuid"] == "source-uuid"
    assert approved["keeper_uuid"] == "keeper-uuid"

    keeper.uuid = "recycled-keeper"
    assert module.preflight(source, keeper)["token"] != approved["token"]
    keeper.uuid = "keeper-uuid"
    keeper.title = "Other edition"
    assert module.preflight(source, keeper)["token"] != approved["token"]
    keeper.title = "銀河英雄伝説"
    source.uuid = "recycled-source"
    assert module.preflight(source, keeper)["token"] != approved["token"]
    source.uuid = ""
    assert "source_stable_uuid_missing" in module.preflight(source, keeper)["blockers"]


def test_real_sqlite_guarded_delete_and_concurrent_writer(tmp_path, monkeypatch):
    """Exercise two SQLite files and a competing app.db writer connection.

    The competing connection cannot add a protected reference after guarded
    deletion reserves app.db. A reference committed before reservation instead
    prevents removal. This does NOT mock away the storage lock.
    """
    import sqlite3
    from contextlib import nullcontext
    from sqlalchemy import create_engine, Column, Integer, JSON, String, text
    from sqlalchemy.orm import declarative_base, Session
    from cps.api import duplicate_delete as module

    Base = declarative_base()

    class BookOriginalFilename(Base):
        __tablename__ = "book_original_filename"
        id = Column(Integer, primary_key=True)
        book_id = Column(Integer)
        filename = Column(String)

    class Bookmark(Base):
        __tablename__ = "bookmark"
        id = Column(Integer, primary_key=True)
        book_id = Column(Integer)

    class MagicShelfCache(Base):
        __tablename__ = "magic_shelf_cache"
        id = Column(Integer, primary_key=True)
        book_ids = Column(JSON)

    app_path = tmp_path / "app.db"
    calibre_path = tmp_path / "metadata.db"
    app_engine = create_engine(f"sqlite:///{app_path}", connect_args={"timeout": 0.1})
    calibre_engine = create_engine(f"sqlite:///{calibre_path}", connect_args={"timeout": 0.1})
    Base.metadata.create_all(app_engine)
    with calibre_engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE books (id INTEGER PRIMARY KEY, uuid TEXT, title TEXT)")
        conn.exec_driver_sql("INSERT INTO books VALUES (1, 'keeper', 'Keeper'), (5, 'source', 'Source'), (50, 'other', 'Other')")
    with app_engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO book_original_filename VALUES (1, 5, 'original.epub')")

    app_owner = Session(app_engine)
    metadata = Session(calibre_engine)
    monkeypatch.setattr(module.ub, "session", app_owner)
    monkeypatch.setattr(module.ub, "Base", Base)
    monkeypatch.setattr(module.ub, "BookOriginalFilename", BookOriginalFilename)
    monkeypatch.setattr(module.ub, "MagicShelfCache", MagicShelfCache)
    monkeypatch.setattr(module.calibre_db, "session", metadata)
    monkeypatch.setattr(module, "metadata_db_write_lock", lambda: nullcontext())
    monkeypatch.setattr(module.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(module.helper, "delete_book", lambda *_args, **_kw: (True, None))

    def resolve(source_id, keeper_id):
        def one(bid):
            row = metadata.execute(text("SELECT uuid, title FROM books WHERE id = :id"),
                                   {"id": bid}).first()
            if row is None:
                return None
            value = _book(bid, title=row.title)
            value.uuid = row.uuid
            value.path = f"author/{bid}"
            return value
        return one(source_id), one(keeper_id)

    monkeypatch.setattr(module, "resolve_candidates", resolve)
    class MaxId:
        def scalar(self):
            return 50
    real_query = metadata.query
    # The actual mapped CWA Books model is not used for this isolated
    # two-file database setup; supply the same max-ID SQLite result.
    monkeypatch.setattr(metadata, "query", lambda *_: MaxId())

    blocked_writer = []
    deleted = []
    def remove_row(book_id, book, *, guarded_unreferenced=False):
        assert guarded_unreferenced
        assert not module.user_book_references(book_id, read_session)
        assert metadata.execute(text("SELECT uuid FROM books WHERE id=5")).scalar() == "source"
        competing = sqlite3.connect(app_path, timeout=0.05)
        try:
            try:
                competing.execute("INSERT INTO bookmark (book_id) VALUES (5)")
                competing.commit()
                blocked_writer.append(False)
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc)
                blocked_writer.append(True)
        finally:
            competing.close()
        metadata.execute(text("DELETE FROM books WHERE id=:bid"), {"bid": book_id})
        deleted.append(book_id)

    monkeypatch.setattr(module, "delete_whole_book", remove_row)
    source, keeper = resolve(5, 1)
    token = module.preflight(source, keeper)["token"]

    # Capture the real guarded Session passed to the preflight in order to
    # assert it sees the exclusive app.db snapshot during deletion.
    original_preflight = module.preflight
    read_session = None
    def wrapped_preflight(*args, **kwargs):
        nonlocal read_session
        read_session = kwargs.get("reference_session")
        return original_preflight(*args, **kwargs)
    monkeypatch.setattr(module, "preflight", wrapped_preflight)

    result, blockers, _path = module._delete_guarded_unreferenced(5, 1, token)
    assert (result, blockers) == ("deleted", [])
    assert deleted == [5]
    assert blocked_writer == [True]
    assert metadata.execute(text("SELECT id FROM books WHERE id=5")).first() is None
    assert metadata.execute(text("SELECT id FROM books WHERE id=1")).first() is not None
    with app_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM book_original_filename WHERE book_id=5").scalar() == 0

    # A protected reference submitted BEFORE the writer reservation blocks
    # removal, rather than disappearing in the generic purge function.
    with calibre_engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO books VALUES (5, 'newsource', 'Another')")
    with app_engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO bookmark (book_id) VALUES (5)")
    source, keeper = resolve(5, 1)
    new_token = original_preflight(source, keeper)["token"]
    result, blockers, _ = module._delete_guarded_unreferenced(5, 1, new_token)
    assert result == "stale"
    assert "all_user_book_references" in blockers
    assert deleted == [5]
    assert metadata.execute(text("SELECT uuid FROM books WHERE id=5")).scalar() == "newsource"
    with app_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM bookmark WHERE book_id=5").scalar() == 1
    metadata.close()
    app_owner.close()


def test_clean_guarded_delete_route_delegates_to_serialized_storage(monkeypatch):
    from flask import Flask
    from cps.api import duplicate_delete as module

    monkeypatch.setattr(module, "_require_book_delete", lambda: None)
    captured = []
    def guarded(source, keeper, token):
        captured.append((source, keeper, token))
        return "deleted", [], "author/book"
    monkeypatch.setattr(module, "_delete_guarded_unreferenced", guarded)
    app = Flask("guarded-delete-contract")
    with app.test_request_context(
            "/api/v1/books/5/duplicates/delete", method="POST",
            json={"keeper_id": 1, "token": "f" * 64}):
        reply = module.duplicate_delete.__wrapped__(5)
        assert reply == ("", 204)
    assert captured == [(5, 1, "f" * 64)]
