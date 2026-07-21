# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency-free collation keys for Latin text.

Only Latin script is folded - see _fold_latin_marks.

This is intentionally a bounded improvement, not an ICU replacement. Accented
letters fold to their base for ordering and buckets. Spanish ``ñ`` remains a
distinct letter after the N block, while Unicode casefold supplies German
``ß`` -> ``ss`` primary equivalence.
"""

import unicodedata

_ENYE_MARKER = "\uf8ff"


def _is_latin(ch):
    return unicodedata.name(ch, "").startswith("LATIN")


def _fold_latin_marks(decomposed):
    """Drop combining marks, but only the ones sitting on a Latin base.

    Folding is only meaningful for Latin text. Applying it to every script
    merges letters that are distinct in their own alphabet: Russian J/I,
    Ukrainian YI/I, Greek alpha-with-tonos, and Japanese voiced kana (the
    dakuten is a combining mark). Those readers sorted correctly before the
    #521 fold landed, so a blanket drop is a regression for them.
    """
    out = []
    base_is_latin = False
    for ch in decomposed:
        if unicodedata.combining(ch):
            # A mark with no base yet is kept - there is nothing to fold onto.
            if base_is_latin:
                continue
            out.append(ch)
        else:
            base_is_latin = _is_latin(ch)
            out.append(ch)
    return "".join(out)


def unicode_sort_key(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    protected = value.replace("ñ", _ENYE_MARKER).replace("Ñ", _ENYE_MARKER)
    decomposed = unicodedata.normalize("NFKD", protected)
    # Recompose so a kept mark rejoins its base (ka + dakuten -> ga); otherwise
    # the key would expose decomposed forms to callers and to unicode_initial.
    folded = unicodedata.normalize("NFC", _fold_latin_marks(decomposed)).casefold()
    # Sort after the complete N block but before O in normal Unicode order.
    return folded.replace(_ENYE_MARKER.casefold(), "n\uffff")


def unicode_initial(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if not value:
        return ""
    if value[0] in ("ñ", "Ñ"):
        return "Ñ"
    key = unicode_sort_key(value[0])
    return key[0].upper() if key else ""
