#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject fuzzy translations for msgids consumed by the React SPA.

The SPA and ``msgfmt`` intentionally exclude fuzzy entries: gettext's fuzzy
value is an unreviewed similarity guess and can have a different meaning from
the current msgid.  ``scripts/update_translations.sh`` prevents new guesses
with ``msgmerge --no-fuzzy-matching``; this gate prevents an existing or
manually-added fuzzy SPA entry from silently returning to the catalogs.

``--clear`` is the one-time migration tool for legacy guesses.  It removes the
fuzzy flag *and* empties the unreviewed value, preserving the runtime's existing
English fallback while making translation status honest.  Human-reviewed
translations must be written explicitly after that migration.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PO_GLOB = "cps/translations/*/LC_MESSAGES/messages.po"
_FLAGS = re.compile(r"^#,\s*(.*)$")
_MSGID = re.compile(r'^msgid\s+(".*")$')


def _spa_msgids() -> set[str]:
    script = ROOT / "scripts" / "extract_spa_strings.py"
    spec = importlib.util.spec_from_file_location("extract_spa_strings", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.parse_anchored()


def _entry_msgid(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        match = _MSGID.match(line.rstrip("\n"))
        if not match:
            continue
        pieces = [ast.literal_eval(match.group(1))]
        for continuation in lines[index + 1 :]:
            stripped = continuation.rstrip("\n")
            if not stripped.startswith('"'):
                break
            pieces.append(ast.literal_eval(stripped))
        return "".join(pieces)
    return None


def _clear_fuzzy_entry(entry: str, spa_msgids: set[str]) -> tuple[str, str | None]:
    if entry.startswith("#~"):
        return entry, None
    lines = entry.splitlines(keepends=True)
    flag_index = None
    flags: list[str] = []
    for index, line in enumerate(lines):
        match = _FLAGS.match(line.rstrip("\n"))
        if match:
            flag_index = index
            flags = [flag.strip() for flag in match.group(1).split(",") if flag.strip()]
            break
    if flag_index is None or "fuzzy" not in flags:
        return entry, None

    msgid = _entry_msgid(lines)
    if not msgid or msgid not in spa_msgids:
        return entry, None

    flags = [flag for flag in flags if flag != "fuzzy"]
    if flags:
        lines[flag_index] = "#, " + ", ".join(flags) + "\n"
    else:
        del lines[flag_index]

    msgstr_index = next(
        (index for index, line in enumerate(lines) if line.startswith("msgstr ")),
        None,
    )
    if msgstr_index is None or any(line.startswith("msgid_plural ") for line in lines):
        raise ValueError(f"Unsupported fuzzy SPA entry shape for {msgid!r}")
    end = msgstr_index + 1
    while end < len(lines) and lines[end].startswith('"'):
        end += 1
    lines[msgstr_index:end] = ['msgstr ""\n']
    return "".join(lines), msgid


def process(path: Path, spa_msgids: set[str], clear: bool) -> list[str]:
    content = path.read_text(encoding="utf-8")
    parts = re.split(r"(\n[ \t]*\n)", content)
    found: list[str] = []
    for index in range(0, len(parts), 2):
        updated, msgid = _clear_fuzzy_entry(parts[index], spa_msgids)
        if msgid is None:
            continue
        found.append(msgid)
        if clear:
            parts[index] = updated
    if clear and found:
        path.write_text("".join(parts), encoding="utf-8")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clear",
        action="store_true",
        help="clear legacy fuzzy SPA guesses instead of failing",
    )
    args = parser.parse_args()
    spa_msgids = _spa_msgids()
    violations: list[tuple[Path, list[str]]] = []
    for path in sorted(ROOT.glob(PO_GLOB)):
        found = process(path, spa_msgids, args.clear)
        if found:
            violations.append((path, found))
            action = "cleared" if args.clear else "fuzzy"
            print(f"[spa-fuzzy] {path.relative_to(ROOT)}: {action} {len(found)}")

    if args.clear:
        print(f"[spa-fuzzy] cleared {sum(len(v) for _, v in violations)} legacy guess(es)")
        return 0
    if violations:
        print(
            "[spa-fuzzy] ERROR: SPA msgids may not remain fuzzy; review the "
            "translation or run the explicit one-time --clear migration"
        )
        for path, msgids in violations:
            for msgid in msgids[:10]:
                print(f"    {path.relative_to(ROOT)}: {msgid!r}")
            if len(msgids) > 10:
                print(f"    ... and {len(msgids) - 10} more")
        return 1
    print("[spa-fuzzy] OK — no SPA-anchored msgid is fuzzy in any locale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
