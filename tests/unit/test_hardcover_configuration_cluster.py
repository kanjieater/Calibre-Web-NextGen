# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression contract for the Hardcover configuration cluster (#897–#900).

These tests deliberately exercise behavior where possible and use source pins
only for the DOM invariant that browsers enforce (unique IDs / form nesting).
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _bare_config():
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    cfg.config_hardcover_token = None
    cfg.config_hardcover_sync = False
    cfg.config_hardcover_sync_migrated = False
    return cfg


@pytest.fixture(autouse=True)
def _clean_hardcover_env(monkeypatch):
    for name in (
        "HARDCOVER_TOKEN",
        "HARDCOVER_TOKEN_FILE",
        "HARDCOVER_SYNC_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_token_source_distinguishes_database_environment_file_and_none(
    monkeypatch, tmp_path
):
    cfg = _bare_config()
    assert cfg.hardcover_token_source() is None

    secret = tmp_path / "hardcover-token"
    secret.write_text("file-value\n", encoding="utf-8")
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", str(secret))
    assert cfg.hardcover_token_source() == "HARDCOVER_TOKEN_FILE"

    monkeypatch.setenv("HARDCOVER_TOKEN", "env-value")
    assert cfg.hardcover_token_source() == "HARDCOVER_TOKEN"

    cfg.config_hardcover_token = "database-value"
    assert cfg.hardcover_token_source() == "database"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
    ],
)
def test_sync_environment_override_is_strict_and_case_insensitive(
    monkeypatch, raw, expected
):
    cfg = _bare_config()
    cfg.config_hardcover_sync = not expected
    monkeypatch.setenv("HARDCOVER_SYNC_ENABLED", raw.upper())

    assert cfg.hardcover_sync_enabled() is expected
    assert cfg.hardcover_sync_source() == "HARDCOVER_SYNC_ENABLED"


def test_invalid_sync_environment_override_falls_back_to_database(monkeypatch, caplog):
    cfg = _bare_config()
    cfg.config_hardcover_sync = True
    monkeypatch.setenv("HARDCOVER_SYNC_ENABLED", "sometimes")

    assert cfg.hardcover_sync_enabled() is True
    assert cfg.hardcover_sync_source() == "database"
    assert "HARDCOVER_SYNC_ENABLED" in caplog.text


def test_first_migration_preserves_either_preexisting_enable_flag(monkeypatch):
    cfg = _bare_config()
    saved = []
    monkeypatch.setattr(cfg, "save", lambda: saved.append(True))

    effective = cfg.reconcile_hardcover_sync(legacy_auto_fetch_enabled=True)

    assert effective is True
    assert cfg.config_hardcover_sync is True
    assert cfg.config_hardcover_sync_migrated is True
    assert saved == [True]


def test_completed_migration_never_reimports_stale_legacy_true(monkeypatch):
    cfg = _bare_config()
    cfg.config_hardcover_sync = False
    cfg.config_hardcover_sync_migrated = True
    monkeypatch.setattr(cfg, "save", lambda: pytest.fail("migration saved twice"))

    assert cfg.reconcile_hardcover_sync(legacy_auto_fetch_enabled=True) is False


def test_environment_override_is_effective_but_not_persisted_by_migration(monkeypatch):
    cfg = _bare_config()
    monkeypatch.setenv("HARDCOVER_SYNC_ENABLED", "true")
    monkeypatch.setattr(cfg, "save", lambda: None)

    assert cfg.reconcile_hardcover_sync(legacy_auto_fetch_enabled=False) is True
    assert cfg.config_hardcover_sync is False
    assert cfg.config_hardcover_sync_migrated is True


def test_admin_template_has_one_sync_control_and_ungated_token_status():
    template = (REPO_ROOT / "cps/templates/config_edit.html").read_text(
        encoding="utf-8"
    )

    assert template.count('id="config_hardcover_sync"') == 1
    assert template.count('name="config_hardcover_sync"') == 1
    assert 'data-related="hardcover-settings"' not in template

    token_pos = template.index('id="config_hardcover_token"')
    status_pos = template.index("hardcover_token_status")
    sync_pos = template.index('id="config_hardcover_sync"')
    assert token_pos > sync_pos
    assert status_pos > sync_pos


def test_admin_save_has_one_hardcover_sync_coercion_path():
    source = (REPO_ROOT / "cps/admin.py").read_text(encoding="utf-8")
    assert source.count('_config_checkbox(to_save, "config_hardcover_sync")') == 1
    assert '_config_checkbox_int(to_save, "config_hardcover_sync")' not in source


def test_scheduler_logs_disabled_and_missing_token_as_distinct_states(
    monkeypatch, caplog
):
    import sys
    from types import ModuleType, SimpleNamespace

    import cps.schedule as schedule

    class FakeDB:
        def get_cwa_settings(self):
            return {
                "hardcover_auto_fetch_enabled": False,
                "hardcover_auto_fetch_schedule": "weekly",
            }

        def execute_write(self, *_args, **_kwargs):
            return None

    fake_module = ModuleType("cwa_db")
    fake_module.CWA_DB = FakeDB
    monkeypatch.setitem(sys.modules, "cwa_db", fake_module)

    cfg = SimpleNamespace(
        reconcile_hardcover_sync=lambda legacy_auto_fetch_enabled: False,
        hardcover_sync_enabled=lambda: False,
        hardcover_sync_source=lambda: "database",
        resolved_hardcover_token=lambda: "",
        hardcover_token_source=lambda: None,
    )
    monkeypatch.setattr(schedule, "config", cfg)

    schedule._schedule_hardcover_auto_fetch(SimpleNamespace(), None)

    assert "Hardcover sync is disabled" in caplog.text
    assert "Hardcover token is not configured" in caplog.text


def test_scheduler_logs_presence_and_source_without_token_value(monkeypatch, caplog):
    import sys
    from types import ModuleType, SimpleNamespace

    import cps.schedule as schedule

    token = "must-never-appear-in-logs"

    class FakeDB:
        def get_cwa_settings(self):
            return {
                "hardcover_auto_fetch_enabled": True,
                "hardcover_auto_fetch_schedule": "weekly",
                "hardcover_auto_fetch_schedule_day": "sunday",
                "hardcover_auto_fetch_schedule_hour": 2,
                "hardcover_auto_fetch_min_confidence": 0.85,
                "hardcover_auto_fetch_batch_size": 50,
                "hardcover_auto_fetch_rate_limit": 5.0,
            }

        def execute_write(self, *_args, **_kwargs):
            return None

    fake_module = ModuleType("cwa_db")
    fake_module.CWA_DB = FakeDB
    monkeypatch.setitem(sys.modules, "cwa_db", fake_module)

    cfg = SimpleNamespace(
        reconcile_hardcover_sync=lambda legacy_auto_fetch_enabled: True,
        hardcover_sync_enabled=lambda: True,
        hardcover_sync_source=lambda: "HARDCOVER_SYNC_ENABLED",
        resolved_hardcover_token=lambda: token,
        hardcover_token_source=lambda: "HARDCOVER_TOKEN",
    )
    monkeypatch.setattr(schedule, "config", cfg)

    jobs = []
    scheduler = SimpleNamespace(
        schedule_task=lambda *args, **kwargs: jobs.append((args, kwargs))
    )
    schedule._schedule_hardcover_auto_fetch(scheduler, None)

    assert "Hardcover sync is enabled via HARDCOVER_SYNC_ENABLED" in caplog.text
    assert "Hardcover token is configured via HARDCOVER_TOKEN" in caplog.text
    assert token not in caplog.text
    assert len(jobs) == 1
