# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""AES-256-GCM storage for per-user Store provider credentials."""

import base64
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError

from .. import constants, logger, ub

ENV_NAME = "CWNG_STORE_SECRET_KEY"
KEY_FILENAME = "cwng-store-secret.key"
KEY_VERSION = 1
MAX_CREDENTIAL_LENGTH = 16 * 1024
PROVIDER_DESCRIPTORS = (
    {"key": "annas_archive", "label": "Anna's Archive"},
    {"key": "libgen", "label": "LibGen"},
    {"key": "zlibrary", "label": "Z-Library"},
    {"key": "welib", "label": "welib"},
    {"key": "prowlarr", "label": "Prowlarr"},
    {"key": "newznab", "label": "Newznab"},
    {"key": "irc", "label": "IRC"},
    {"key": "audiobookbay", "label": "AudiobookBay"},
)
PROVIDERS = frozenset(item["key"] for item in PROVIDER_DESCRIPTORS)


class StoreCredentialError(ValueError):
    pass


def _decode_key(value):
    value = value.strip()
    try:
        key = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError, base64.binascii.Error) as exc:
        raise StoreCredentialError(f"{ENV_NAME} must be URL-safe base64") from exc
    if len(key) != 32:
        raise StoreCredentialError(f"{ENV_NAME} must decode to exactly 32 bytes")
    if _encode_key(key) != value:
        raise StoreCredentialError(f"{ENV_NAME} must use canonical URL-safe base64")
    return key


def _encode_key(key):
    return base64.urlsafe_b64encode(key).decode("ascii")


def _atomic_create_key(path):
    key = AESGCM.generate_key(bit_length=256)
    fd, temporary = tempfile.mkstemp(prefix=".cwng-store-secret-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (_encode_key(key) + "\n").encode("ascii"))
        os.fsync(fd)
        os.close(fd)
        fd = None
        # link(2) publishes the fully written inode without replacing a key
        # another process may have won the first-boot race to create.
        os.link(temporary, path)
        # Persist the directory entry as well as the file contents. Losing this
        # key after a crash would make every stored credential undecryptable.
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return key


def _read_key_file(path):
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StoreCredentialError("Store key path must be a regular file")
        os.fchmod(fd, 0o600)
        encoded = os.read(fd, 256).decode("ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise StoreCredentialError("Store key file is unreadable") from exc
    finally:
        if fd is not None:
            os.close(fd)
    return encoded


def load_master_key(config_dir=None):
    env_value = os.environ.get(ENV_NAME, "").strip()
    if env_value:
        logger.register_sensitive_value(env_value)
        return _decode_key(env_value)

    root = Path(config_dir or constants.CONFIG_DIR)
    root.mkdir(parents=True, exist_ok=True)
    path = root / KEY_FILENAME
    try:
        key = _atomic_create_key(path)
    except FileExistsError:
        encoded = _read_key_file(path)
        logger.register_sensitive_value(encoded)
        key = _decode_key(encoded)
    return key


def _aad(user_id, provider, key_version=KEY_VERSION):
    return f"cwng-store:{int(user_id)}:{provider}:{int(key_version)}".encode("utf-8")


def validate_provider(provider):
    normalized = (provider or "").strip().lower()
    if normalized not in PROVIDERS:
        raise StoreCredentialError("Unsupported Store credential provider")
    return normalized


def encrypt_value(user_id, provider, plaintext, key=None, key_version=KEY_VERSION):
    provider = validate_provider(provider)
    if not isinstance(plaintext, str) or not plaintext.strip():
        raise StoreCredentialError("Credential must be a non-empty string")
    plaintext = plaintext.strip()
    if len(plaintext) > MAX_CREDENTIAL_LENGTH:
        raise StoreCredentialError("Credential is too long")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key or load_master_key()).encrypt(
        nonce, plaintext.encode("utf-8"), _aad(user_id, provider, key_version))
    return ciphertext, nonce


def decrypt_row(row, key=None):
    if row.key_version != KEY_VERSION:
        raise StoreCredentialError("Unsupported Store credential key version")
    plaintext = AESGCM(key or load_master_key()).decrypt(
        bytes(row.nonce), bytes(row.ciphertext),
        _aad(row.user_id, row.provider, row.key_version)).decode("utf-8")
    return plaintext


def credential_status(row):
    return {
        "provider": row.provider,
        "configured": True,
        "last4": row.last4,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def upsert_credential(user_id, provider, plaintext, session=None, key=None):
    session = session or ub.session
    provider = validate_provider(provider)
    ciphertext, nonce = encrypt_value(user_id, provider, plaintext, key=key)
    row = (session.query(ub.StoreCredential)
           .filter(ub.StoreCredential.user_id == int(user_id),
                   ub.StoreCredential.provider == provider).first())
    now = datetime.now(timezone.utc)
    if row is None:
        row = ub.StoreCredential(user_id=int(user_id), provider=provider,
                                 created_at=now)
        session.add(row)
    row.ciphertext = ciphertext
    row.nonce = nonce
    row.key_version = KEY_VERSION
    row.last4 = plaintext.strip()[-4:]
    row.updated_at = now
    try:
        session.commit()
        return row
    except IntegrityError as exc:
        # Two first writes can race past the initial SELECT. The unique
        # (user, provider) constraint chooses one winner; update that row with
        # this request's freshly encrypted value instead of leaking a 500.
        session.rollback()
        winner = (session.query(ub.StoreCredential)
                  .filter(ub.StoreCredential.user_id == int(user_id),
                          ub.StoreCredential.provider == provider).first())
        if winner is None:
            raise exc
        winner.ciphertext = ciphertext
        winner.nonce = nonce
        winner.key_version = KEY_VERSION
        winner.last4 = plaintext[-4:]
        winner.updated_at = now
        session.commit()
        return winner


def revoke_credential(user_id, provider, session=None):
    session = session or ub.session
    provider = validate_provider(provider)
    count = (session.query(ub.StoreCredential)
             .filter(ub.StoreCredential.user_id == int(user_id),
                     ub.StoreCredential.provider == provider)
             .delete(synchronize_session=False))
    session.commit()
    return bool(count)


def get_plaintext_for_outbound(user_id, provider, session=None):
    """Decrypt only for the immediate, in-process outbound adapter call."""
    session = session or ub.session
    provider = validate_provider(provider)
    row = (session.query(ub.StoreCredential)
           .filter(ub.StoreCredential.user_id == int(user_id),
                   ub.StoreCredential.provider == provider).first())
    return decrypt_row(row) if row is not None else None
