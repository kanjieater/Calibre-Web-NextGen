# -*- coding: utf-8 -*-
"""Validated server-side ordering for configured scalar Calibre columns.

Only the admin-selected, direct-per-book numeric/date custom-column types are
supported here.  Other Calibre custom columns use link tables or multiple
values and need explicit ordering semantics before they can safely join this
feature.
"""
import re

from sqlalchemy import case

from . import db

SORTABLE_DATATYPES = frozenset(("int", "float", "datetime"))
_SORT_KEY = re.compile(r"^cc-(\d+)-(asc|desc)$")


def configured_column_ids(config):
    """Return configured IDs, tolerating malformed legacy config values."""
    return frozenset(
        int(value) for value in (getattr(config, "config_sortable_custom_columns", "") or "").split(",")
        if value.isdigit()
    )


def sortable_columns(columns, config):
    """Return configured live Calibre columns suitable for the sort menu."""
    allowed = configured_column_ids(config)
    return [column for column in columns
            if column.id in allowed and column.datatype in SORTABLE_DATATYPES
            and not column.is_multiple and not column.mark_for_delete]


def resolve(sort_param, config):
    """Resolve a validated key into ``(column model, deterministic order)``.

    ``None`` means the key was not an enabled custom-column sort.  No request
    data is ever used as a table or SQL identifier.
    """
    match = _SORT_KEY.fullmatch(sort_param or "")
    if not match:
        return None
    column_id, direction = int(match.group(1)), match.group(2)
    if column_id not in configured_column_ids(config):
        return None
    model = db.cc_classes.get(column_id)
    if model is None or not hasattr(model, "book"):
        return None
    value = model.value
    value_order = value.asc() if direction == "asc" else value.desc()
    id_order = db.Books.id.asc() if direction == "asc" else db.Books.id.desc()
    # SQLite sorts NULL first for ASC and last for DESC.  Make it last in both
    # directions without relying on a SQLite-version-specific NULLS LAST.
    return model, [case((value.is_(None), 1), else_=0), value_order, id_order]
