"""Private, transactional task journal. No content from this module is logged."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.lock_fd)
            raise RuntimeError("Developer state is already owned by another process") from None
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS threads "
                "(id TEXT PRIMARY KEY, backend TEXT NOT NULL, state TEXT NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS calls "
                "(id INTEGER PRIMARY KEY, thread TEXT NOT NULL, turn TEXT NOT NULL, "
                "call_id TEXT NOT NULL, signature TEXT NOT NULL, state TEXT NOT NULL, "
                "result TEXT, approval TEXT, expires REAL, tool TEXT, arguments TEXT, "
                "UNIQUE(thread,turn,call_id))"
            )
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(calls)")}
            if "tool" not in columns:
                self.db.execute("ALTER TABLE calls ADD COLUMN tool TEXT")
            if "arguments" not in columns:
                self.db.execute("ALTER TABLE calls ADD COLUMN arguments TEXT")
            # Never replay an operation whose completion was not durably recorded.
            self.db.execute("UPDATE calls SET state='uncertain' WHERE state='executing'")
        for thread in self.threads():
            if thread.get("status") in {"running", "retrying", "waiting_approval"}:
                thread["status"] = "paused"
                self.save(thread)

    def close(self) -> None:
        self.db.close()
        os.close(self.lock_fd)

    def create(self, workspace: str) -> dict[str, Any]:
        thread = {
            "id": "api_" + uuid.uuid4().hex,
            "backend": "api",
            "cwd": workspace,
            "name": "API development",
            "createdAt": int(time.time()),
            "updatedAt": int(time.time()),
            "status": "idle",
            "turns": [],
            "response_id": None,
            "worktree": None,
            "branch": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        self.save(thread)
        return thread

    def save(self, thread: dict[str, Any]) -> None:
        thread["updatedAt"] = int(time.time())
        with self.db:
            self.db.execute(
                "INSERT INTO threads VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                (thread["id"], "api", json.dumps(thread)),
            )

    def get(self, thread_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT state FROM threads WHERE id=? AND backend='api'", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("Unknown API thread")
        return json.loads(row["state"])

    def threads(self) -> list[dict[str, Any]]:
        return [
            json.loads(row["state"])
            for row in self.db.execute("SELECT state FROM threads WHERE backend='api'")
        ]

    def delete(self, thread_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM calls WHERE thread=?", (thread_id,))
            self.db.execute("DELETE FROM threads WHERE id=?", (thread_id,))

    def claim(self, thread: str, turn: str, call_id: str, name: str, args: Any) -> dict[str, Any]:
        signature = hashlib.sha256(
            json.dumps([name, args], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO calls(thread,turn,call_id,signature,state,tool,arguments) "
                "VALUES(?,?,?,?,'pending',?,?)",
                (thread, turn, call_id, signature, name, json.dumps(args)),
            )
        row = self.db.execute(
            "SELECT * FROM calls WHERE thread=? AND turn=? AND call_id=?",
            (thread, turn, call_id),
        ).fetchone()
        assert row
        if row["signature"] != signature:
            raise ValueError("Tool call identity conflict")
        return dict(row)

    def call(self, identifier: int) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM calls WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("Unknown approval")
        return dict(row)

    def update_call(self, identifier: int, **fields: Any) -> None:
        if not fields or set(fields) - {"state", "result", "approval", "expires"}:
            raise ValueError("Invalid journal update")
        with self.db:
            self.db.execute(
                "UPDATE calls SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                (*fields.values(), identifier),
            )

    def pending(self, thread: str) -> list[dict[str, Any]]:
        with self.db:
            self.db.execute(
                "UPDATE calls SET approval='expired', state='completed', result=? "
                "WHERE thread=? AND approval='pending' AND expires<=? AND state='pending'",
                (json.dumps({"ok": False, "error": "Approval expired"}), thread, time.time()),
            )
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM calls WHERE thread=? AND approval='pending'", (thread,)
            )
        ]

    def cancel_pending(self, thread: str, turn: str | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE calls SET approval='cancelled', state='completed', result=? "
                "WHERE thread=? AND turn=? AND state='pending'",
                (json.dumps({"ok": False, "error": "Turn cancelled"}), thread, turn),
            )
