"""SQLite connections must close without relying on garbage collection."""

import gc
import sqlite3
from pathlib import Path

import pytest

from app.proactive_intelligence import ProactiveEngine
from app.self_improvement import SelfImprovementEngine
from app.proactive_orchestrator import ProactiveOrchestrator
from app.house_awareness import HouseAwarenessEngine
from app.proactive_speech import ProactiveSpeechCoordinator
from app.dialogue_manager import DialogueManager
from app.conversation_engine import ConversationEngine
from app.integration_accounts import CredentialCipher, IntegrationAccountStore


def owner(kind, path):
    if kind == "dialogue":
        return DialogueManager(str(path))
    if kind == "conversation":
        return ConversationEngine(str(path))
    if kind == "proactive":
        return ProactiveEngine(str(path))
    if kind == "improvement":
        return SelfImprovementEngine(str(path))
    assert kind == "accounts"
    return IntegrationAccountStore(path, CredentialCipher(""))


@pytest.mark.parametrize(
    "kind", ["dialogue", "conversation", "accounts", "proactive", "improvement"]
)
def test_connection_closes_after_success(tmp_path, kind):
    service = owner(kind, tmp_path / f"{kind}.db")

    with service._connection() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


@pytest.mark.parametrize(
    "kind", ["dialogue", "conversation", "accounts", "proactive", "improvement"]
)
def test_transaction_rolls_back_and_closes(tmp_path, kind):
    service = owner(kind, tmp_path / f"{kind}.db")

    with service._connection() as connection:
        connection.execute("CREATE TABLE fd_probe (value INTEGER)")

    with pytest.raises(RuntimeError, match="abort"):
        with service._connection() as connection:
            connection.execute("INSERT INTO fd_probe VALUES (42)")
            raise RuntimeError("abort")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    with service._connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM fd_probe").fetchone()[0]

    assert count == 0


@pytest.mark.parametrize(
    "kind", ["dialogue", "conversation", "accounts", "proactive", "improvement"]
)
def test_repeated_connections_do_not_accumulate_fds(tmp_path, kind):
    fd_directory = Path("/proc/self/fd")

    if not fd_directory.is_dir():
        pytest.skip("Linux descriptor accounting required")

    service = owner(kind, tmp_path / f"{kind}.db")
    before = len(list(fd_directory.iterdir()))
    originally_enabled = gc.isenabled()

    gc.disable()
    try:
        for _ in range(150):
            with service._connection() as connection:
                connection.execute("SELECT 1").fetchone()

        after = len(list(fd_directory.iterdir()))
        assert after <= before + 5, f"{kind}: descriptors increased from {before} to {after}"
    finally:
        if originally_enabled:
            gc.enable()


@pytest.fixture(
    params=[
        ProactiveOrchestrator,
        HouseAwarenessEngine,
        ProactiveSpeechCoordinator,
    ]
)
def additional_owner(request, tmp_path):
    # Exercise the database helper without activating external services.
    service = object.__new__(request.param)
    service.database_path = tmp_path / "lifecycle.db"
    return service


def test_additional_owner_closes_connection(additional_owner):
    with additional_owner._connection() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_additional_owner_rolls_back_and_closes(additional_owner):
    with additional_owner._connection() as connection:
        connection.execute("CREATE TABLE lifecycle_probe (value INTEGER)")

    with pytest.raises(RuntimeError):
        with additional_owner._connection() as connection:
            connection.execute("INSERT INTO lifecycle_probe VALUES (42)")
            raise RuntimeError("Abort transaction")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    with additional_owner._connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM lifecycle_probe").fetchone()[0]

    assert count == 0


def test_additional_owner_does_not_accumulate_fds(additional_owner):
    fd_path = Path("/proc/self/fd")
    if not fd_path.is_dir():
        pytest.skip("Linux FD accounting required")

    before = len(list(fd_path.iterdir()))
    original_gc = gc.isenabled()

    gc.disable()
    try:
        for _ in range(150):
            with additional_owner._connection() as connection:
                connection.execute("SELECT 1").fetchone()

        after = len(list(fd_path.iterdir()))
        assert after <= before + 5
    finally:
        if original_gc:
            gc.enable()
