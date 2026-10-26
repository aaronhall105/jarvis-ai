"""Tests must never attempt to own a real operator's Developer journal/key."""

import pytest


@pytest.fixture(autouse=True)
def isolated_developer_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DEVELOPER_STATE_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("JARVIS_DEVELOPER_API_KEY_FILE", str(tmp_path / "missing-key"))
