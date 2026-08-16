# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the Basic Configuration form's AJAX submits.

These tests run in the unit lane. They prove the markup cannot natively submit
the backfill button, Enter is intercepted by the form submit handler, and the
AJAX payload includes the clicked button's name/value. They do not execute a
browser engine or jQuery itself.
"""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "cps" / "templates" / "config_edit.html"
MAIN_JS = REPO_ROOT / "cps" / "static" / "js" / "main.js"


class _ConfigForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = None
        self.controls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and self.form is None:
            self.form = attributes
        elif tag in {"button", "input"}:
            self.controls.append((tag, attributes))


def _callback_body(source, selector, event):
    pattern = re.compile(
        r"\$\(\"" + re.escape(selector) + r"\"\)\." + re.escape(event)
        + r"\(function\([^)]*\)\s*\{"
    )
    match = pattern.search(source)
    assert match, f"missing {event} handler for {selector}"

    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"unterminated {event} handler for {selector}")


@pytest.mark.unit
def test_backfill_control_has_no_native_json_or_405_submission_path():
    parser = _ConfigForm()
    parser.feed(TEMPLATE.read_text(encoding="utf-8"))

    assert parser.form is not None
    assert parser.form.get("id") == "config_form"
    assert "action" not in parser.form, (
        "the JavaScript-owned config form must not natively navigate to an "
        "HTML error or the AJAX endpoint's JSON response"
    )

    backfill = [
        attrs for _tag, attrs in parser.controls
        if attrs.get("name") == "kobo_kepub_backfill"
    ]
    assert backfill == [{
        "class": "btn btn-default",
        "type": "button",
        "id": "kobo_kepub_backfill",
        "name": "kobo_kepub_backfill",
        "value": "on",
    }]

    native_submits = [
        attrs for tag, attrs in parser.controls
        if attrs.get("type", "submit") == "submit"
        and not (tag == "input" and attrs.get("type") in {"hidden", "text"})
    ]
    assert native_submits == []


@pytest.mark.unit
def test_backfill_click_and_enter_use_ajax_with_the_required_payload():
    source = MAIN_JS.read_text(encoding="utf-8")

    form_submit = _callback_body(source, "#config_form", "submit")
    assert "preventDefault()" in form_submit
    assert "submitConfigForm($(this))" in form_submit

    backfill_click = _callback_body(source, "#kobo_kepub_backfill", "click")
    assert "submitConfigForm($(this).closest(\"form\"), this)" in backfill_click

    submit_function = re.search(
        r"function submitConfigForm\(\$form, submitter\)\s*\{(?P<body>.*?)\n    \}",
        source,
        re.DOTALL,
    )
    assert submit_function, "missing shared AJAX config submission function"
    body = submit_function.group("body")
    assert "$form.serialize()" in body
    assert "submitter.name" in body
    assert "submitter.value" in body
    assert "$.param" in body
    assert 'request_path = "/admin/ajaxconfig"' in body
    assert "$.post(getPath() + request_path" in body

    handler_source = (REPO_ROOT / "cps" / "admin.py").read_text(encoding="utf-8")
    assert 'elif "kobo_kepub_backfill" in to_save:' in handler_source
