from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Float, Integer
from sqlalchemy.orm import declarative_base

from cps import custom_column_sort

pytestmark = pytest.mark.unit


class ColumnConfig:
    def __init__(self, column_id, datatype="float", multiple=False, deleted=False):
        self.id = column_id
        self.datatype = datatype
        self.is_multiple = multiple
        self.mark_for_delete = deleted


def test_sortable_columns_only_returns_enabled_scalar_columns():
    config = SimpleNamespace(config_sortable_custom_columns="2,3,garbage")
    columns = [
        ColumnConfig(2, "float"),
        ColumnConfig(3, "int"),
        ColumnConfig(4, "datetime"),
        ColumnConfig(5, "text"),
        ColumnConfig(6, "float", multiple=True),
    ]

    assert [column.id for column in custom_column_sort.sortable_columns(columns, config)] == [2, 3]


def test_resolve_rejects_unknown_or_not_configured_keys(monkeypatch):
    config = SimpleNamespace(config_sortable_custom_columns="2")
    monkeypatch.setattr(custom_column_sort.db, "cc_classes", {})

    assert custom_column_sort.resolve("cc-2-asc", config) is None
    assert custom_column_sort.resolve("cc-3-desc", config) is None
    assert custom_column_sort.resolve("cc-2-drop table", config) is None


def test_resolve_returns_direct_model_and_deterministic_order(monkeypatch):
    base = declarative_base()

    class Books(base):
        __tablename__ = "books"
        id = Column(Integer, primary_key=True)

    class Difficulty(base):
        __tablename__ = "custom_column_2"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Float)

    monkeypatch.setattr(custom_column_sort.db, "Books", Books)
    monkeypatch.setattr(custom_column_sort.db, "cc_classes", {2: Difficulty})
    config = SimpleNamespace(config_sortable_custom_columns="2")

    model, order = custom_column_sort.resolve("cc-2-desc", config)

    assert model is Difficulty
    assert len(order) == 3
    assert "custom_column_2.value DESC" in str(order[1])
    assert "books.id DESC" in str(order[2])
