import sqlite3

import pytest

from tools.autonomy_broker import Denied
from tools.autonomy_broker_data import (
    compatible_code,
    compatible_state,
    persistence_surface,
    schema_state,
)


def test_asset_only_code_rollback_preserves_new_user_rows(tmp_path):
    before_tree = {
        "bridge/app/main.py": "a",
        "bridge/requirements.txt": "b",
        "bridge/app/static/chat.html": "c",
    }
    after_tree = {**before_tree, "bridge/app/static/chat.html": "d"}
    compatible_code(before_tree, after_tree, persistence_surface(before_tree))
    db = sqlite3.connect(tmp_path / "conversations.db")
    db.execute("CREATE TABLE messages (content TEXT)")
    db.execute("INSERT INTO messages VALUES ('before release')")
    db.commit()
    before = schema_state(tmp_path)
    db.execute("INSERT INTO messages VALUES ('during canary')")
    db.commit()
    after = schema_state(tmp_path)
    compatible_state(before, after)
    compatible_code(after_tree, before_tree, persistence_surface(before_tree))
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    assert "before release" not in str(before)
    db.close()


@pytest.mark.parametrize(
    "name",
    [
        "bridge/app/main.py",
        "bridge/app/new_migration.py",
        "bridge/requirements.txt",
        "bridge/Dockerfile",
    ],
)
def test_unknown_runtime_or_migration_change_denied(name):
    original = {"bridge/app/main.py": "a", "bridge/requirements.txt": "b"}
    with pytest.raises(Denied, match="unknown_persistence_compatibility"):
        compatible_code(original, {**original, name: "changed"}, persistence_surface(original))


def test_forward_migration_is_not_automatically_reversed(tmp_path):
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE state (id INTEGER)")
    db.commit()
    before = schema_state(tmp_path)
    db.execute("ALTER TABLE state ADD COLUMN new_field TEXT")
    db.execute("PRAGMA user_version=2")
    db.commit()
    with pytest.raises(Denied, match="schema_changed_reconciliation_required"):
        compatible_state(before, schema_state(tmp_path))
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()


def test_wal_is_included_without_rewinding_or_checkpointing_original(tmp_path):
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE state (id INTEGER)")
    db.execute("PRAGMA user_version=7")
    db.commit()
    before = (tmp_path / "state.db-wal").read_bytes()
    sampled = schema_state(tmp_path)
    assert sampled["state.db"]["user_version"] == 7
    assert (tmp_path / "state.db-wal").read_bytes() == before
    db.close()


def test_database_links_rejected(tmp_path):
    (tmp_path / "state.db").symlink_to("/etc/shadow")
    with pytest.raises(Denied, match="unsafe_database_path"):
        schema_state(tmp_path)
