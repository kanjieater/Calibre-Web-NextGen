# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavior pins for the new-UI hidden-books library controls."""

import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


pytestmark = pytest.mark.unit


def _book(book_id=7):
    return SimpleNamespace(
        id=book_id,
        title="Hidden in plain sight",
        series_index="1.0",
        has_cover=0,
        authors=[],
        series=[],
        data=[],
        tags=[],
    )


def test_hide_books_is_enabled_for_new_instances_by_default():
    """The existing admin switch remains a kill switch, but fresh installs opt in."""
    from cps.config_sql import _Settings

    default = _Settings.__table__.c.config_user_hide_enabled.default
    assert default is not None
    assert default.arg is True


def test_book_list_items_expose_hidden_state_for_a_visible_marker():
    from cps.api.serializers import serialize_book_list_item

    assert serialize_book_list_item(_book(), hidden=True)["hidden"] is True
    assert serialize_book_list_item(_book(), hidden=False)["hidden"] is False


def test_show_hidden_query_opts_only_the_spa_list_into_hidden_rows():
    """``show_hidden=1`` reaches common_filters through fill_indexpage."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    row = SimpleNamespace(Books=_book(), is_archived=False, read_status=None)
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?show_hidden=1"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([row], None, Pagination(1, 60, 1))) as fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(id=11, is_authenticated=True, is_anonymous=False)), \
             patch.object(books_mod, "_hidden_book_ids", return_value={7}):
            response = inspect.unwrap(books_mod.list_books)()

    assert fill.call_args.kwargs["allow_show_hidden"] is True
    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["hidden"] is True


def test_default_spa_list_keeps_hidden_exclusion_enabled():
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, Pagination(1, 60, 0))) as fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(id=11, is_authenticated=True, is_anonymous=False)), \
             patch.object(books_mod, "_hidden_book_ids", return_value={7}):
            inspect.unwrap(books_mod.list_books)()

    assert fill.call_args.kwargs.get("allow_show_hidden", False) is False


def test_show_hidden_applies_to_library_search_and_marks_results():
    from cps.api import books as books_mod

    row = SimpleNamespace(Books=_book(), is_archived=False, read_status=None)
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?search=plain&show_hidden=true"):
        with patch.object(books_mod.calibre_db, "get_search_results",
                          return_value=([row], 1, None)) as search, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(id=11, is_authenticated=True, is_anonymous=False)), \
             patch.object(books_mod, "_hidden_book_ids", return_value={7}):
            response = inspect.unwrap(books_mod.list_books)()

    assert search.call_args.kwargs["allow_show_hidden"] is True
    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["hidden"] is True


def test_anonymous_role_is_explicit_in_me_payload_for_action_gating():
    from cps.api.serializers import serialize_user
    from cps import constants, ub

    guest = ub.User()
    guest.id, guest.name, guest.locale, guest.theme = 1, "Guest", "en", 1
    guest.role = constants.ROLE_ANONYMOUS
    assert serialize_user(guest)["role"]["anonymous"] is True
