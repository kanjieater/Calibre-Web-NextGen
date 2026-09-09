# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for issue #553 — uploading a file whose name is long
enough to push the staging path component past the filesystem NAME_MAX
(255 bytes) raised ``OSError [Errno 36] File name too long`` and the upload
failed with the opaque "Failed to queue for processing" message.

The ingest service renames imported books from their metadata, so the staging
name is throwaway — the fix truncates it to fit instead of erroring. These
tests pin that the constructed ingest path always fits NAME_MAX (accounting
for the ".uploading"/".cwa.json" suffixes the pipeline appends downstream),
keeps the file extension, and leaves normal-length uploads untouched.
"""
import os
from types import SimpleNamespace

import pytest

# 9 repeats of the 26-letter alphabet + ".pdf" == 238 chars, the exact
# reproduction from the issue report.
LONG_BASENAME = "abcdefghijklmnopqrstuvwxyz" * 9 + ".pdf"


@pytest.mark.unit
def test_truncate_ingest_name_leaves_short_names_untouched():
    from cps.editbooks import _truncate_ingest_name
    name = "new_1_20260628_205351_821892_book.epub"
    assert _truncate_ingest_name(name) == name


@pytest.mark.unit
def test_truncate_ingest_name_fits_name_max_and_keeps_extension():
    from cps.editbooks import _truncate_ingest_name
    name = "new_1_20260628_205351_821892_" + LONG_BASENAME
    out = _truncate_ingest_name(name)
    # The pipeline appends ".uploading" (10 bytes) to the staging file, so the
    # truncated component plus that suffix must fit within NAME_MAX.
    assert len((out + ".uploading").encode("utf-8")) <= 255
    assert out.endswith(".pdf")
    # The unique prefix that drives sort order is preserved.
    assert out.startswith("new_1_20260628_205351_821892_")


@pytest.mark.unit
def test_truncate_ingest_name_is_byte_safe_for_multibyte_stems():
    from cps.editbooks import _truncate_ingest_name
    # A stem of multi-byte characters must never be cut mid-codepoint.
    name = ("é" * 400) + ".epub"  # é repeated; 2 bytes each
    out = _truncate_ingest_name(name)
    out.encode("utf-8")  # would raise if a surrogate/partial slipped through
    assert len((out + ".uploading").encode("utf-8")) <= 255
    assert out.endswith(".epub")


@pytest.mark.unit
def test_truncate_ingest_name_overlong_extension_still_fits():
    """A crafted name whose 'extension' alone exceeds the budget (os.path.splitext
    treats everything after the last dot as the extension) must still be trimmed
    to fit NAME_MAX — not returned full-length, which would re-trip ENAMETOOLONG."""
    from cps.editbooks import _truncate_ingest_name
    name = "x." + ("a" * 300)  # 302-char "extension", well past the budget
    out = _truncate_ingest_name(name)
    assert len((out + ".uploading").encode("utf-8")) <= 255


@pytest.mark.unit
def test_get_ingest_path_long_filename_is_writable(tmp_path, monkeypatch):
    """The real end-to-end check: the path _get_ingest_path returns for a
    238-char filename must be writable (i.e. not trip ENAMETOOLONG) once the
    ".uploading" staging suffix is appended."""
    from cps import editbooks
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))

    uploaded = SimpleNamespace(filename=LONG_BASENAME)
    final_path = editbooks._get_ingest_path(uploaded, prefix_parts=["new", 1])

    component = os.path.basename(final_path)
    assert len((component + ".uploading").encode("utf-8")) <= 255
    assert final_path.endswith(".pdf")

    # The actual filesystem write that raised [Errno 36] before the fix.
    staging = final_path + ".uploading"
    with open(staging, "w", encoding="utf-8") as handle:
        handle.write("x")
    assert os.path.exists(staging)


@pytest.mark.unit
def test_get_ingest_path_preserves_extension_for_non_ascii_filename(tmp_path, monkeypatch):
    """The ingest worker must receive an EPUB extension even without ASCII stem."""
    from cps import editbooks
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))

    final_path = editbooks._get_ingest_path(
        SimpleNamespace(filename="とある飛空士への追憶.epub"), prefix_parts=["format", 29968]
    )
    assert os.path.basename(final_path).endswith(".epub")


def test_get_ingest_path_normal_filename_unchanged(tmp_path, monkeypatch):
    """Normal-length uploads keep their full sanitized name — truncation only
    engages past NAME_MAX, so the common path is untouched."""
    from cps import editbooks
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))

    uploaded = SimpleNamespace(filename="A Tale of Two Cities.epub")
    final_path = editbooks._get_ingest_path(uploaded, prefix_parts=["new", 1])
    component = os.path.basename(final_path)
    # secure_filename collapses spaces to underscores but keeps the full title.
    assert "A_Tale_of_Two_Cities.epub" in component
    assert component.endswith(".epub")
