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
from sqlalchemy import func

from . import api_v1
from .edit import _require_book_delete, _editable_book, _delete_api_response
from .. import calibre_db, db, ub, logger
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..editbooks import delete_book_from_table

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


def user_book_references(book_id):
    """Conservative all-accounts check over every mapped app.db book_id.

    This dynamically picks up new book-scoped models. A missing/unqueryable
    model is not evidence that a book has no state: fail closed on errors.
    """
    found = []
    for mapper in sorted(ub.Base.registry.mappers, key=lambda m: m.class_.__name__):
        if "book_id" not in mapper.columns:
            continue
        model = mapper.class_
        # Original filenames are import provenance rather than a user's
        # reading/ownership state. Bookhelper archives this before deletion.
        if model.__name__ == "BookOriginalFilename":
            continue
        if ub.session.query(model).filter(
                getattr(model, "book_id") == book_id).limit(1).first() is not None:
            found.append(model.__name__)
    return found


def resolve_candidates(source_id, keeper_id):
    if not isinstance(keeper_id, int) or keeper_id < 1 or source_id == keeper_id:
        return None, None
    source = _editable_book(source_id)
    keeper = _editable_book(keeper_id)
    return source, keeper


def preflight(source, keeper):
    blockers = []
    # The Calibre SQLite book id is not guaranteed AUTOINCREMENT. Deleting
    # the maximum live id permits a later ingest to reuse that numeric ID,
    # defeating Bookhelper's exact-ID redirect for external consumers.
    max_id = calibre_db.session.query(func.max(db.Books.id)).scalar()
    if max_id is None or int(source.id) >= int(max_id):
        blockers.append("id_reuse_risk_maximum_book_id")
    if any(tag.name in PROTECTED_TAGS for tag in (source.tags or [])):
        blockers.append("protected_book_sync_tag")
    if not any(row.format.upper() == "EPUB" for row in (source.data or [])):
        blockers.append("source_epub_missing")
    if not any(row.format.upper() == "EPUB" for row in (keeper.data or [])):
        blockers.append("keeper_epub_missing")
    refs = user_book_references(source.id)
    if refs:
        blockers.append("all_user_book_references")
    return {
        "book_id": source.id,
        "keeper_id": keeper.id,
        "token": snapshot_token(source),
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


@api_v1.route("/books/<int:book_id>/duplicates/delete", methods=["POST"])
@login_required_if_no_ano
def duplicate_delete(book_id):
    guard = _require_book_delete()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("token"), str) or len(body["token"]) != 64:
        return jsonify({"error": {"code": "invalid_request", "message": "Preflight token required"}}), 400
    source, keeper = resolve_candidates(book_id, body.get("keeper_id"))
    if not source or not keeper:
        return jsonify({"error": {"code": "not_found", "message": "Source or keeper unavailable"}}), 404
    try:
        current = preflight(source, keeper)
    except Exception:
        log.error("Duplicate deletion reference validation unavailable", exc_info=True)
        return jsonify({"error": {"code": "preflight_unavailable", "message": "Reference check failed"}}), 503
    if not current["allowed"] or current["token"] != body["token"]:
        return jsonify({"error": {"code": "stale_or_referenced", "message": "State changed or book has protected references"},
                        "blockers": current["blockers"]}), 409
    # Delegate to the supported DB-first/file-last deletion. The client must
    # journal the request before sending it and reconcile 204/timeout by GET.
    return _delete_api_response(delete_book_from_table(book_id, "", True))


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
    if not re.fullmatch(r"bookhelper-undo-[0-9a-f]{32}-[0-9]+\\.epub", reference):
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
