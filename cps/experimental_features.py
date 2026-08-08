# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry and persistence helpers for explicitly enabled experimental features."""

import sys
from dataclasses import asdict, dataclass

from . import constants

if constants.SCRIPTS_DIR not in sys.path:
    sys.path.insert(1, constants.SCRIPTS_DIR)
from cwa_db import CWA_DB  # noqa: E402


@dataclass(frozen=True)
class ExperimentalFeature:
    key: str
    setting: str
    name: str
    description: str
    default: bool = False
    dev_only: bool = True


STORE_DISCOVER = ExperimentalFeature(
    key="store_discover",
    setting="experimental_store_discover",
    name="Store / Discover",
    description="Search external acquisition sources through a configured Shelfmark service.",
)

FEATURES = {STORE_DISCOVER.key: STORE_DISCOVER}


def _close_owned_db(cwa_db, owned):
    if not owned:
        return
    try:
        cwa_db.cur.close()
    finally:
        cwa_db.con.close()


def feature_enabled(key, db=None):
    feature = FEATURES.get(key)
    if feature is None:
        return False
    owned = db is None
    cwa_db = db or CWA_DB()
    try:
        return bool(cwa_db.get_cwa_settings().get(feature.setting, feature.default))
    finally:
        _close_owned_db(cwa_db, owned)


def list_features(db=None):
    owned = db is None
    cwa_db = db or CWA_DB()
    try:
        settings = cwa_db.get_cwa_settings()
        return [dict(asdict(feature), enabled=bool(settings.get(feature.setting, feature.default)))
                for feature in FEATURES.values()]
    finally:
        _close_owned_db(cwa_db, owned)


def set_feature_enabled(key, enabled, db=None):
    feature = FEATURES.get(key)
    if feature is None:
        raise KeyError(key)
    owned = db is None
    cwa_db = db or CWA_DB()
    try:
        cwa_db.update_cwa_settings({feature.setting: int(bool(enabled))})
        return bool(cwa_db.get_cwa_settings().get(feature.setting, feature.default))
    finally:
        _close_owned_db(cwa_db, owned)
