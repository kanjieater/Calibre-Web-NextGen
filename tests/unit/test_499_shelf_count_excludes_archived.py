# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #499 (@jasonxbergman): the sidebar shelf
badge counts books a user has archived, even though opening the shelf hides
them.

Root cause: the shelf *view* (``render_show_shelf`` -> ``fill_indexpage`` ->
``common_filters``) excludes the current user's archived books
(``Books.id.notin_(archived_book_ids)`` in cps/db.py). The sidebar badge
(layout.html) and the ``book_count`` sort modes (``_shelf_book_count`` in
cps/shelf.py) used a raw ``shelf.books.count()`` that ignored archive state, so
the badge stayed at N while the view dropped to N-archived. Archive a book and
the shelf count does not decrement — exactly the reported symptom.

Fix: make ``_shelf_book_count`` archive-aware when given a concrete user, count
only ``BookShelf`` rows whose ``book_id`` is not in that user's archived set.
Both the sort modes and the sidebar badge feed through it, so the badge now
matches the view.

These tests exercise the real query against an in-memory SQLAlchemy engine
(behaviour-pinning, not call-shape mocking), plus source pins that the sort
path and the template actually route through the archive-aware count.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELF_PY = REPO_ROOT / "cps" / "shelf.py"
RENDER_PY = REPO_ROOT / "cps" / "render_template.py"
LAYOUT_HTML = REPO_ROOT / "cps" / "templates" / "layout.html"


@pytest.fixture
def session(monkeypatch):
    """In-memory app.db with cps.ub.session pointed at it.

    cps.shelf reads the module-global ``ub.session`` for the archive query, so
    monkeypatching the attribute on the ub module is enough to redirect it.
    """
    import cps.ub as ub

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    monkeypatch.setattr(ub, "session", sess, raising=False)
    yield sess
    sess.close()


def _make_shelf(session, *, shelf_id=1, book_ids=(1, 2, 3), user_id=1):
    import cps.ub as ub

    shelf = ub.Shelf(id=shelf_id, name="My Shelf", user_id=user_id, is_public=0)
    session.add(shelf)
    session.flush()
    # Append through the relationship (as the real add_selected_to_shelf path
    # does) so the BookShelf.ub_shelf backref is populated for the before_flush
    # last_modified listener.
    for order, bid in enumerate(book_ids, start=1):
        shelf.books.append(ub.BookShelf(book_id=bid, order=order))
    session.commit()
    return shelf


def _archive(session, *, user_id, book_id, is_archived=True):
    import cps.ub as ub

    session.add(ub.ArchivedBook(user_id=user_id, book_id=book_id,
                                is_archived=is_archived))
    session.commit()


def _hide(session, *, user_id, book_id):
    import cps.ub as ub

    session.add(ub.UserHiddenBook(user_id=user_id, book_id=book_id))
    session.commit()


def _user(uid):
    return SimpleNamespace(id=uid, is_anonymous=False)


# ---------------------------------------------------------------------------
# Behaviour: archive-aware count
# ---------------------------------------------------------------------------

def test_raw_count_without_user_unchanged(session):
    """Back-compat: no user (or anonymous) -> raw relationship count."""
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    assert _shelf_book_count(shelf) == 3
    assert _shelf_book_count(shelf, SimpleNamespace(id=1, is_anonymous=True)) == 3


def test_user_count_excludes_archived_book(session):
    """The reported bug: archiving a book drops it from the user's count."""
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    _archive(session, user_id=1, book_id=2)
    assert _shelf_book_count(shelf, _user(1)) == 2  # book 2 hidden for user 1


def test_other_user_unaffected_by_someone_elses_archive(session):
    """Archive state is per-user: user 2 still sees all three books."""
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    _archive(session, user_id=1, book_id=2)
    assert _shelf_book_count(shelf, _user(2)) == 3


def test_user_count_excludes_personally_hidden_book(session):
    """Shelf badges must match the shelf grid, which uses common_filters()."""
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    _hide(session, user_id=1, book_id=2)
    assert _shelf_book_count(shelf, _user(1)) == 2


def test_other_user_shelf_count_ignores_someone_elses_hidden_state(session):
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    _hide(session, user_id=1, book_id=2)
    assert _shelf_book_count(shelf, _user(2)) == 3


def test_archive_and_hide_are_independent_but_each_excludes_from_shelf_count(session):
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    _archive(session, user_id=1, book_id=1)
    _hide(session, user_id=1, book_id=2)
    assert _shelf_book_count(shelf, _user(1)) == 1


def test_is_archived_false_row_does_not_reduce_count(session):
    """A tombstone row with is_archived=False must NOT shrink the count
    (ArchivedBook doubles as a Kobo deletion-track; only True means hidden)."""
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2, 3))
    _archive(session, user_id=1, book_id=2, is_archived=False)
    assert _shelf_book_count(shelf, _user(1)) == 3


def test_all_archived_counts_zero(session):
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=(1, 2))
    _archive(session, user_id=1, book_id=1)
    _archive(session, user_id=1, book_id=2)
    assert _shelf_book_count(shelf, _user(1)) == 0


def test_empty_shelf_counts_zero(session):
    from cps.shelf import _shelf_book_count
    shelf = _make_shelf(session, book_ids=())
    assert _shelf_book_count(shelf, _user(1)) == 0
    assert _shelf_book_count(shelf) == 0


# ---------------------------------------------------------------------------
# Source pins: the badge + sort path actually route through the user-aware count
# ---------------------------------------------------------------------------

def test_sort_passes_user_to_book_count(session=None):
    """book_count_* sort modes must pass the user so the order matches the
    badge the user actually sees."""
    src = SHELF_PY.read_text()
    match = re.search(
        r"(def sort_shelves_for_user\(.*?)(?=\n(?:def |@|class ))",
        src, re.DOTALL,
    )
    assert match, "sort_shelves_for_user not found"
    body = match.group(1)
    for call in re.findall(r"_shelf_book_count\(([^)]*)\)", body):
        assert "user" in call, (
            f"_shelf_book_count({call}) in sort_shelves_for_user must pass the "
            "user so the count is archive-aware"
        )


def test_render_template_attaches_archive_aware_count():
    """render_template.py must compute the archive-aware count for each
    sidebar shelf and attach it as book_count for the template."""
    src = RENDER_PY.read_text()
    assert "_shelf_book_count" in src, (
        "render_template.py must import _shelf_book_count to compute the badge"
    )
    assert re.search(r"\.book_count\s*=", src), (
        "render_template.py must attach .book_count to each g.shelves_access shelf"
    )


def test_layout_uses_attached_book_count_not_raw_relationship():
    """The regular-shelf sidebar badge must use the archive-aware book_count
    attribute, not a raw shelf.books.count()."""
    src = LAYOUT_HTML.read_text()
    # The regular-shelf loop is the one over g.shelves_access.
    block = re.search(
        r"g\.shelves_access\s*%}(.*?){%\s*endfor\s*%}",
        src, re.DOTALL,
    )
    assert block, "g.shelves_access loop not found in layout.html"
    loop = block.group(1)
    assert "shelf.book_count" in loop, (
        "regular-shelf badge must read shelf.book_count (archive-aware)"
    )
    # The raw shelf.books.count() may remain ONLY as a guarded fallback for the
    # (unexpected) case where book_count was not attached — never as the primary
    # value. Pin that the primary read is the archive-aware attribute.
    primary = loop.split("else")[0]
    assert "shelf.book_count" in primary and "shelf.books.count()" not in primary, (
        "regular-shelf badge must read shelf.book_count first; the raw "
        "archive-blind shelf.books.count() may only appear after an `else`"
    )
