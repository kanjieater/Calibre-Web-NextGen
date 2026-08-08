# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small server-side adapter for a deployment-provided Shelfmark instance."""

import os
import json as json_module
import threading
from urllib.parse import urlsplit
from urllib.parse import quote

import requests

from .. import logger

log = logger.create()

DEFAULT_TIMEOUT = (5, 30)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class ShelfmarkError(RuntimeError):
    def __init__(self, message, status=502, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class ShelfmarkNotConfigured(ShelfmarkError):
    def __init__(self):
        super().__init__("Store acquisition service is not configured", status=503)


class ShelfmarkCredentialTransportUnsupported(ShelfmarkError):
    pass


class ShelfmarkClient:
    _shared = None
    _shared_lock = threading.Lock()

    def __init__(self, base_url=None, username=None, password=None, session=None,
                 timeout=DEFAULT_TIMEOUT):
        self.base_url = (base_url if base_url is not None else
                         os.environ.get("CWNG_SHELFMARK_URL", "")).strip().rstrip("/")
        self.username = (username if username is not None else
                         os.environ.get("CWNG_SHELFMARK_USERNAME", "")).strip()
        self.password = (password if password is not None else
                         os.environ.get("CWNG_SHELFMARK_PASSWORD", ""))
        self.session = session or requests.Session()
        self.timeout = timeout
        self._authenticated = False
        self._request_lock = threading.RLock()
        logger.register_sensitive_value(self.password)
        if self.base_url:
            parsed = urlsplit(self.base_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ShelfmarkError("CWNG_SHELFMARK_URL must be an absolute HTTP(S) URL", 503)

    @classmethod
    def shared(cls):
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    def configured(self):
        return bool(self.base_url and self.username and self.password)

    def _login(self):
        if not self.configured():
            raise ShelfmarkNotConfigured()
        try:
            response = self.session.post(
                self.base_url + "/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise ShelfmarkError("Store acquisition service is unavailable", 502) from exc
        try:
            payload = self._json(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if response.status_code >= 400 or not isinstance(payload, dict) or not payload.get("success"):
            raise ShelfmarkError("Store acquisition service authentication failed", 502)
        self._authenticated = True

    @staticmethod
    def _json(response):
        content_length = getattr(response, "headers", {}).get("Content-Length")
        try:
            if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                raise ShelfmarkError("Store acquisition service response is too large", 502)
        except (TypeError, ValueError):
            pass
        iter_content = getattr(response, "iter_content", None)
        if callable(iter_content):
            body = bytearray()
            for chunk in iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ShelfmarkError("Store acquisition service response is too large", 502)
            try:
                return json_module.loads(body.decode("utf-8"))
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ShelfmarkError(
                    "Store acquisition service returned an invalid response", 502) from exc
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray)) and len(content) > MAX_RESPONSE_BYTES:
            raise ShelfmarkError("Store acquisition service response is too large", 502)
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise ShelfmarkError("Store acquisition service returned an invalid response", 502) from exc

    def _request(self, method, path, *, params=None, json=None, retry_auth=True):
        with self._request_lock:
            if not self._authenticated:
                self._login()
            try:
                response = self.session.request(
                    method, self.base_url + path, params=params, json=json,
                    timeout=self.timeout, stream=True)
            except requests.RequestException as exc:
                raise ShelfmarkError("Store acquisition service is unavailable", 502) from exc
            if response.status_code == 401 and retry_auth:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                self._authenticated = False
                self._login()
                return self._request(method, path, params=params, json=json, retry_auth=False)
            try:
                payload = self._json(response)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            if response.status_code >= 400:
                message = payload.get("error") if isinstance(payload, dict) else None
                raise ShelfmarkError(message or "Store acquisition service request failed",
                                     response.status_code, payload)
            return payload

    @staticmethod
    def provider_credential_transport(provider, credential):
        """Single boundary for a future Shelfmark per-user credential contract.

        Shelfmark's verified HTTP surface does not specify how a caller supplies
        a per-user provider key. Refuse instead of guessing a header or payload
        field. Credentials remain safely stored until that contract is defined.
        """
        if credential:
            raise ShelfmarkCredentialTransportUnsupported(
                f"Per-user credential transport is not supported for {provider}", 501)
        return {}

    def search(self, query):
        payload = self._request("GET", "/api/metadata/search", params={"query": query})
        if (not isinstance(payload, dict) or not isinstance(payload.get("books"), list) or
                len(payload["books"]) > 2000 or
                any(not isinstance(item, dict) for item in payload["books"])):
            raise ShelfmarkError("Store search service returned an invalid response", 502)
        return payload

    def releases(self, provider, book_id, source="direct_download"):
        payload = self._request("GET", "/api/releases", params={
            "provider": provider, "book_id": book_id, "source": source})
        if (not isinstance(payload, dict) or not isinstance(payload.get("releases"), list) or
                len(payload["releases"]) > 5000 or
                any(not isinstance(item, dict) for item in payload["releases"])):
            raise ShelfmarkError("Store release service returned an invalid response", 502)
        return payload

    def release_sources(self):
        payload = self._request("GET", "/api/release-sources")
        if (not isinstance(payload, list) or len(payload) > 500 or
                any(not isinstance(item, dict) for item in payload)):
            raise ShelfmarkError("Store source service returned an invalid response", 502)
        return payload

    def download(self, release):
        payload = self._request("POST", "/api/releases/download", json=release)
        if not isinstance(payload, dict):
            raise ShelfmarkError("Store download service returned an invalid response", 502)
        return payload

    def active_downloads(self):
        return self._request("GET", "/api/downloads/active")

    def status(self):
        payload = self._request("GET", "/api/status")
        if not isinstance(payload, dict):
            raise ShelfmarkError("Store status service returned an invalid response", 502)
        return payload

    def download_action(self, book_id, action):
        if action not in ("cancel", "retry"):
            raise ValueError("Unsupported download action")
        safe_book_id = quote(str(book_id), safe="")
        return self._request("POST", f"/api/download/{safe_book_id}/{action}")

    def requests(self, method="GET", payload=None):
        return self._request(method, "/api/requests", json=payload)

    def admin_requests(self):
        return self._request("GET", "/api/admin/requests")

    def admin_request_action(self, request_id, action, payload=None):
        if action not in ("fulfil", "reject"):
            raise ValueError("Unsupported request action")
        return self._request("POST", f"/api/admin/requests/{request_id}/{action}", json=payload)
