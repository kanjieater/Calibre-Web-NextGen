# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import inspect
import json
import logging
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock

import flask
import pytest
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from cps import constants, logger, ub
from cps.services import shelfmark
from cps.services import store_credentials as credentials


def test_store_roles_are_independent_and_admin_default_off():
    assert constants.ROLE_STORE_ACCESS != constants.ROLE_STORE_AUTO_APPROVE
    assert not constants.ADMIN_USER_ROLES & constants.ROLE_STORE_ACCESS
    assert not constants.ADMIN_USER_ROLES & constants.ROLE_STORE_AUTO_APPROVE
    selected = constants.selected_roles({"store_access_role": "on"})
    assert selected & constants.ROLE_STORE_ACCESS
    assert not selected & constants.ROLE_STORE_AUTO_APPROVE


def test_experimental_registry_defaults_off_and_persists(temp_cwa_db):
    from cps.experimental_features import feature_enabled, list_features, set_feature_enabled
    assert feature_enabled("store_discover", temp_cwa_db) is False
    assert list_features(temp_cwa_db)[0]["dev_only"] is True
    assert set_feature_enabled("store_discover", True, temp_cwa_db) is True
    assert feature_enabled("store_discover", temp_cwa_db) is True


def test_experimental_registry_closes_connections_it_owns(monkeypatch):
    from cps import experimental_features
    fake = MagicMock()
    fake.get_cwa_settings.return_value = {"experimental_store_discover": 0}
    monkeypatch.setattr(experimental_features, "CWA_DB", lambda: fake)
    assert experimental_features.feature_enabled("store_discover") is False
    fake.cur.close.assert_called_once_with()
    fake.con.close.assert_called_once_with()


def test_legacy_settings_does_not_own_experimental_flag():
    source = inspect.getsource(__import__("cps.cwa_functions", fromlist=["set_cwa_settings"]).set_cwa_settings)
    assert "'experimental_store_discover'" in source
    assert source.index("'experimental_store_discover'") > source.index("skip_settings =")


def test_master_key_file_is_atomic_0600_and_env_wins(tmp_path, monkeypatch):
    monkeypatch.delenv(credentials.ENV_NAME, raising=False)
    first = credentials.load_master_key(tmp_path)
    path = tmp_path / credentials.KEY_FILENAME
    assert len(first) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert credentials.load_master_key(tmp_path) == first
    override = AESGCM.generate_key(bit_length=256)
    monkeypatch.setenv(credentials.ENV_NAME, base64.urlsafe_b64encode(override).decode())
    assert credentials.load_master_key(tmp_path) == override


def test_app_startup_initializer_creates_store_key(tmp_path, monkeypatch):
    import cps
    monkeypatch.delenv(credentials.ENV_NAME, raising=False)
    assert len(cps._initialize_store_key(str(tmp_path))) == 32
    assert stat.S_IMODE((tmp_path / credentials.KEY_FILENAME).stat().st_mode) == 0o600


def test_hidden_classic_store_roles_are_preserved(monkeypatch):
    from cps import admin
    current = constants.ROLE_VIEWER | constants.ROLE_STORE_ACCESS
    monkeypatch.setattr(admin, "feature_enabled", lambda _key: False)
    selected = admin._selected_user_edit_role({"viewer_role": "on"}, current)
    assert selected & constants.ROLE_VIEWER
    assert selected & constants.ROLE_STORE_ACCESS
    assert not selected & constants.ROLE_STORE_AUTO_APPROVE


def test_invalid_explicit_master_key_fails_without_value_in_error(tmp_path, monkeypatch):
    invalid = "unit-invalid-secret-value"
    monkeypatch.setenv(credentials.ENV_NAME, invalid)
    with pytest.raises(credentials.StoreCredentialError) as caught:
        credentials.load_master_key(tmp_path)
    assert invalid not in str(caught.value)


def test_aes_gcm_roundtrip_uses_user_provider_version_aad():
    key = AESGCM.generate_key(bit_length=256)
    ciphertext, nonce = credentials.encrypt_value(7, "annas_archive", "paid-example-token", key)
    row = SimpleNamespace(user_id=7, provider="annas_archive", key_version=1,
                          ciphertext=ciphertext, nonce=nonce)
    assert credentials.decrypt_row(row, key) == "paid-example-token"
    row.user_id = 8
    with pytest.raises(Exception):
        credentials.decrypt_row(row, key)


def test_store_credential_model_has_no_plaintext_column():
    columns = {column.name for column in ub.StoreCredential.__table__.columns}
    assert columns == {"id", "user_id", "provider", "ciphertext", "nonce",
                       "key_version", "last4", "created_at", "updated_at"}


def test_credential_upsert_persists_only_ciphertext_and_status():
    engine = create_engine("sqlite:///:memory:")
    ub.User.__table__.create(engine)
    ub.StoreCredential.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    key = AESGCM.generate_key(bit_length=256)
    row = credentials.upsert_credential(
        5, "annas_archive", "unit-paid-key-6789", session=session, key=key)
    session.expire_all()
    stored = session.query(ub.StoreCredential).one()
    assert b"unit-paid-key" not in bytes(stored.ciphertext)
    assert credentials.decrypt_row(stored, key) == "unit-paid-key-6789"
    assert credentials.credential_status(stored) == {
        "provider": "annas_archive", "configured": True,
        "last4": "6789", "updated_at": stored.updated_at.isoformat()}


def test_credential_first_write_unique_race_becomes_update():
    winner = SimpleNamespace()
    query = MagicMock()
    query.filter.return_value.first.side_effect = [None, winner]
    session = MagicMock()
    session.query.return_value = query
    session.commit.side_effect = [IntegrityError("insert", {}, Exception("unique")), None]
    result = credentials.upsert_credential(
        5, "annas_archive", "unit-racing-key-6789", session=session,
        key=AESGCM.generate_key(bit_length=256))
    assert result is winner
    session.rollback.assert_called_once_with()
    assert winner.last4 == "6789"


def test_configured_credential_reaches_only_transport_boundary(monkeypatch):
    from cps.api import store
    seen = []
    secret = "unit-only-provider-key"
    client = SimpleNamespace(provider_credential_transport=lambda provider, value:
                             seen.append((provider, value)))
    monkeypatch.setattr(store, "get_plaintext_for_outbound", lambda *_a: secret)
    store._prepare_provider_credential(client, 9, "annas_archive")
    assert seen == [("annas_archive", secret)]
    assert secret not in json.dumps(_selection())
    assert logger.redact_sensitive(secret) == secret


def test_provider_transport_refuses_configured_key_honestly():
    with pytest.raises(shelfmark.ShelfmarkCredentialTransportUnsupported) as caught:
        shelfmark.ShelfmarkClient.provider_credential_transport(
            "annas_archive", "unit-only-provider-key")
    assert caught.value.status == 501


def test_logger_filter_redacts_registered_value_and_sensitive_repr(tmp_path):
    path = tmp_path / "store.log"
    logger.setup(str(path), logging.INFO)
    secret = "unit-only-example-secret"
    logger.register_sensitive_value(secret)
    logging.getLogger("cps.store.test").warning("payload=%r", {"credential": secret})
    for handler in logging.root.handlers:
        handler.flush()
    body = path.read_text(encoding="utf-8")
    assert secret not in body
    assert "***REDACTED***" in body


def test_scoped_logger_redaction_does_not_retain_paid_key():
    secret = "unit-scoped-paid-key"
    with logger.sensitive_value_scope(secret):
        assert secret not in logger.redact_sensitive(secret)
    assert logger.redact_sensitive(secret) == secret


def test_credential_length_is_bounded():
    with pytest.raises(credentials.StoreCredentialError, match="too long"):
        credentials.encrypt_value(
            7, "annas_archive", "x" * (credentials.MAX_CREDENTIAL_LENGTH + 1),
            AESGCM.generate_key(bit_length=256))


class _Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.logins = 0
        self.requests = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.guard = threading.Lock()

    def post(self, *_args, **_kwargs):
        self.logins += 1
        return _Response(200, {"success": True})

    def request(self, method, url, **kwargs):
        with self.guard:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            time.sleep(0.002)
            self.requests.append((method, url, kwargs))
            return self.responses.pop(0) if self.responses else _Response(200, {"ok": True})
        finally:
            with self.guard:
                self.concurrent -= 1


def test_shelfmark_reauthenticates_once_on_401():
    session = _Session([_Response(401, {"error": "expired"}), _Response(200, {"books": []})])
    client = shelfmark.ShelfmarkClient("http://shelfmark.invalid", "user", "example-password", session)
    assert client.search("Dune") == {"books": []}
    assert session.logins == 2
    assert len(session.requests) == 2


def test_shelfmark_login_network_error_is_mapped():
    session = _Session()
    session.post = lambda *_a, **_k: (_ for _ in ()).throw(requests.ConnectionError("offline"))
    client = shelfmark.ShelfmarkClient("http://shelfmark.invalid", "user", "example-password", session)
    with pytest.raises(shelfmark.ShelfmarkError) as caught:
        client.status()
    assert caught.value.status == 502


def test_shelfmark_session_is_serialized_for_concurrent_calls():
    session = _Session()
    client = shelfmark.ShelfmarkClient("http://shelfmark.invalid", "user", "example-password", session)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: client.status(), range(16)))
    assert session.logins == 1
    assert session.max_concurrent == 1


def test_shelfmark_rejects_oversized_or_wrong_shape_response():
    too_large = _Response(200, {"books": []})
    too_large.headers = {"Content-Length": str(shelfmark.MAX_RESPONSE_BYTES + 1)}
    session = _Session([too_large])
    client = shelfmark.ShelfmarkClient(
        "http://shelfmark.invalid", "user", "example-password", session)
    with pytest.raises(shelfmark.ShelfmarkError, match="too large"):
        client.search("Dune")

    session = _Session([_Response(200, {"unexpected": []})])
    client = shelfmark.ShelfmarkClient(
        "http://shelfmark.invalid", "user", "example-password", session)
    with pytest.raises(shelfmark.ShelfmarkError, match="invalid response"):
        client.search("Dune")


def _user(auto=False):
    return SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=3,
        role_store_access=lambda: True,
        role_store_auto_approve=lambda: auto,
        role_admin=lambda: False,
    )


def _ctx(body):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context("/api/v1/store/acquire", method="POST", json=body)


def _selection():
    return {
        "work": {"provider": "annas_archive", "provider_id": "work-2"},
        "release": {"source": "direct_download", "source_id": "release-9",
                    "provider": "annas_archive", "book_id": "work-2",
                    "title": "Chosen edition", "format": "epub", "size": 42,
                    "extra": {"language": "en"}},
    }


def test_acquire_requires_explicit_work_and_release_and_never_indexes_results():
    from cps.api import store
    source = inspect.getsource(store.store_acquire) + inspect.getsource(store._selected_acquisition)
    assert "results[0]" not in source and "books[0]" not in source and "releases[0]" not in source
    with _ctx({"release": _selection()["release"]}), \
         patch.object(store, "current_user", _user()), \
         patch.object(store, "feature_enabled", return_value=True):
        response, status = inspect.unwrap(store.store_acquire)()
    assert status == 400
    assert json.loads(response.get_data())["error"]["code"] == "invalid_selection"


def test_acquire_requires_release_work_association_and_rejects_nested_secret():
    from cps.api import store
    mismatch = _selection()
    mismatch["release"]["book_id"] = "different-work"
    with pytest.raises(ValueError, match="does not belong"):
        store._selected_acquisition(mismatch)
    nested = _selection()
    nested["release"]["extra"] = {"metadata": {"token": "must-not-pass"}}
    with pytest.raises(ValueError, match="sensitive"):
        store._selected_acquisition(nested)


def test_request_mapping_stores_audit_projection_not_extra(monkeypatch):
    from cps.api import store
    added = []
    fake_session = SimpleNamespace(add=added.append, commit=lambda: None)
    monkeypatch.setattr(store.ub, "session", fake_session)
    work, release = store._selected_acquisition(_selection())
    store._save_request_mapping({"id": 44}, 7, work, release)
    assert added[0].release == {
        "source": "direct_download", "source_id": "release-9",
        "title": "Chosen edition", "format": "epub", "size": 42}
    assert "extra" not in added[0].release


def test_mapping_commit_retries_sqlite_lock_and_rolls_back(monkeypatch):
    from cps.api import store
    fake_session = MagicMock()
    fake_session.commit.side_effect = [
        OperationalError("commit", {}, Exception("database is locked")),
        OperationalError("commit", {}, Exception("database is locked")),
        None,
    ]
    monkeypatch.setattr(store.ub, "session", fake_session)
    monkeypatch.setattr(store.time, "sleep", lambda _delay: None)
    created = []
    result = store._commit_store_mapping(lambda: created.append(object()) or created[-1])
    assert result is created[-1]
    assert len(created) == 3
    assert fake_session.rollback.call_count == 2


def test_duplicate_upstream_500_is_benign_already_queued():
    from cps.api import store
    duplicate = shelfmark.ShelfmarkError(
        "Release is already in the download queue", 500,
        {"error": "Release is already in the download queue"})
    fake = SimpleNamespace(
        provider_credential_transport=lambda _provider, _credential: {},
        download=lambda _release: (_ for _ in ()).throw(duplicate))
    with _ctx(_selection()), patch.object(store, "current_user", _user(auto=True)), \
         patch.object(store, "feature_enabled", return_value=True), \
         patch.object(store, "get_plaintext_for_outbound", return_value=None), \
         patch.object(shelfmark.ShelfmarkClient, "shared", return_value=fake):
        response = inspect.unwrap(store.store_acquire)()
    assert response.status_code == 200
    assert json.loads(response.get_data()) == {
        "already_queued": True, "mode": "download", "status": "already_queued"}


def test_active_downloads_are_filtered_and_bind_proven_upstream_id(monkeypatch):
    from cps.api import store
    mine = SimpleNamespace(source="direct_download", source_id="release-9",
                           upstream_book_id=None)
    other = SimpleNamespace(source="direct_download", source_id="other",
                            upstream_book_id="other-book")
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.all.return_value = [mine, other]
    monkeypatch.setattr(store.ub, "session", fake_session)
    result = store._owned_active_downloads({"downloads": [
        {"book_id": "mine-book", "source": "direct_download", "source_id": "release-9"},
        {"book_id": "other-book", "source": "direct_download", "source_id": "other"},
        {"book_id": "unowned", "title": "Private title"},
    ]}, 7)
    assert [row["book_id"] for row in result["downloads"]] == ["mine-book", "other-book"]
    assert all(row["book_id"] != "unowned" for row in result["downloads"])
    assert mine.upstream_book_id == "mine-book"
    fake_session.commit.assert_called_once_with()


def test_active_binding_lock_failure_rolls_back_without_failing_poll(monkeypatch):
    from cps.api import store
    unbound = SimpleNamespace(source="direct_download", source_id="release-9",
                              upstream_book_id=None)
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.all.return_value = [unbound]
    fake_session.commit.side_effect = OperationalError(
        "commit", {}, Exception("database is locked"))
    monkeypatch.setattr(store.ub, "session", fake_session)
    monkeypatch.setattr(store.time, "sleep", lambda _delay: None)
    result = store._owned_active_downloads({"downloads": [{
        "book_id": "mine-book", "source": "direct_download", "source_id": "release-9",
    }]}, 7)
    assert result["downloads"][0]["book_id"] == "mine-book"
    assert fake_session.rollback.call_count == 3


def test_bound_download_identity_cannot_be_rebound_by_release_fields(monkeypatch):
    from cps.api import store
    bound = SimpleNamespace(source="direct_download", source_id="release-9",
                            upstream_book_id="real-book")
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.all.return_value = [bound]
    monkeypatch.setattr(store.ub, "session", fake_session)
    result = store._owned_active_downloads({"downloads": [{
        "book_id": "different-book", "source": "direct_download",
        "source_id": "release-9", "title": "Must stay private",
    }]}, 7)
    assert result == {"downloads": []}
    assert bound.upstream_book_id == "real-book"


def test_generic_active_id_is_never_bound_as_actionable_book_id(monkeypatch):
    from cps.api import store
    unbound = SimpleNamespace(source="direct_download", source_id="release-9",
                              upstream_book_id=None)
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.all.return_value = [unbound]
    monkeypatch.setattr(store.ub, "session", fake_session)
    result = store._owned_active_downloads({"downloads": [{
        "id": "queue-row", "source": "direct_download", "source_id": "release-9",
    }]}, 7)
    assert result["downloads"][0]["id"] == "queue-row"
    assert unbound.upstream_book_id is None
    fake_session.commit.assert_not_called()


def test_download_action_rejects_unowned_book_id():
    from cps.api import store
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = None
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/store/downloads/other/cancel", method="POST"), \
         patch.object(store, "current_user", _user()), \
         patch.object(store, "feature_enabled", return_value=True), \
         patch.object(store.ub, "session", fake_session), \
         patch.object(shelfmark.ShelfmarkClient, "shared") as shared:
        response, status = inspect.unwrap(store.store_download_action)("other", "cancel")
    assert status == 404
    shared.assert_not_called()


def test_store_flag_off_is_dark_404():
    from cps.api import store
    with _ctx(_selection()), patch.object(store, "current_user", _user()), \
         patch.object(store, "feature_enabled", return_value=False):
        response, status = inspect.unwrap(store.store_acquire)()
    assert status == 404
    assert json.loads(response.get_data())["error"]["code"] == "not_found"


def test_admin_credential_revoke_is_dark_while_feature_off():
    from cps.api import store
    app = flask.Flask(__name__)
    with app.test_request_context(
            "/api/v1/admin/store/credentials/7/annas_archive", method="DELETE"), \
         patch.object(store, "feature_enabled", return_value=False), \
         patch.object(store, "_require_admin") as require_admin:
        response, status = inspect.unwrap(store.admin_store_credential_revoke)(
            7, "annas_archive")
    assert status == 404
    require_admin.assert_not_called()


def test_user_request_list_filters_to_server_owned_mapping():
    from cps.api import store
    fake = SimpleNamespace(requests=lambda: [{"id": 1}, {"id": 2}])
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.all.return_value = [("1",)]
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/store/requests", method="GET"), \
         patch.object(store, "current_user", _user()), \
         patch.object(store, "feature_enabled", return_value=True), \
         patch.object(store.ub, "session", fake_session), \
         patch.object(shelfmark.ShelfmarkClient, "shared", return_value=fake):
        response = inspect.unwrap(store.store_requests)()
    assert json.loads(response.get_data()) == [{"id": 1}]


def test_admin_review_does_not_require_store_access_role():
    from cps.api import store
    admin = SimpleNamespace(is_authenticated=True, is_anonymous=False,
                            role_admin=lambda: True,
                            role_store_access=lambda: False)
    fake = SimpleNamespace(admin_requests=lambda: [])
    fake_session = MagicMock()
    fake_session.query.return_value.all.return_value = []
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/store/admin/requests"), \
         patch.object(store, "current_user", admin), \
         patch.object(store, "feature_enabled", return_value=True), \
         patch.object(store, "_require_admin", return_value=None), \
         patch.object(store.ub, "session", fake_session), \
         patch.object(shelfmark.ShelfmarkClient, "shared", return_value=fake):
        response = inspect.unwrap(store.store_admin_requests)()
    assert response.status_code == 200
    assert json.loads(response.get_data()) == []


def test_credential_status_is_write_only_boundary():
    row = SimpleNamespace(provider="annas_archive", last4="7890",
                          updated_at=None, ciphertext=b"cipher", nonce=b"nonce")
    assert credentials.credential_status(row) == {
        "provider": "annas_archive", "configured": True,
        "last4": "7890", "updated_at": None}
