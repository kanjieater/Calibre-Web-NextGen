import sqlite3

import pytest

from cps.unicode_collation import unicode_initial, unicode_sort_key


@pytest.mark.parametrize(
    ("value", "key", "initial"),
    [
        (None, None, None),
        ("", "", ""),
        (0, None, None),
        (b"E", None, None),
        ([], None, None),
        ({}, None, None),
        ("Èclair", "eclair", "E"),
        ("E\u0300clair", "eclair", "E"),
        ("Ñandú", "n\uffffandu", "Ñ"),
        ("Straße", "strasse", "S"),
    ],
)
def test_collation_contract(value, key, initial):
    assert unicode_sort_key(value) == key
    assert unicode_initial(value) == initial


def test_real_sqlite_udfs_sort_group_and_filter():
    conn = sqlite3.connect(":memory:")
    conn.create_function("ng_sort_key", 1, unicode_sort_key)
    conn.create_function("ng_initial", 1, unicode_initial)
    conn.execute("create table items (id integer, value text)")
    values = [None, "", "Zulu", "Ñandú", "Nube", "Èclair", "Eclair", "E\u0300cole"]
    conn.executemany("insert into items values (?, ?)", enumerate(values, 1))

    ordered = [r[0] for r in conn.execute(
        "select value from items order by ng_sort_key(value), value, id"
    )]
    assert ordered.index("Nube") < ordered.index("Ñandú") < ordered.index("Zulu")

    e_group = [r[0] for r in conn.execute(
        "select value from items where ng_initial(value) = 'E' "
        "order by ng_sort_key(value), value, id"
    )]
    assert set(e_group) == {"Èclair", "Eclair", "E\u0300cole"}

    buckets = [r[0] for r in conn.execute(
        "select ng_initial(value) from items "
        "where ng_initial(value) is not null and ng_initial(value) <> '' "
        "group by ng_initial(value)"
    )]
    assert buckets.count("E") == 1


# --- Regression: the fold must only touch Latin script -----------------------
#
# The #521 fix folded combining marks for every script, not just Latin. That
# silently merged letters that are distinct in their own alphabet: Russian
# Й/И, Ukrainian Ї/І, Greek ά/α, and Japanese voiced kana が/か (dakuten is a
# combining mark). Those cohorts had *correct* ordering before #521 shipped,
# so this is a regression, not a missing feature. Folding is only ever
# meaningful for Latin text; every other script keeps its own letters.


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Йогурт", "Иогурт"),      # ru: short I vs I
        ("Їжак", "Іжак"),          # uk: yi vs i
        ("άλφα", "αλφα"),          # el: tonos is not a fold
        ("がく", "かく"),           # ja: dakuten
        ("ぱん", "はん"),           # ja: handakuten
        ("ガク", "カク"),           # ja: katakana dakuten
    ],
)
def test_non_latin_letters_stay_distinct(left, right):
    assert unicode_sort_key(left) != unicode_sort_key(right)


@pytest.mark.parametrize(
    ("value", "expected_initial"),
    [
        ("Йогурт", "Й"),
        ("Їжак", "Ї"),
        ("Ярослав", "Я"),
        ("άλφα", "Ά"),
        ("がく", "が"),
    ],
)
def test_non_latin_initials_are_not_folded(value, expected_initial):
    assert unicode_initial(value) == expected_initial


def test_mixed_script_title_folds_only_the_latin_part():
    # A Latin accent still folds even when the string also carries Cyrillic.
    assert unicode_sort_key("Café Йогурт") == "cafe йогурт"


def test_latin_folding_still_works_after_the_guard():
    # The reporter's original #521 cases must keep passing.
    assert unicode_sort_key("Èclair") == "eclair"
    assert unicode_initial("Álbum") == "A"
    assert unicode_sort_key("Straße") == "strasse"
    assert unicode_initial("Ñandú") == "Ñ"
