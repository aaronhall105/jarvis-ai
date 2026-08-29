import sqlite3

import pytest

from tools.verify_sqlite_integrity import verify_database


def test_quick_check_accepts_a_valid_database(tmp_path) -> None:
    database = tmp_path / "valid.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES ('preserved')")

    assert verify_database(database) == "ok"


def test_quick_check_rejects_a_non_database(tmp_path) -> None:
    database = tmp_path / "broken.db"
    database.write_bytes(b"not sqlite")

    with pytest.raises(sqlite3.DatabaseError):
        verify_database(database)
