# SPDX-License-Identifier: GPL-3.0-or-later
"""Guarded duplicate removal for an external content-verified library cleaner.

Only a completely unreferenced record can be removed here. All-user state is
checked on the CWA side, not inferred from one authenticated user's book detail.
The caller must independently verify content equivalence and durable recovery.
"""
import hashlib
import json
import re

from flask import jsonify, request
from sqlalchemy.orm import Session
from sqlalchemy import text

from . import api_v1
from .edit import _require_book_delete, _editable_book
from .. import calibre_db, db, ub, logger, config, duplicate_quarantine
from ..editbooks import delete_whole_book
from ..services.calibre_db_lock import metadata_db_write_lock
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano

log = logger.create()
PROTECTED_TAGS = frozenset({
    "pNext", "priority", "priorityQ", "hasAudiobook", "needsEpub", "囁き優先",
})


def snapshot_token(book):
    """Version token covering source metadata, format roster and UUID."""
    details = {
        "id": book.id,
        "uuid": getattr(book, "uuid", None),
        "title": book.title,
        "authors": sorted(author.name for author in (book.authors or [])),
        "series": sorted(series.name for series in (book.series or [])),
        "series_index": str(getattr(book, "series_index", "")),
        "tags": sorted(tag.name for tag in (book.tags or [])),
        "identifiers": sorted((entry.type, entry.val) for entry in (book.identifiers or [])),
        "formats": sorted((entry.format, entry.name, entry.uncompressed_size)
                          for entry in (book.data or [])),
        "last_modified": str(getattr(book, "last_modified", "")),
        "pubdate": str(getattr(book, "pubdate", "")),
        "path": str(getattr(book, "path", "")),
    }
    return hashlib.sha256(json.dumps(details, sort_keys=True, ensure_ascii=False,
                                      default=str).encode("utf-8")).hexdigest()


def user_book_references(book_id, session=None):
    """Conservative all-accounts check over every mapped app.db book_id.

    This dynamically picks up new book-scoped models. A missing/unqueryable
    model is not evidence that a book has no state: fail closed on errors.
    """
    session = session if session is not None else ub.session
    found = []
    for mapper in sorted(ub.Base.registry.mappers, key=lambda m: m.class_.__name__):
        if "book_id" not in mapper.columns:
            continue
        model = mapper.class_
        # Original filenames are import provenance rather than a user's
        # reading/ownership state. Bookhelper archives this before deletion.
        if model.__name__ == "BookOriginalFilename":
            continue
        if session.query(model).filter(
                getattr(model, "book_id") == book_id).limit(1).first() is not None:
            found.append(model.__name__)
    # MagicShelfCache.book_ids stores numeric Calibre IDs in JSON, not in a
    # mapped book_id column. A stale cached reference cannot be ignored by an
    # all-user reference-free deletion assertion. Cap this scan and fail closed.
    cache_rows = session.query(ub.MagicShelfCache.book_ids).limit(10001).all()
    if len(cache_rows) > 10000:
        raise ValueError("magic-shelf cache reference inventory exceeds safety bound")
    for (ids,) in cache_rows:
        if ids is None:
            continue
        if (not isinstance(ids, list) or len(ids) > 300000
                or any(type(value) is not int or value <= 0 for value in ids)):
            raise ValueError("malformed magic-shelf cached book ID inventory")
        if book_id in ids:
            found.append("MagicShelfCache")
            break
    return found


def resolve_candidates(source_id, keeper_id):
    if not isinstance(keeper_id, int) or keeper_id < 1 or source_id == keeper_id:
        return None, None
    source = _editable_book(source_id)
    keeper = _editable_book(keeper_id)
    return source, keeper


def preflight(source, keeper, reference_session=None):
    blockers = []
    # Highest IDs may be recycled by Calibre. Bookhelper validates BOTH
    # source and keeper UUID-qualified aliases before any exact-ID operation;
    # rejecting the highest ID would strand perfectly valid duplicates.
    # Recycled-ID safety is an identity contract, not a maximum-ID heuristic.
    if any(tag.name in PROTECTED_TAGS for tag in (source.tags or [])):
        blockers.append("protected_book_sync_tag")
    if not any(row.format.upper() == "EPUB" for row in (source.data or [])):
        blockers.append("source_epub_missing")
    if not any(row.format.upper() == "EPUB" for row in (keeper.data or [])):
        blockers.append("keeper_epub_missing")
    source_uuid = getattr(source, "uuid", None)
    keeper_uuid = getattr(keeper, "uuid", None)
    if not isinstance(source_uuid, str) or not source_uuid.strip():
        blockers.append("source_stable_uuid_missing")
    if not isinstance(keeper_uuid, str) or not keeper_uuid.strip():
        blockers.append("keeper_stable_uuid_missing")
    refs = (user_book_references(source.id, reference_session)
            if reference_session is not None
            else user_book_references(source.id))
    if refs:
        blockers.append("all_user_book_references")
    # Bind the proof to BOTH rows. A recycled or edited keeper cannot inherit
    # a still-valid source-only deletion token.
    pair_token = hashlib.sha256(
        (snapshot_token(source) + ":" + snapshot_token(keeper)).encode("ascii")
    ).hexdigest()
    return {
        "book_id": source.id,
        "keeper_id": keeper.id,
        "source_uuid": source_uuid,
        "keeper_uuid": keeper_uuid,
        "quarantine_id": (duplicate_quarantine.transaction_id_for(
            source.id, source_uuid, keeper_uuid, str(source.path))
            if isinstance(source_uuid, str) and source_uuid and
               isinstance(keeper_uuid, str) and keeper_uuid else None),
        "token": pair_token,
        "allowed": not blockers,
        "blockers": blockers,
        "referencing_models": refs,
    }


@api_v1.route("/books/<int:book_id>/duplicates/preflight", methods=["POST"])
@login_required_if_no_ano
def duplicate_delete_preflight(book_id):
    guard = _require_book_delete()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    source, keeper = resolve_candidates(book_id, body.get("keeper_id"))
    if not source or not keeper:
        return jsonify({"error": {"code": "not_found", "message": "Source or keeper unavailable"}}), 404
    try:
        return jsonify(preflight(source, keeper))
    except Exception:
        log.error("Duplicate deletion reference preflight unavailable", exc_info=True)
        return jsonify({"error": {"code": "preflight_unavailable", "message": "Reference check failed"}}), 503


def _begin_immediate(connection, database):
    """Reserve SQLite's real writer slot, not a thread-local application mutex.

    The app.db writer slot blocks all connections/processes inserting any
    user-book reference while the metadata row is deleted. The Calibre slot
    freezes both numeric ID and UUID through commit, including ingest workers.
    An existing transaction cannot be promoted safely after a stale read.
    """
    if connection.dialect.name != "sqlite":
        raise RuntimeError(f"{database}: guarded delete needs SQLite transactions")
    driver = connection.connection.driver_connection
    if driver.in_transaction:
        raise RuntimeError(f"{database}: existing transaction prevents guarded delete")
    connection.exec_driver_sql("BEGIN IMMEDIATE")


def _delete_guarded_unreferenced(source_id, keeper_id, expected_token):
    """DB-first, files-last removal with app.db+Calibre SQLite writer exclusion.

    The generic delete core purges all-user data and commits app.db mid-flight.
    This dedicated path instead holds app.db BEGIN IMMEDIATE while checking
    references AND deleting in Calibre, and never purges any protected data.
    There are no user references to migrate. The only app.db row removed is
    non-user-state original filename provenance. All preflight validation
    happens AGAIN after both SQLite writer reservations have been acquired.

    Before committing the Calibre row, stage the COMPLETE original book
    directory into CWA-owned persistent quarantine with a verified journal.
    Separate SQLite files cannot be crash-atomically committed in WAL mode:
    commit Calibre first, then delete the orphanable provenance row. A crash
    can leave harmless provenance and/or original file remnants; the staged
    verified quarantine is not destroyed or mistaken for an active book.
    """
    with metadata_db_write_lock():
        if config.config_use_google_drive:
            raise RuntimeError("guarded quarantine requires a local Calibre library")
        app_engine = ub.session.get_bind()
        with app_engine.connect() as app_connection:
            app_session = Session(bind=app_connection, autoflush=False)
            ticket = None
            calibre_committed = False
            try:
                _begin_immediate(app_connection, "app.db")
                calibre_connection = calibre_db.session.connection()
                _begin_immediate(calibre_connection, "metadata.db")
                # Re-fetch the full identity under BOTH writer reservations.
                calibre_db.session.expire_all()
                source, keeper = resolve_candidates(source_id, keeper_id)
                if source is None or keeper is None:
                    return "stale", ["source_or_keeper_unavailable"], None
                latest = preflight(source, keeper, reference_session=app_session)
                if not latest["allowed"] or latest["token"] != expected_token:
                    return "stale", latest["blockers"], None

                # Defensive second check before issuing the supported Calibre
                # delete. app.db's writer reservation remains held throughout.
                if user_book_references(source_id, app_session):
                    return "stale", ["all_user_book_references"], None
                # Publish a hash-verified intact quarantine before deleting
                # metadata. A crash here leaves the Calibre record and original
                # directory intact, plus a transaction-owned staged copy.
                original_path = str(source.path)
                ticket = duplicate_quarantine.publish(
                    config.get_book_path(), original_path, source_id,
                    getattr(source, "uuid"), getattr(keeper, "uuid"))
                delete_whole_book(source_id, source, guarded_unreferenced=True)
                # No interior commits are permitted in the guarded path.
                calibre_db.session.commit()
                calibre_committed = True
                # Only provenance, never personal reading, shelf or Kobo state.
                app_session.query(ub.BookOriginalFilename).filter(
                    ub.BookOriginalFilename.book_id == source_id).delete(
                    synchronize_session=False)
                app_session.flush()
                app_connection.commit()
                # metadata.db commit releases its SQLite writer reservation.
                # Reacquire it BEFORE removing the orphaned source directory:
                # another writer might have reused the source ID or path in
                # that interval. A held app.db lock alone does not protect
                # Calibre's path namespace.
                cleanup_connection = calibre_db.session.connection()
                _begin_immediate(cleanup_connection, "metadata.db orphan cleanup")
                occupied = cleanup_connection.execute(
                    text("SELECT id FROM books WHERE id = :book_id "
                         "OR path = :book_path LIMIT 1"),
                    {"book_id": source_id, "book_path": original_path},
                ).first()
                if occupied is not None:
                    raise duplicate_quarantine.QuarantineError(
                        "original Calibre ID/path reoccupied after metadata commit; "
                        "verified quarantine retained, refusing orphan cleanup")
                # The exclusive Calibre writer reservation stays active
                # THROUGH fs cleanup, not merely through the DB commit.
                confirmation = duplicate_quarantine.finish(ticket)
                calibre_db.session.rollback()
            except Exception:
                calibre_db.session.rollback()
                app_connection.rollback()
                if ticket is not None and not calibre_committed:
                    try:
                        duplicate_quarantine.cancel(ticket)
                    except Exception:
                        log.error("Could not cancel uncommitted guarded quarantine", exc_info=True)
                # Once Calibre commits, its original file bytes remain in
                # quarantine. Never cancel the only recovery copy if the
                # separate app.db provenance commit fails.
                raise
            finally:
                app_session.close()
                # Even a rejected preflight must release reserved writer slots.
                if app_connection.in_transaction():
                    app_connection.rollback()
                calibre_db.session.rollback()

    # The DB row is gone and orphan cleanup completed under a fresh Calibre
    # writer reservation. Any failure above reports an uncertain result and
    # leaves the committed, verified quarantine available for reconciliation.
    try:
        from cps.cwa_db_loader import load_cwa_db
        cwa_db = load_cwa_db().CWA_DB()
        cwa_db.invalidate_duplicate_cache()
    except Exception:
        log.warning("Could not invalidate duplicate index after quarantine", exc_info=True)
    return "deleted", [], confirmation["quarantine_id"]


@api_v1.route("/books/<int:book_id>/duplicates/delete", methods=["POST"])
@login_required_if_no_ano
def duplicate_delete(book_id):
    guard = _require_book_delete()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    keeper_id = body.get("keeper_id")
    if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
            or type(keeper_id) is not int or keeper_id < 1 or keeper_id == book_id):
        return jsonify({"error": {"code": "invalid_request",
                                  "message": "Preflight token and keeper ID required"}}), 400
    try:
        result, blockers, _ = _delete_guarded_unreferenced(book_id, keeper_id, token)
    except Exception:
        log.error("Guarded duplicate deletion failed (no success implied)", exc_info=True)
        return jsonify({"error": {"code": "guarded_delete_failed",
                                  "message": "Cannot confirm guarded deletion outcome"}}), 503
    if result == "stale":
        return jsonify({"error": {"code": "stale_or_referenced",
                                  "message": "Identity changed or user state exists"},
                        "blockers": blockers}), 409
    return jsonify({"deleted": True, "quarantine_id": _,
                    "state": "quarantined"}), 200


@api_v1.route(
    "/books/<int:book_id>/duplicates/quarantine/<quarantine_id>",
    methods=["GET"])
@login_required_if_no_ano
def quarantine_status(book_id, quarantine_id):
    """Attest a committed, complete-file quarantine after a lost HTTP reply.

    The caller must independently recheck its prior source/keeper UUIDs and
    original deletion journal. No delete or cleanup is initiated by GET.
    """
    guard = _require_book_delete()
    if guard:
        return guard
    try:
        result = duplicate_quarantine.inspect(
            config.get_book_path(), book_id, quarantine_id)
    except (duplicate_quarantine.QuarantineError, OSError, ValueError, KeyError):
        return jsonify({"error": {
            "code": "quarantine_unverified",
            "message": "No verified quarantine exists for this identity",
        }}), 404
    return jsonify(result), 200


@api_v1.route("/books/duplicates/quarantine/reconcile", methods=["POST"])
@login_required_if_no_ano
def reconcile_duplicate_quarantine():
    """Bounded restart recovery; no Bookhelper filesystem/database access."""
    guard = _require_book_delete()
    if guard:
        return guard
    if config.config_use_google_drive:
        return jsonify({"error": {"code": "unsupported_backend",
                                  "message": "Local quarantine requires local Calibre storage"}}), 409
    try:
        with metadata_db_write_lock():
            calibre_connection = calibre_db.session.connection()
            _begin_immediate(calibre_connection, "metadata.db")
            try:
                result = duplicate_quarantine.reconcile(
                    config.get_book_path(),
                    lambda book_id, book_path: calibre_db.session.query(db.Books).filter(
                        (db.Books.id == book_id) | (db.Books.path == book_path)
                    ).first(),
                    max_transactions=32,
                )
            finally:
                calibre_db.session.rollback()
    except Exception:
        log.error("Duplicate quarantine reconciliation failed", exc_info=True)
        return jsonify({"error": {"code": "reconcile_unavailable",
                                  "message": "Cannot verify quarantine journal"}}), 503
    return jsonify(result), 200


def valid_restore_reference(reference):
    """Validate precisely the provenance filename produced by Bookhelper undo."""
    return bool(re.fullmatch(r"bookhelper-undo-[0-9a-f]{32}-[0-9]+\.epub", reference))


@api_v1.route("/books/duplicates/restore-ref/<reference>", methods=["GET"])
@login_required_if_no_ano
def lookup_restored_book(reference):
    """Identify an asynchronous undo import by its unique original filename.

    A queued import is not a created book. The caller must reconcile this
    stable marker after ingestion and must never submit the same import again
    while its outcome is unknown.
    """
    guard = _require_book_delete()
    if guard:
        return guard
    if not valid_restore_reference(reference):
        return jsonify({"error": {"code": "invalid_reference", "message": "Invalid restore reference"}}), 400
    rows = ub.session.query(ub.BookOriginalFilename).filter(
        ub.BookOriginalFilename.filename == reference).limit(2).all()
    if len(rows) > 1:
        return jsonify({"error": {"code": "ambiguous_reference", "message": "Multiple imports match"}}), 409
    if not rows:
        return jsonify({"book_id": None}), 200
    book_id = int(rows[0].book_id)
    if not _editable_book(book_id):
        return jsonify({"error": {"code": "not_found", "message": "Restored book not visible"}}), 404
    return jsonify({"book_id": book_id}), 200
