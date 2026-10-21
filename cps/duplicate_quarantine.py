# SPDX-License-Identifier: GPL-3.0-or-later
"""Crash-recoverable, intact book-directory quarantine for guarded duplicates.

Inspired by DramaCD's transaction-owned potentialDupes quarantine. CWA alone
mutates the Calibre filesystem. Bookhelper owns the duplicate decision.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

ENV = "BOOKHELPER_DUPLICATE_QUARANTINE_DIR"
MARKER = ".bookhelper-quarantine-owner.json"


class QuarantineError(RuntimeError):
    pass


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".bookhelper-journal-", delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)
    _sync_dir(path.parent)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _tree_inventory(directory):
    if not directory.is_dir() or directory.is_symlink():
        raise QuarantineError("source/quarantine is not a real directory")
    result = {}
    for base, subdirs, files in os.walk(directory, followlinks=False):
        base = Path(base)
        for name in subdirs + files:
            entry = base / name
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise QuarantineError("symlink/special file forbidden in quarantine")
        for name in files:
            path = base / name
            rel = path.relative_to(directory).as_posix()
            if rel == MARKER:
                continue
            result[rel] = {"sha256": _sha(path), "size": path.stat().st_size}
    if not result:
        raise QuarantineError("cannot quarantine an empty book directory")
    return result


def _origin(calibre_root, relative):
    root = Path(calibre_root).resolve(strict=True)
    pieces = Path(relative).parts
    if Path(relative).is_absolute() or len(pieces) != 2 or any(
            p in ("", ".", "..", "/", "\\\\") for p in pieces):
        raise QuarantineError("invalid Calibre relative book path")
    origin = root.joinpath(*pieces)
    if origin.is_symlink() or origin.parent.is_symlink():
        raise QuarantineError("symlink in Calibre path")
    if origin.resolve(strict=True).parent.parent != root:
        raise QuarantineError("book path escaped Calibre root")
    return root, origin


def _quarantine_root(calibre_root):
    destination = os.environ.get(ENV)
    if not destination or not os.path.isabs(destination):
        raise QuarantineError(f"{ENV} must be a persistent absolute directory")
    root = Path(calibre_root).resolve(strict=True)
    qroot = Path(destination).resolve()
    if qroot == root or root in qroot.parents:
        raise QuarantineError("quarantine must be outside the active Calibre library")
    qroot.mkdir(parents=True, exist_ok=True)
    return qroot


def _paths(qroot, name):
    return (qroot / name, qroot / (".stage-" + name),
            qroot / ".transactions" / (name + ".json"))


def publish(calibre_root, book_path, source_id, source_uuid, keeper_uuid):
    """Publish verified original bytes *before* metadata.db deletes the row.

    Repeated calls after a crash reuse the existing transaction only if every
    book/UUID/path/hash and marker matches. No ambiguous overwrite is allowed.
    """
    root, origin = _origin(calibre_root, book_path)
    qroot = _quarantine_root(root)
    if not source_uuid or not keeper_uuid:
        raise QuarantineError("stable source and keeper UUIDs required")
    identity = f"{source_id}|{source_uuid}|{keeper_uuid}|{book_path}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    name = f"{source_id}-{key}"
    final, stage, journal_path = _paths(qroot, name)
    if (origin / MARKER).exists():
        raise QuarantineError("original directory contains reserved ownership marker")
    observed = _tree_inventory(origin)
    expected = {"key": key, "book_id": source_id, "source_uuid": source_uuid,
                "keeper_uuid": keeper_uuid, "book_path": book_path,
                "inventory": observed, "state": "preparing"}
    if journal_path.exists():
        prior = json.loads(journal_path.read_text(encoding="utf-8"))
        for field in ("key", "book_id", "source_uuid", "keeper_uuid",
                      "book_path", "inventory"):
            if prior.get(field) != expected[field]:
                raise QuarantineError("existing journal identity/hash conflict")
    else:
        prior = expected
        _atomic_json(journal_path, prior)
    if final.exists():
        _verify(final, expected)
    else:
        if stage.exists():
            marker = stage / MARKER
            if not marker.is_file() or json.loads(marker.read_text()).get("key") != key:
                raise QuarantineError("staging path exists without ownership marker")
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        _atomic_json(stage / MARKER, {"key": key})
        try:
            # A stage marker is written before copying, enabling crash recovery.
            for base, dirs, files in os.walk(origin, followlinks=False):
                dest = stage / Path(base).relative_to(origin)
                for d in dirs:
                    if (Path(base) / d).is_symlink():
                        raise QuarantineError("symlink in source")
                    (dest / d).mkdir()
                for name in files:
                    path = Path(base) / name
                    if path.is_symlink() or not path.is_file():
                        raise QuarantineError("symlink/special source entry")
                    target = dest / name
                    with open(path, "rb") as inp, open(target, "xb") as out:
                        shutil.copyfileobj(inp, out, 1024 * 1024)
                        out.flush()
                        os.fsync(out.fileno())
                _sync_dir(dest)
            _verify(stage, expected)
            os.rename(stage, final)
            _sync_dir(qroot)
        except BaseException:
            # Keep journal and owned stage. Retrying discards only the stage
            # with the matching marker; the original live book is unchanged.
            raise
    prior["state"] = "published"
    _atomic_json(journal_path, prior)
    return {"key": key, "journal": str(journal_path), "original": str(origin),
            "destination": str(final), "inventory": observed}


def _verify(directory, expected):
    marker = directory / MARKER
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != {"key": expected["key"]}:
        raise QuarantineError("unowned quarantine path")
    if _tree_inventory(directory) != expected["inventory"]:
        raise QuarantineError("quarantined original differs from verified source")


def finish(ticket):
    """Only after Calibre DB commit, clean the now-orphaned original directory.

    A failure to remove the original leaves both copies intact; retrying this
    step verifies ownership and content rather than invoking DB deletion again.
    """
    journal = Path(ticket["journal"])
    item = json.loads(journal.read_text(encoding="utf-8"))
    final = Path(ticket["destination"])
    origin = Path(ticket["original"])
    _verify(final, item)
    item["state"] = "db_committed"
    _atomic_json(journal, item)
    if origin.exists():
        if _tree_inventory(origin) != item["inventory"]:
            raise QuarantineError("orphaned original changed; preserved for manual recovery")
        shutil.rmtree(origin)
        _sync_dir(origin.parent)
    item["state"] = "complete"
    _atomic_json(journal, item)
    return {"quarantine_id": item["key"], "state": "complete",
            "inventory": item["inventory"]}


def cancel(ticket):
    """Compensate an explicitly rolled-back metadata deletion, never source."""
    journal = Path(ticket["journal"])
    if not journal.exists():
        return
    item = json.loads(journal.read_text(encoding="utf-8"))
    if item["state"] not in ("preparing", "published"):
        raise QuarantineError("cannot undo an already committed quarantine")
    original = Path(ticket["original"])
    if not original.exists() or _tree_inventory(original) != item["inventory"]:
        raise QuarantineError("cannot cancel quarantine without intact live source")
    destination = Path(ticket["destination"])
    if destination.exists():
        _verify(destination, item)
        shutil.rmtree(destination)
        _sync_dir(destination.parent)
    item["state"] = "aborted"
    _atomic_json(journal, item)



def reconcile(calibre_root, book_lookup, max_transactions=32):
    """Finish orphan cleanup after a crash following a successful Calibre commit.

    Caller holds CWA's metadata writer exclusion while book_lookup inspects
    the real metadata.db. Never remove a directory still owned by a live book,
    and never infer absence from an unavailable DB lookup.
    """
    qroot = _quarantine_root(calibre_root)
    journals = qroot / ".transactions"
    if not journals.exists():
        return {"finished": [], "pending": [], "blocked": []}
    result = {"finished": [], "pending": [], "blocked": []}
    count = 0
    for path in sorted(journals.glob("*.json")):
        if count >= max_transactions:
            break
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("state") in ("complete", "aborted"):
            continue
        count += 1
        key = entry.get("key")
        if (not isinstance(key, str)
                or not re.fullmatch(r"[0-9a-f]{32}", key)
                or not isinstance(entry.get("book_id"), int)):
            result["blocked"].append({"journal": path.name, "reason": "invalid_journal"})
            continue
        existing = book_lookup(entry["book_id"])
        if existing is not None:
            # A new unrelated work may reuse the numeric ID. In either case
            # an occupied ID is not grounds to remove arbitrary live files.
            result["pending"].append({
                "key": key, "reason": "book_id_currently_occupied"})
            continue
        destination = qroot / f"{entry['book_id']}-{key}"
        original = Path(calibre_root).resolve() / entry["book_path"]
        ticket = {"key": key, "journal": str(path),
                  "destination": str(destination), "original": str(original)}
        try:
            finish(ticket)
            result["finished"].append(key)
        except (QuarantineError, OSError, ValueError) as exc:
            result["blocked"].append({"key": key, "reason": str(exc)})
    return result
