# SPDX-License-Identifier: GPL-3.0-or-later
"""List and detail custom dates share the existing edit-form calendar policy."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, TIMESTAMP, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("stored,expected", [
    (datetime(2026, 1, 10, tzinfo=timezone.utc), "2026-01-10"),
    (datetime(2026, 1, 9, 23, tzinfo=timezone.utc), "2026-01-09"),
    (datetime(101, 1, 1, tzinfo=timezone.utc), None),
    (None, None),
])
@pytest.mark.parametrize("storage", ["orm", "calibre_text"])
def test_real_sql_list_and_detail_emit_the_same_custom_calendar(stored, expected, storage, monkeypatch):
    from cps.api import books, serializers
    from cps import db
    base = declarative_base()

    class Deadline(base):
        __tablename__ = "custom_column_31"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(TIMESTAMP(timezone=True))

    engine = create_engine("sqlite://")
    base.metadata.create_all(engine)
    definition = SimpleNamespace(id=31, name="Deadline", label="deadline", datatype="datetime", is_multiple=False)
    with sessionmaker(bind=engine)() as session:
        if storage == "orm":
            session.add(Deadline(book=1, value=stored))
            session.commit()
        else:
            with engine.begin() as connection:
                connection.exec_driver_sql("INSERT INTO custom_column_31 (book,value) VALUES (?,?)", (1, stored.isoformat() if stored else None))
        monkeypatch.setitem(db.cc_classes, 31, Deadline)
        monkeypatch.setattr(books.calibre_db, "session", session)
        monkeypatch.setattr(books, "load_configured_columns", lambda _config: [definition])
        _definitions, values = books._list_custom_column_data([SimpleNamespace(id=1)])
        detail = serializers._serialize_custom_columns(SimpleNamespace(id=1, custom_column_31=session.query(Deadline).all()), [definition])
        assert values[1]["31"][0]["value"] == expected
        assert detail[0]["values"][0]["value"] == expected
    engine.dispose()
