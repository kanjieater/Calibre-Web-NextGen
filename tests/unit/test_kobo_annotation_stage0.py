# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 0 contract for Kobo two-way annotation sync.

Stage 0 is deliberately passive: it adds durable evidence and opt-ins, while
the existing reading-services proxy remains the only response owner.
"""

from __future__ import annotations

import gzip
import json
import logging
from types import SimpleNamespace

import pytest
from flask import Flask, make_response
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


OLD_ANNOTATION_COLUMNS = (
    "id", "user_id", "annotation_id", "book_id", "source",
    "highlighted_text", "highlight_color", "note_text", "content_id",
    "start_container_path", "start_container_child_index", "start_offset",
    "end_container_path", "end_container_child_index", "end_offset",
    "context_string", "chapter_progress", "cfi_range", "position_type",
    "pdf_page", "pdf_quad_json", "comic_page", "start_xpointer",
    "end_xpointer", "device_origin_id", "hidden", "created_at",
    "client_modified_at", "origin_device_id", "assigned_device_id",
    "routing_revision", "last_synced",
)


def _create_gate_tables(conn, *, user_gate=False, settings_gate=False):
    conn.execute(text(
        "CREATE TABLE user (id INTEGER PRIMARY KEY, name VARCHAR(64)"
        + (", kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0" if user_gate else "")
        + ")"
    ))
    conn.execute(text(
        "CREATE TABLE settings (id INTEGER PRIMARY KEY"
        + (", config_kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0" if settings_gate else "")
        + ")"
    ))
    conn.execute(text("INSERT INTO user (id, name) VALUES (7, 'reader')"))
    conn.execute(text("INSERT INTO settings (id) VALUES (1)"))
    conn.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY)"))


def _create_old_annotation_table(conn, *, annotation_type=False):
    extra = ", annotation_type VARCHAR(32)" if annotation_type else ""
    conn.execute(text(f"""
        CREATE TABLE annotation (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          annotation_id VARCHAR NOT NULL,
          book_id INTEGER NOT NULL,
          source VARCHAR,
          highlighted_text VARCHAR,
          highlight_color VARCHAR,
          note_text VARCHAR,
          content_id VARCHAR,
          start_container_path TEXT,
          start_container_child_index INTEGER,
          start_offset INTEGER,
          end_container_path TEXT,
          end_container_child_index INTEGER,
          end_offset INTEGER,
          context_string TEXT,
          chapter_progress REAL,
          cfi_range VARCHAR,
          position_type VARCHAR,
          pdf_page INTEGER,
          pdf_quad_json TEXT,
          comic_page INTEGER,
          start_xpointer TEXT,
          end_xpointer TEXT,
          device_origin_id VARCHAR,
          hidden BOOLEAN,
          created_at DATETIME,
          client_modified_at DATETIME,
          origin_device_id INTEGER,
          assigned_device_id INTEGER,
          routing_revision INTEGER NOT NULL DEFAULT 1,
          last_synced DATETIME
          {extra}
        )
    """))
    conn.exec_driver_sql("""
        INSERT INTO annotation (
          user_id, annotation_id, book_id, source, highlighted_text,
          highlight_color, note_text, content_id, start_container_path,
          start_container_child_index, start_offset, end_container_path,
          end_container_child_index, end_offset, context_string,
          chapter_progress, cfi_range, position_type, pdf_page, pdf_quad_json,
          comic_page, start_xpointer, end_xpointer, device_origin_id, hidden,
          created_at, client_modified_at, origin_device_id, assigned_device_id,
          routing_revision, last_synced
        ) VALUES (
          7, 'ann-\u2603', 348, 'kobo', 'text with é and \\slashes',
          'yellow', 'note\nbytes', 'book-uuid!!chapter.xhtml',
          'span#kobo\\.1', -99, 0, 'span#kobo\\.2', -99, 17,
          'left | selected | right', 0.375,
          'epubcfi(/6/2!/4[kobo.1]:0,/4[kobo.2]:17)', 'cfi', NULL, NULL,
          NULL, NULL, NULL, 'native-row-1', 0,
          '2026-01-02 03:04:05.000006', '2026-01-02 03:04:06.000007',
          NULL, NULL, 1, '2026-01-02 03:04:07.000008'
        )
    """)


def _snapshot_old_values(conn):
    row = conn.execute(text(
        "SELECT " + ",".join(OLD_ANNOTATION_COLUMNS) + " FROM annotation ORDER BY id"
    )).mappings().one()
    return dict(row)


@pytest.mark.unit
def test_models_declare_stage0_schema_and_constraints():
    from cps import ub

    assert {c.name for c in ub.Annotation.__table__.columns} >= {
        "annotation_type", "content_revision", "server_modified_at",
        "last_editor_device_id",
    }
    expected_tables = {
        "kobo_annotation_materialization",
        "kobo_annotation_book_state",
        "kobo_device_book_annotation_state",
        "kobo_annotation_seed_capture",
        "kobo_annotation_seed_capture_page",
        "kobo_annotation_page_snapshot",
        "kobo_annotation_page_cursor",
    }
    assert expected_tables <= set(ub.Base.metadata.tables)
    assert ub.KoboAnnotationMaterialization.__table__.c.raw_annotation_json.type.python_type is bytes


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["fresh", "legacy", "partial", "repeated"])
def test_stage0_migration_is_idempotent_and_preserves_annotation_bytes(shape):
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    if shape == "fresh":
        from cps import config_sql
        ub.Base.metadata.create_all(engine)
        config_sql._Settings.__table__.create(engine, checkfirst=True)
    else:
        with engine.begin() as conn:
            _create_gate_tables(
                conn,
                user_gate=shape == "partial",
                settings_gate=False,
            )
            if shape != "legacy":
                _create_old_annotation_table(conn, annotation_type=shape == "partial")

    before = None
    if shape not in {"fresh", "legacy"}:
        with engine.connect() as conn:
            before = _snapshot_old_values(conn)

    ub.migrate_kobo_two_way_annotation_sync(engine, None)
    if shape == "repeated":
        ub.migrate_kobo_two_way_annotation_sync(engine, None)

    tables = set(inspect(engine).get_table_names())
    assert {
        "kobo_annotation_materialization", "kobo_annotation_book_state",
        "kobo_device_book_annotation_state", "kobo_annotation_seed_capture",
        "kobo_annotation_seed_capture_page", "kobo_annotation_page_snapshot",
        "kobo_annotation_page_cursor",
    } <= tables

    if shape not in {"fresh", "legacy"}:
        with engine.connect() as conn:
            after = _snapshot_old_values(conn)
            assert after == before
            assert after["cfi_range"] == before["cfi_range"]
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
            assert {"annotation_type", "content_revision", "server_modified_at",
                    "last_editor_device_id"} <= columns
            state = conn.execute(text(
                "SELECT authority_status, authority_revision, opaque_content_status "
                "FROM kobo_annotation_book_state WHERE user_id=7 AND book_id=348"
            )).one()
            assert tuple(state) == ("unseeded", 0, "unknown")
        assert before["cfi_range"].encode("utf-8") == after["cfi_range"].encode("utf-8")

    if shape != "fresh":
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT kobo_two_way_annotation_sync FROM user WHERE id=7"
            )).scalar_one() == 0
            assert conn.execute(text(
                "SELECT config_kobo_two_way_annotation_sync FROM settings WHERE id=1"
            )).scalar_one() == 0


@pytest.mark.unit
def test_opaque_present_is_sticky_at_database_boundary():
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    ub.migrate_kobo_two_way_annotation_sync(engine, None)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO kobo_annotation_book_state "
            "(user_id, book_id, content_id, authority_status, authority_revision, "
            "generation_id, opaque_content_status, updated_at) VALUES "
            "(7, 348, 'content-id', 'unseeded', 0, 'generation', 'present', CURRENT_TIMESTAMP)"
        ))
        with pytest.raises(Exception):
            conn.execute(text(
                "UPDATE kobo_annotation_book_state "
                "SET opaque_content_status='absent' WHERE user_id=7 AND book_id=348"
            ))


RAW_PATCH = (
    b'{ "updatedAnnotations" : [ {"id":"ann-1","type":"highlight",'
    b'"clientLastModifiedUtc":"2026-08-16T12:34:56.000Z",'
    b'"highlightedText":"private annotation text",'
    b'"location" : { "span" : {"startPath":"p\\/a",'
    b'"chapterFilename":"chapter.xhtml","startChar":1,"endChar":2}},'
    b'"attachments":{} } ], "deletedAnnotationIds": [] }'
)
RAW_LOCATION = (
    b'{ "span" : {"startPath":"p\\/a",'
    b'"chapterFilename":"chapter.xhtml","startChar":1,"endChar":2}}'
)


@pytest.mark.unit
def test_raw_lexical_capture_and_projection_preserve_location_bytes():
    from cps.services import kobo_annotation_capture as capture

    records = capture.extract_updated_annotation_materializations(RAW_PATCH)
    assert len(records) == 1
    record = records[0]
    assert record.annotation_id == "ann-1"
    assert record.raw_annotation_json in RAW_PATCH
    assert record.raw_location_json == RAW_LOCATION
    assert capture.project_exact_materialization(
        record.raw_annotation_json, record.raw_location_json,
    ) == record.raw_annotation_json
    emitted = capture.extract_object_member_value(
        capture.project_exact_materialization(
            record.raw_annotation_json, record.raw_location_json,
        ),
        "location",
    )
    assert emitted == record.raw_location_json


@pytest.mark.unit
def test_dispatch_persists_raw_sidecar_without_rewriting_parsed_location(monkeypatch):
    from cps import ub
    from cps.services import annotation_backup, kobo_annotation_capture
    from cps.services import annotation_sync

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda: session.commit())
    monkeypatch.setattr(annotation_sync, "_REMOTE_ENQUEUE", None)
    annotation_sync.reset_registry_for_testing()

    payload = json.loads(RAW_PATCH)["updatedAnnotations"]
    raw_records = kobo_annotation_capture.extract_updated_annotation_materializations(RAW_PATCH)
    book = SimpleNamespace(id=348, uuid="book-uuid")
    user = SimpleNamespace(id=7)
    annotation_sync.dispatch_annotation_sync(
        payload, book, user, raw_materializations=raw_records,
    )

    ann = session.query(ub.Annotation).one()
    materialization = session.query(ub.KoboAnnotationMaterialization).one()
    assert ann.start_container_path == "p/a"
    assert ann.cfi_range is None
    assert ann.annotation_type == "highlight"
    assert ann.content_revision == 1
    assert ann.server_modified_at is not None
    assert materialization.annotation_id == ann.id
    assert materialization.raw_location_json == RAW_LOCATION
    assert materialization.raw_annotation_json == raw_records[0].raw_annotation_json
    assert materialization.provenance == "kobo_patch"
    assert materialization.attachments_state == "empty"
    assert materialization.serveable is False
    annotation_sync.dispatch_annotation_deletes(["ann-1"], user, book_id=348)
    session.refresh(ann)
    assert ann.hidden is True
    assert ann.content_revision == 2
    session.close()
    annotation_backup.reset_for_tests()


@pytest.mark.unit
def test_patch_raw_capture_failure_logs_but_keeps_proxy_bytes_and_response(caplog, monkeypatch):
    import cps.readingservices as rs

    app = Flask(__name__)
    forwarded = []
    dispatched = []
    response_body = b' {"upstream":"unchanged"} '

    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: SimpleNamespace(id=348))
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "cps.services.annotation_sync.dispatch_annotation_sync",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: forwarded.append(rs.request.get_data()) or
        make_response(response_body, 207, {"X-Upstream": "same"}),
    )
    malformed_for_capture = b'{"updatedAnnotations":[{"id":"ann-1","location":null}]}'
    with app.test_request_context(
        "/api/v3/content/book/annotations", method="PATCH",
        data=malformed_for_capture, content_type="application/json",
    ):
        response = rs.handle_annotations.__wrapped__("book")

    assert dispatched
    assert forwarded == [malformed_for_capture]
    assert response.status_code == 207
    assert response.get_data() == response_body
    assert response.headers["X-Upstream"] == "same"
    assert "raw lexical capture failed" in caplog.text.lower()

    forwarded.clear()
    dispatched.clear()
    with app.test_request_context(
        "/api/v3/content/book/annotations", method="PATCH",
        data=RAW_PATCH, content_type="application/json",
    ):
        response = rs.handle_annotations.__wrapped__("book")
    assert forwarded == [RAW_PATCH]
    assert response.status_code == 207
    assert response.get_data() == response_body
    assert response.headers["X-Upstream"] == "same"
    raw_records = dispatched[0][1]["raw_materializations"]
    assert raw_records[0].raw_annotation_json in RAW_PATCH
    assert raw_records[0].raw_location_json == RAW_LOCATION


@pytest.mark.unit
def test_gates_default_off_and_environment_can_only_disable(monkeypatch):
    from cps import config_sql, ub
    from cps.services import kobo_annotation_stage0

    assert ub.User.__table__.c.kobo_two_way_annotation_sync.default.arg is False
    assert config_sql._Settings.__table__.c.config_kobo_two_way_annotation_sync.default.arg is False

    user = SimpleNamespace(kobo_two_way_annotation_sync=True)
    settings = SimpleNamespace(config_kobo_two_way_annotation_sync=True)
    state = SimpleNamespace(authority_status="authoritative")

    monkeypatch.delenv("CWNG_KOBO_TWO_WAY_ANNOTATIONS", raising=False)
    assert kobo_annotation_stage0.gates_allow(settings, user, state, schema_ready=True) is True
    monkeypatch.setenv("CWNG_KOBO_TWO_WAY_ANNOTATIONS", "1")
    assert kobo_annotation_stage0.gates_allow(settings, user, state, schema_ready=True) is True
    monkeypatch.setenv("CWNG_KOBO_TWO_WAY_ANNOTATIONS", "0")
    assert kobo_annotation_stage0.gates_allow(settings, user, state, schema_ready=True) is False

    user.kobo_two_way_annotation_sync = False
    monkeypatch.setenv("CWNG_KOBO_TWO_WAY_ANNOTATIONS", "1")
    assert kobo_annotation_stage0.gates_allow(settings, user, state, schema_ready=True) is False

    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    config_sql._Settings.__table__.create(engine, checkfirst=True)
    ub.migrate_kobo_two_way_annotation_sync(engine, None)
    assert kobo_annotation_stage0.schema_capable(engine) is True
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_kam_serveable"))
    assert kobo_annotation_stage0.schema_capable(engine) is False


@pytest.mark.unit
def test_admin_and_user_gate_controls_are_wired_to_persistence():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config_template = (root / "cps/templates/config_edit.html").read_text(encoding="utf-8")
    user_template = (root / "cps/templates/user_edit.html").read_text(encoding="utf-8")
    admin_source = (root / "cps/admin.py").read_text(encoding="utf-8")
    web_source = (root / "cps/web.py").read_text(encoding="utf-8")

    assert 'name="config_kobo_two_way_annotation_sync"' in config_template
    assert "config.config_kobo_two_way_annotation_sync" in config_template
    assert 'name="kobo_two_way_annotation_sync"' in user_template
    assert "content.kobo_two_way_annotation_sync" in user_template
    assert '_config_checkbox_int(to_save, "config_kobo_two_way_annotation_sync")' in admin_source
    assert "content.kobo_two_way_annotation_sync" in admin_source
    assert "current_user.kobo_two_way_annotation_sync" in web_source


@pytest.mark.unit
def test_observability_is_structured_and_never_logs_annotation_text(caplog):
    import inspect as py_inspect
    import cps.readingservices as readingservices
    from cps.services import kobo_annotation_stage0

    caplog.set_level(logging.INFO)
    kobo_annotation_stage0.reset_metrics_for_testing()
    secret_text = "private annotation text"
    kobo_annotation_stage0.record_event(
        "raw_capture", "stored", trace_id="trace-123", user_id=7,
        book_id=348, annotation_count=1, annotation_text=secret_text,
    )
    snapshot = kobo_annotation_stage0.metrics_snapshot()
    assert snapshot[("raw_capture", "stored")] == 1
    assert "trace-123" in caplog.text
    assert "raw_capture" in caplog.text
    assert "stored" in caplog.text
    assert secret_text not in caplog.text
    assert "Response body:" not in py_inspect.getsource(
        readingservices.proxy_to_kobo_reading_services
    )
    redacted = readingservices.redact_headers({
        "authorization": "credential", "X-Kobo-UserKey": "user-key", "Safe": "ok",
    })
    assert redacted == {
        "authorization": "***REDACTED***",
        "X-Kobo-UserKey": "***REDACTED***",
        "Safe": "ok",
    }


@pytest.mark.unit
def test_backup_schema3_records_empty_set_and_stage0_metadata(tmp_path, monkeypatch):
    from cps import constants, ub
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    state = ub.KoboAnnotationBookState(
        user_id=7, book_id=348, content_id="book-content", generation_id="generation",
        authority_status="authoritative", authority_revision=2,
        opaque_content_status="unknown",
    )
    session.add(state)
    session.commit()

    path = annotation_backup.run_backup_now(7, 348, session=session)
    assert path is not None
    with gzip.open(path, "rb") as handle:
        payload = json.loads(handle.read())
    assert payload["schema_version"] == 3
    assert payload["annotations"] == []
    assert payload["materializations"] == []
    assert payload["book_authority"]["authority_status"] == "authoritative"
    assert payload["book_authority"]["authority_revision"] == 2
    assert set(payload["annotation_columns"]) == {c.name for c in ub.Annotation.__table__.columns}
    session.close()
    annotation_backup.reset_for_tests()
