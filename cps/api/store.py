# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Store/Discover API, backed by a configured Shelfmark service."""

import json
import time

from flask import jsonify, request
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from . import api_v1
from .admin import _require_admin
from .. import constants, logger, ub
from ..cw_login import current_user
from ..experimental_features import (
    STORE_DISCOVER, FEATURES, feature_enabled, list_features, set_feature_enabled,
)
from ..services.shelfmark import ShelfmarkClient, ShelfmarkError
from ..services.store_credentials import (
    MAX_CREDENTIAL_LENGTH, PROVIDER_DESCRIPTORS, PROVIDERS, StoreCredentialError,
    credential_status, revoke_credential,
    get_plaintext_for_outbound, upsert_credential, validate_provider,
)
from ..usermanagement import login_required_if_no_ano


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _store_guard():
    # Use a 404 for both off and unauthorized so a default installation has no
    # discoverable Store surface at all.
    if not feature_enabled(STORE_DISCOVER.key):
        return _err("not_found", "Not found", 404)
    if (not current_user.is_authenticated or current_user.is_anonymous or
            not current_user.role_store_access()):
        return _err("not_found", "Not found", 404)
    return None


def _adapter_call(call):
    try:
        return jsonify(call())
    except ShelfmarkError as exc:
        return _err("shelfmark_error", str(exc), exc.status)


def _required_text(value, name, max_length=512):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{name} is too long")
    return value


def _selected_acquisition(data):
    """Validate two explicit selections; this function never indexes search results."""
    if not isinstance(data, dict):
        raise ValueError("A JSON object is required")
    work = data.get("work")
    release = data.get("release")
    if not isinstance(work, dict) or not isinstance(release, dict):
        raise ValueError("Explicit work and release selections are required")
    selected_work = {
        "provider": _required_text(work.get("provider"), "work.provider", 64),
        "provider_id": _required_text(str(work.get("provider_id") or ""), "work.provider_id"),
    }
    extra = release.get("extra") if isinstance(release.get("extra"), dict) else {}
    selected_release = {
        "source": _required_text(release.get("source"), "release.source", 64),
        "source_id": _required_text(str(release.get("source_id") or ""), "release.source_id"),
        "title": _required_text(release.get("title"), "release.title"),
        "format": _required_text(release.get("format"), "release.format", 32),
        "size": release.get("size"),
        "extra": extra,
    }
    forbidden = {"credential", "password", "secret", "api_key", "token", "authorization"}

    def validate_extra(value, depth=0):
        if depth > 6:
            raise ValueError("release.extra is too deeply nested")
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden:
                    raise ValueError("release.extra contains a forbidden sensitive field")
                validate_extra(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 256:
                raise ValueError("release.extra contains too many items")
            for child in value:
                validate_extra(child, depth + 1)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("release.extra contains an unsupported value")

    validate_extra(selected_release["extra"])
    if len(json.dumps(selected_release["extra"], separators=(",", ":"))) > 65536:
        raise ValueError("release.extra is too large")
    if any(str(key).lower() in forbidden for key in selected_release["extra"]):
        raise ValueError("release.extra contains a forbidden sensitive field")
    release_provider = _required_text(release.get("provider"), "release.provider", 64)
    release_book_id = _required_text(
        str(release.get("book_id") or release.get("provider_id") or ""),
        "release.book_id")
    if release_provider != selected_work["provider"]:
        raise ValueError("Selected release does not belong to the selected work provider")
    if release_book_id != selected_work["provider_id"]:
        raise ValueError("Selected release does not belong to the selected work")
    return selected_work, selected_release


def _prepare_provider_credential(client, user_id, provider):
    """Decrypt at the last possible moment and hand it only to the transport boundary."""
    if provider not in PROVIDERS:
        return
    credential = get_plaintext_for_outbound(user_id, provider)
    with logger.sensitive_value_scope(credential):
        client.provider_credential_transport(provider, credential)


def _request_items(payload):
    if isinstance(payload, list):
        items, container = payload, None
    if isinstance(payload, dict) and isinstance(payload.get("requests"), list):
        items, container = payload["requests"], "requests"
    elif not isinstance(payload, list):
        raise ShelfmarkError("Store request service returned an invalid response", 502)
    if len(items) > 5000 or any(not isinstance(item, dict) for item in items):
        raise ShelfmarkError("Store request service returned an invalid response", 502)
    return items, container


def _request_id(row):
    if not isinstance(row, dict):
        return None
    value = row.get("id", row.get("request_id"))
    return str(value) if value is not None else None


def _save_request_mapping(payload, user_id, work, release):
    request_id = _request_id(payload)
    if request_id is None:
        raise ShelfmarkError("Store request response did not include a request id", 502)
    audit_release = {key: release.get(key) for key in
                     ("source", "source_id", "title", "format", "size")}
    def build_mapping():
        return ub.StoreRequestMapping(shelfmark_request_id=request_id,
                                      user_id=int(user_id), work=dict(work),
                                      release=audit_release)
    try:
        _commit_store_mapping(build_mapping)
    except IntegrityError:
        existing = (ub.session.query(ub.StoreRequestMapping)
                    .filter(ub.StoreRequestMapping.shelfmark_request_id == request_id).first())
        if (existing is None or existing.user_id != int(user_id) or
                existing.work != dict(work) or existing.release != audit_release):
            raise ShelfmarkError(
                "Store request was queued but ownership tracking conflicted", 503)
    return payload


def _upstream_book_id(payload):
    if not isinstance(payload, dict):
        return None
    # Shelfmark's action contract names book_id specifically. A generic `id`
    # may identify a request or queue row and must never authorize an action.
    value = payload.get("book_id")
    return str(value) if value is not None and str(value).strip() else None


def _save_download_mapping(user_id, release, payload):
    def build_mapping():
        return ub.StoreDownloadMapping(
            user_id=int(user_id),
            source=release["source"],
            source_id=release["source_id"],
            upstream_book_id=_upstream_book_id(payload),
            title=release["title"],
            format=release["format"],
            size=str(release["size"]) if release.get("size") is not None else None,
        )
    _commit_store_mapping(build_mapping)
    return payload


def _commit_store_mapping(factory):
    """Bounded SQLite-lock retry for ownership written after an upstream side effect."""
    for attempt in range(3):
        row = factory()
        ub.session.add(row)
        try:
            ub.session.commit()
            return row
        except OperationalError as exc:
            ub.session.rollback()
            if "locked" not in str(exc).lower() or attempt == 2:
                raise ShelfmarkError(
                    "Store item was queued but local ownership tracking failed", 503) from exc
            time.sleep(0.05 * (attempt + 1))
        except IntegrityError:
            ub.session.rollback()
            raise
        except Exception as exc:
            ub.session.rollback()
            raise ShelfmarkError(
                "Store item was queued but local ownership tracking failed", 503) from exc


def _download_items(payload):
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        for key in ("downloads", "items"):
            if isinstance(payload.get(key), list):
                return payload[key], key
    raise ShelfmarkError("Store download service returned an invalid response", 502)


def _download_identity(item):
    if not isinstance(item, dict):
        return None, None, None
    release = item.get("release") if isinstance(item.get("release"), dict) else {}
    # Only Shelfmark's explicit book_id is actionable. Generic queue/request
    # row IDs may be displayed but must never be backfilled as action authority.
    book_id = item.get("book_id")
    source = item.get("source", release.get("source"))
    source_id = item.get("source_id", release.get("source_id"))
    return (str(book_id) if book_id is not None else None,
            str(source) if source is not None else None,
            str(source_id) if source_id is not None else None)


def _owned_active_downloads(payload, user_id):
    items, container_key = _download_items(payload)
    mappings = (ub.session.query(ub.StoreDownloadMapping)
                .filter(ub.StoreDownloadMapping.user_id == int(user_id)).all())
    owned = []
    for item in items:
        book_id, source, source_id = _download_identity(item)
        match = next((row for row in mappings if (
            (row.upstream_book_id is not None and book_id == row.upstream_book_id) or
            (row.upstream_book_id is None and source and source_id and
             row.source == source and row.source_id == source_id)
        )), None)
        if match is None:
            continue
        owned.append(item)
        if book_id and not match.upstream_book_id:
            _bind_download_book_id(match, book_id)
    if container_key:
        result = dict(payload)
        result[container_key] = owned
        return result
    return owned


def _bind_download_book_id(mapping, book_id):
    """Best-effort durable action binding; polling must never poison ub.session."""
    for attempt in range(3):
        mapping.upstream_book_id = book_id
        try:
            ub.session.commit()
            return True
        except OperationalError as exc:
            ub.session.rollback()
            if "locked" not in str(exc).lower() or attempt == 2:
                return False
            time.sleep(0.05 * (attempt + 1))
        except SQLAlchemyError:
            ub.session.rollback()
            return False
    return False


def _create_user_request(client, user_id, work, release):
    payload = client.requests("POST", {"work": work, "release": release})
    return _save_request_mapping(payload, user_id, work, release)


@api_v1.route("/store")
@login_required_if_no_ano
def store_bootstrap():
    guard = _store_guard()
    if guard:
        return guard
    return jsonify({"enabled": True,
                    "auto_approve": current_user.role_store_auto_approve(),
                    "credential_providers": PROVIDER_DESCRIPTORS})


@api_v1.route("/store/search")
@login_required_if_no_ano
def store_search():
    guard = _store_guard()
    if guard:
        return guard
    try:
        query = _required_text(request.args.get("query"), "query")
    except ValueError as exc:
        return _err("invalid_request", str(exc), 400)
    return _adapter_call(lambda: ShelfmarkClient.shared().search(query))


@api_v1.route("/store/releases")
@login_required_if_no_ano
def store_releases():
    guard = _store_guard()
    if guard:
        return guard
    try:
        provider = _required_text(request.args.get("provider"), "provider", 64)
        book_id = _required_text(request.args.get("book_id"), "book_id")
        source = _required_text(request.args.get("source", "direct_download"), "source", 64)
    except ValueError as exc:
        return _err("invalid_request", str(exc), 400)
    return _adapter_call(lambda: ShelfmarkClient.shared().releases(provider, book_id, source))


@api_v1.route("/store/sources")
@login_required_if_no_ano
def store_sources():
    guard = _store_guard()
    if guard:
        return guard
    return _adapter_call(lambda: ShelfmarkClient.shared().release_sources())


@api_v1.route("/store/active")
@login_required_if_no_ano
def store_active():
    guard = _store_guard()
    if guard:
        return guard
    try:
        payload = ShelfmarkClient.shared().active_downloads()
        return jsonify(_owned_active_downloads(payload, current_user.id))
    except ShelfmarkError as exc:
        return _err("shelfmark_error", str(exc), exc.status)


@api_v1.route("/store/status")
@login_required_if_no_ano
def store_status():
    guard = _store_guard()
    if guard:
        return guard
    return _adapter_call(lambda: ShelfmarkClient.shared().status())


@api_v1.route("/store/downloads/<path:book_id>/<action>", methods=["POST"])
@login_required_if_no_ano
def store_download_action(book_id, action):
    guard = _store_guard()
    if guard:
        return guard
    if action not in ("cancel", "retry"):
        return _err("invalid_request", "Unsupported download action", 400)
    try:
        book_id = _required_text(book_id, "book_id")
    except ValueError as exc:
        return _err("invalid_request", str(exc), 400)
    owned = (ub.session.query(ub.StoreDownloadMapping.id)
             .filter(ub.StoreDownloadMapping.user_id == current_user.id,
                     ub.StoreDownloadMapping.upstream_book_id == book_id).first())
    if owned is None:
        return _err("not_found", "Not found", 404)
    return _adapter_call(lambda: ShelfmarkClient.shared().download_action(book_id, action))


@api_v1.route("/store/acquire", methods=["POST"])
@login_required_if_no_ano
def store_acquire():
    guard = _store_guard()
    if guard:
        return guard
    try:
        work, release = _selected_acquisition(request.get_json(silent=True))
    except ValueError as exc:
        return _err("invalid_selection", str(exc), 400)
    client = ShelfmarkClient.shared()
    try:
        _prepare_provider_credential(client, current_user.id, work["provider"])
    except (StoreCredentialError, ShelfmarkError) as exc:
        status = exc.status if isinstance(exc, ShelfmarkError) else 400
        return _err("credential_transport", str(exc), status)
    if current_user.role_store_auto_approve():
        try:
            payload = client.download(release)
            _save_download_mapping(current_user.id, release, payload)
        except ShelfmarkError as exc:
            duplicate = (exc.status == 500 and isinstance(exc.payload, dict) and
                         exc.payload.get("error") == "Release is already in the download queue")
            if duplicate:
                return jsonify({"status": "already_queued", "already_queued": True,
                                "mode": "download"})
            return _err("shelfmark_error", str(exc), exc.status)
        return jsonify(dict(payload, mode="download") if isinstance(payload, dict)
                       else {"result": payload, "mode": "download"})
    try:
        payload = _create_user_request(client, current_user.id, work, release)
    except ShelfmarkError as exc:
        return _err("shelfmark_error", str(exc), exc.status)
    return jsonify(dict(payload, mode="request") if isinstance(payload, dict)
                   else {"result": payload, "mode": "request"})


@api_v1.route("/store/requests", methods=["GET", "POST"])
@login_required_if_no_ano
def store_requests():
    guard = _store_guard()
    if guard:
        return guard
    if request.method == "GET":
        try:
            payload = ShelfmarkClient.shared().requests()
            items, container_key = _request_items(payload)
            owned_ids = {row[0] for row in (ub.session.query(
                ub.StoreRequestMapping.shelfmark_request_id)
                .filter(ub.StoreRequestMapping.user_id == current_user.id).all())}
            filtered = [row for row in items if _request_id(row) in owned_ids]
            if container_key:
                result = dict(payload)
                result[container_key] = filtered
            else:
                result = filtered
            return jsonify(result)
        except ShelfmarkError as exc:
            return _err("shelfmark_error", str(exc), exc.status)
    try:
        work, release = _selected_acquisition(request.get_json(silent=True))
    except ValueError as exc:
        return _err("invalid_selection", str(exc), 400)
    client = ShelfmarkClient.shared()
    try:
        _prepare_provider_credential(client, current_user.id, work["provider"])
        return jsonify(_create_user_request(client, current_user.id, work, release))
    except (StoreCredentialError, ShelfmarkError) as exc:
        status = exc.status if isinstance(exc, ShelfmarkError) else 400
        return _err("shelfmark_error", str(exc), status)


@api_v1.route("/store/admin/requests")
@login_required_if_no_ano
def store_admin_requests():
    if not feature_enabled(STORE_DISCOVER.key):
        return _err("not_found", "Not found", 404)
    admin_guard = _require_admin()
    if admin_guard:
        return admin_guard
    try:
        payload = ShelfmarkClient.shared().admin_requests()
        items, container_key = _request_items(payload)
        mappings = {row.shelfmark_request_id: row for row in
                    ub.session.query(ub.StoreRequestMapping).all()}
        user_ids = {row.user_id for row in mappings.values()}
        users = {row.id: row.name for row in
                 ub.session.query(ub.User).filter(ub.User.id.in_(user_ids)).all()} if user_ids else {}
        enriched = []
        for item in items:
            mapping = mappings.get(_request_id(item))
            if mapping is None:
                entry = dict(item)
                entry["requester"] = None
                entry["attribution_missing"] = True
                enriched.append(entry)
                continue
            entry = dict(item)
            entry["requester"] = {"id": mapping.user_id,
                                  "name": users.get(mapping.user_id, "")}
            entry.setdefault("work", mapping.work)
            entry.setdefault("release", mapping.release)
            enriched.append(entry)
        if container_key:
            result = dict(payload)
            result[container_key] = enriched
        else:
            result = enriched
        return jsonify(result)
    except ShelfmarkError as exc:
        return _err("shelfmark_error", str(exc), exc.status)


@api_v1.route("/store/admin/requests/<int:request_id>/<action>", methods=["POST"])
@login_required_if_no_ano
def store_admin_request_action(request_id, action):
    if not feature_enabled(STORE_DISCOVER.key):
        return _err("not_found", "Not found", 404)
    admin_guard = _require_admin()
    if admin_guard:
        return admin_guard
    if action not in ("fulfil", "reject"):
        return _err("invalid_request", "Unsupported request action", 400)
    try:
        payload = ShelfmarkClient.shared().admin_request_action(request_id, action, None)
        if action == "fulfil":
            request_mapping = (ub.session.query(ub.StoreRequestMapping)
                               .filter(ub.StoreRequestMapping.shelfmark_request_id == str(request_id))
                               .first())
            if request_mapping is not None:
                _save_download_mapping(request_mapping.user_id, request_mapping.release, payload)
        return jsonify(payload)
    except ShelfmarkError as exc:
        return _err("shelfmark_error", str(exc), exc.status)


@api_v1.route("/store/credentials")
@login_required_if_no_ano
def store_credentials_list():
    guard = _store_guard()
    if guard:
        return guard
    rows = (ub.session.query(ub.StoreCredential)
            .filter(ub.StoreCredential.user_id == current_user.id)
            .order_by(ub.StoreCredential.provider.asc()).all())
    return jsonify({"items": [credential_status(row) for row in rows]})


@api_v1.route("/store/credentials/<provider>", methods=["POST", "DELETE"])
@login_required_if_no_ano
def store_credential(provider):
    guard = _store_guard()
    if guard:
        return guard
    try:
        provider = validate_provider(provider)
        if request.method == "DELETE":
            revoke_credential(current_user.id, provider)
            return "", 204
        if request.content_length and request.content_length > MAX_CREDENTIAL_LENGTH + 1024:
            return _err("invalid_credential", "Credential request is too large", 413)
        data = request.get_json(silent=True) or {}
        plaintext = data.get("credential")
        with logger.sensitive_value_scope(plaintext):
            row = upsert_credential(current_user.id, provider, plaintext)
            return jsonify(credential_status(row))
    except StoreCredentialError as exc:
        ub.session.rollback()
        return _err("invalid_credential", str(exc), 400)
    except SQLAlchemyError:
        ub.session.rollback()
        return _err("credential_storage", "Could not update Store credential", 503)


@api_v1.route("/admin/experimental")
@login_required_if_no_ano
def admin_experimental_list():
    guard = _require_admin()
    if guard:
        return guard
    return jsonify({"items": list_features()})


@api_v1.route("/admin/experimental/<key>", methods=["POST"])
@login_required_if_no_ano
def admin_experimental_update(key):
    guard = _require_admin()
    if guard:
        return guard
    if key not in FEATURES:
        return _err("not_found", "Experimental feature not found", 404)
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("enabled"), bool):
        return _err("invalid_request", "enabled must be a boolean", 400)
    enabled = set_feature_enabled(key, data["enabled"])
    return jsonify({"key": key, "enabled": enabled})


@api_v1.route("/admin/store/credentials/<int:user_id>/<provider>", methods=["DELETE"])
@login_required_if_no_ano
def admin_store_credential_revoke(user_id, provider):
    if not feature_enabled(STORE_DISCOVER.key):
        return _err("not_found", "Not found", 404)
    guard = _require_admin()
    if guard:
        return guard
    try:
        validate_provider(provider)
        revoke_credential(user_id, provider)
    except StoreCredentialError as exc:
        ub.session.rollback()
        return _err("invalid_credential", str(exc), 400)
    return "", 204
