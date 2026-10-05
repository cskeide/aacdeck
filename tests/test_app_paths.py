"""Tests for the packaged app's data-folder migration in gui_common.py.

Importing gui_common needs PySide6's Qt libraries; skip where they can't load (the CI
test job installs no system Qt/XCB libs).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

import gui_common


def _legacy(tmp_path):
    legacy = tmp_path / "exe-dir"
    (legacy / "sessions" / "2026-03-familie").mkdir(parents=True)
    (legacy / "sessions" / "2026-03-familie" / "mamma.png").write_bytes(b"x")
    (legacy / "output").mkdir()
    (legacy / "output" / "2026-03-familie.pdf").write_bytes(b"%PDF")
    (legacy / "lotto-sessions").mkdir()  # empty: nothing to copy
    return legacy


def test_migration_copies_legacy_data(tmp_path, monkeypatch):
    legacy = _legacy(tmp_path)
    data = tmp_path / "Documents" / "AACdeck"
    monkeypatch.setattr(gui_common, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(gui_common, "BASE_DIR", data)

    gui_common.migrate_legacy_data()

    assert (data / "sessions" / "2026-03-familie" / "mamma.png").exists()
    assert (data / "output" / "2026-03-familie.pdf").exists()
    assert not (data / "lotto-sessions").exists()
    # Originals are copied, never moved.
    assert (legacy / "sessions" / "2026-03-familie" / "mamma.png").exists()


def test_migration_skipped_when_data_dir_exists(tmp_path, monkeypatch):
    legacy = _legacy(tmp_path)
    data = tmp_path / "Documents" / "AACdeck"
    data.mkdir(parents=True)
    monkeypatch.setattr(gui_common, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(gui_common, "BASE_DIR", data)

    gui_common.migrate_legacy_data()

    assert not (data / "sessions").exists()


def test_migration_noop_from_source(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(gui_common, "_LEGACY_DIR", None)
    monkeypatch.setattr(gui_common, "BASE_DIR", data)

    gui_common.migrate_legacy_data()

    assert not data.exists()
