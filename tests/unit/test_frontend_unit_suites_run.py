# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the frontend's Node unit suites for real, as part of Fast Tests.

They existed and nothing executed them. `frontend/tests/unit/reportBuilder.test.ts`
is 49 careful assertions about the zero-egress report builder -- that no field is
derived from a full URL, that a hostile user-agent cannot smuggle text, that
markdown injection cannot break out of a code fence -- and **no CI job ran it**.
`grep -rn "node --test" .github/workflows/` returns nothing, and no Python test
shelled out to it either.

That is the same hole the plugin's Lua suites had, and its wrapper says why it
matters: *"a gate nobody runs is what let 19 SPA specs rot unnoticed (#1130)"*.
So this is the same fix for the same class, one layer over.

Deliberately does NOT introduce a test framework. The frontend ships none on
purpose -- adding vitest or jest is a new dependency and operator-gated under
hard rule 6 -- so these use Node's built-in runner and native TypeScript type
stripping, which is already on any machine that builds this project.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SUITE_DIR = REPO / "frontend" / "tests" / "unit"
# Explicit file paths, never the directory. `node --test <dir>` fails to apply
# type stripping to the .ts files it discovers and reports a bare "test failed"
# with no assertion behind it, while `node --test <file>` on the same suite
# passes 49/49. Measured 2026-08-10; a wrapper written the obvious way would
# have looked like a broken suite rather than a broken invocation.
SUITES = sorted(SUITE_DIR.glob("*.test.ts"))


def _node() -> str | None:
    return shutil.which("node")


def test_there_are_frontend_suites_to_run():
    """An empty glob must fail rather than pass vacuously -- that is exactly how
    a suite stops running without anyone noticing."""
    assert SUITES, f"no *.test.ts under {SUITE_DIR}; did they move?"


@pytest.mark.parametrize("suite", SUITES, ids=lambda p: p.name)
def test_frontend_unit_suite_passes(suite: Path):
    node = _node()
    if node is None:
        if os.environ.get("CI"):
            pytest.fail(
                "no node on a CI runner: the frontend's unit suites would "
                "silently stop running. The Frontend Build job already installs "
                "node; Fast Tests needs it too."
            )
        pytest.skip("no node available locally (CI has one)")

    result = subprocess.run(
        # --experimental-strip-types explicitly: type stripping is unflagged only
        # from Node 23.6, and CI pins 22. The flag is still accepted (as a no-op)
        # on newer versions -- verified on 26.5 -- so one invocation covers the
        # whole range rather than branching on a version we would have to guess.
        [node, "--experimental-strip-types", "--test", str(suite)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"{suite.name} failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    )
    # A runner that discovers nothing exits 0. Require evidence that assertions
    # actually ran, so "0 tests, all passing" cannot read as a green gate.
    assert "# pass " in result.stdout or "pass " in result.stdout, result.stdout[-2000:]
    passed = 0
    for line in result.stdout.splitlines():
        stripped = line.strip().lstrip("ℹ").strip()
        if stripped.startswith("pass "):
            passed = int(stripped.split()[1])
            break
    assert passed > 0, f"{suite.name} reported {passed} passing assertions:\n{result.stdout[-2000:]}"
