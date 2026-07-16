# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the residual French i18n pushback in #615.

The first fixes anchored direct ``t('literal')`` calls and static ``label``
properties, but two gaps remained: fuzzy/empty French catalog entries are
absent from the SPA catalog, and default smart-shelf names are canonical
English database values rendered as if they were already display text.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_system_shelf_api_localizes_display_name_without_mutating_identity(monkeypatch):
    """System shelf identity stays canonical English in app.db, while the
    request-local API representation uses its lazy translated display name.
    User-created shelf names must remain literal user data.
    """
    from cps.api import magicshelves
    from cps import magic_shelf

    system = SimpleNamespace(
        id=7,
        name="Currently Reading",
        icon="📖",
        is_public=0,
        is_system=True,
        user_id=3,
    )
    custom = SimpleNamespace(
        id=8,
        name="Currently Reading",
        icon="🪄",
        is_public=0,
        is_system=False,
        user_id=3,
    )
    monkeypatch.setattr(
        magic_shelf,
        "system_magic_shelf_display_name",
        lambda shelf: "Lecture en cours" if shelf.is_system else shelf.name,
    )

    assert magicshelves._shelf_item(system, 3)["name"] == "Lecture en cours"
    assert magicshelves._shelf_item(system, 3)["is_system"] is True
    assert magicshelves._shelf_item(custom, 3)["name"] == "Currently Reading"
    assert magicshelves._shelf_item(custom, 3)["is_system"] is False
    assert system.name == "Currently Reading"


@pytest.mark.unit
def test_system_shelf_template_names_are_lazy_translatable_but_canonical_names_are_stable():
    """N_()/lazy_gettext marks display names for extraction without replacing
    the stable English names used for migration, deduplication, and matching.
    """
    from cps.magic_shelf import SYSTEM_SHELF_TEMPLATES

    expected = {
        "recently_added": "Recently Added",
        "highly_rated": "Highly Rated",
        "currently_reading": "Currently Reading",
        "yet_to_read": "Yet to Read",
        "recent_publications": "Recent Publications",
    }
    assert set(SYSTEM_SHELF_TEMPLATES) == set(expected)
    for key, canonical in expected.items():
        template = SYSTEM_SHELF_TEMPLATES[key]
        assert template["name"] == canonical
        assert not isinstance(template["display_name"], str)


@pytest.mark.unit
def test_translation_update_disables_msgmerge_fuzzy_guessing():
    """New SPA labels must enter catalogs as empty reviewable entries, not as
    semantically unrelated fuzzy guesses that look translated in status stats
    but disappear from the compiled/runtime catalog (#879).
    """
    script = (ROOT / "scripts" / "update_translations.sh").read_text(encoding="utf-8")
    assert script.count("msgmerge --no-fuzzy-matching --update") == 3
    assert "msgmerge --update" not in script
