#!/usr/bin/env python3
"""Read-only diagnostics for recent Developer task sessions."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SAFE_FAILURE_REASONS = {
    "provider unavailable": "Provider unavailable",
    "tool outcome uncertain; manual reconciliation required": "Tool outcome uncertain; manual reconciliation required",
    "read-only operation interrupted; retry available": "Read-only operation interrupted; retry available",
}

_SECRET_PATTERNS = (
    re.compile(
        r"\b(authorization|x-api-key|api[_-]?key|password|secret|token|cookie|set-cookie)\b", re.I
    ),
    re.compile(r"\bbearer\s+[a-z0-9._\-]+", re.I),
    re.compile(r"sk-[a-z0-9]+", re.I),
)

# Presentation vocabulary only, never execution authority. Keep this standalone
# stdlib utility usable without importing or opening a gateway backend/journal.
_TOOLS = frozenset(
    {
        "repository",
        "read_file",
        "search",
        "create_worktree",
        "patch_file",
        "verify",
        "git_commit",
        "git_push",
        "pr_status",
        "pr_create",
        "pr_update",
        "pr_merge",
    }
)
_READ_ONLY_TOOLS = frozenset({"repository", "read_file", "search", "pr_status"})
_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "paused",
        "running",
        "retrying",
        "waiting_approval",
        "idle",
        "cancelled",
    }
)


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    created_at: int | None
    updated_at: int | None
    status: str
    failure_reason: str | None
    completed_tools: int
    failed_tools: int
    uncertain_tools: int
    last_tool: str | None
    manual_reconciliation: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticsReport:
    database: Path
    sessions: tuple[SessionSummary, ...]
    warning: str | None = None


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.row_factory = sqlite3.Row
    return connection


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_text(value: Any, *, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit]


def _safe_failure_reason(value: Any) -> str | None:
    text = _safe_text(value, limit=240)
    if not text:
        return None
    normalized = text.lower()
    if normalized in _SAFE_FAILURE_REASONS:
        return _SAFE_FAILURE_REASONS[normalized]
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return "Saved failure details withheld for privacy"
    if any(character in text for character in "{}[]") or "://" in text:
        return "Saved failure details withheld for privacy"
    return "Saved failure details withheld for privacy"


def _tool_outcome(row: sqlite3.Row) -> tuple[str, str | None, bool]:
    state = str(row["state"] or "")
    tool = row["tool"] if row["tool"] in _TOOLS else None
    if state == "uncertain":
        return "uncertain", tool, True
    if state == "recoverable":
        return "uncertain", tool, tool not in _READ_ONLY_TOOLS
    if state == "pending":
        return "uncertain", tool, False
    if state != "completed":
        return "uncertain", tool, tool not in _READ_ONLY_TOOLS
    try:
        result = json.loads(row["result"])
    except (TypeError, ValueError, RecursionError):
        return "failed", tool, False
    if isinstance(result, dict):
        if result.get("ok") is False:
            return "failed", tool, False
        if result.get("ok") is True:
            return "completed", tool, False
        if (
            tool == "read_file"
            and isinstance(result.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", result["sha256"])
            and isinstance(result.get("text"), str)
        ):
            return "completed", tool, False
        return "failed", tool, False
    if (
        tool == "search"
        and isinstance(result, list)
        and all(
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and type(entry.get("line")) is int
            and isinstance(entry.get("text"), str)
            for entry in result
        )
    ):
        return "completed", tool, False
    return "failed", tool, False


def _session_from_row(row: sqlite3.Row, connection: sqlite3.Connection) -> SessionSummary:
    identifier = row["id"]
    session_id = (
        identifier
        if isinstance(identifier, str) and re.fullmatch(r"api_[0-9a-f]{32}", identifier)
        else "unknown-session"
    )
    warnings: list[str] = []
    raw_status = "malformed"
    created_at = updated_at = None
    failure_reason = None
    try:
        payload = json.loads(row["state"])
    except (TypeError, ValueError, RecursionError):
        payload = {}
        warnings.append("Malformed thread state JSON")
    if isinstance(payload, dict):
        raw_status = _safe_text(payload.get("status"), limit=40) or raw_status
        created_at = _safe_int(payload.get("createdAt"))
        updated_at = _safe_int(payload.get("updatedAt"))
        failure_reason = _safe_failure_reason(payload.get("error"))
        if created_at is None:
            warnings.append("Missing or invalid createdAt")
        if updated_at is None:
            warnings.append("Missing or invalid updatedAt")
    else:
        warnings.append("Malformed thread state record")
    completed_tools = failed_tools = uncertain_tools = 0
    last_tool = None
    manual_reconciliation = False
    try:
        calls = connection.execute(
            "SELECT tool,state,result FROM calls WHERE thread=? ORDER BY id", (row["id"],)
        ).fetchall()
    except sqlite3.Error:
        calls = []
        manual_reconciliation = True
        warnings.append("Could not read calls table")
    for call in calls:
        outcome, tool, reconcile = _tool_outcome(call)
        if outcome == "completed":
            completed_tools += 1
        elif outcome == "failed":
            failed_tools += 1
        else:
            uncertain_tools += 1
        if tool:
            last_tool = tool
        manual_reconciliation = manual_reconciliation or reconcile
    if raw_status == "failed" and not failure_reason:
        warnings.append("Failed session without recorded safe error")
    if raw_status not in _STATUSES:
        warnings.append("Unexpected session status withheld")
        raw_status = "unknown"
    return SessionSummary(
        session_id=session_id,
        created_at=created_at,
        updated_at=updated_at,
        status=raw_status,
        failure_reason=failure_reason,
        completed_tools=completed_tools,
        failed_tools=failed_tools,
        uncertain_tools=uncertain_tools,
        last_tool=last_tool,
        manual_reconciliation=manual_reconciliation,
        warnings=tuple(warnings),
    )


def load_recent_sessions(path: Path, *, limit: int = 5) -> DiagnosticsReport:
    if limit <= 0:
        raise ValueError("limit must be positive")
    try:
        if not path.exists():
            return DiagnosticsReport(path, (), warning="Database not found")
        connection = _open_read_only(path)
    except (sqlite3.Error, OSError, ValueError):
        return DiagnosticsReport(path, (), warning="Could not open database read-only")
    try:
        try:
            rows = connection.execute("SELECT id,state FROM threads WHERE backend='api'").fetchall()
        except sqlite3.Error:
            return DiagnosticsReport(path, (), warning="Could not read sessions")
        sessions = tuple(_session_from_row(row, connection) for row in rows)
        sessions = tuple(
            sorted(
                sessions,
                key=lambda item: (
                    item.updated_at is not None,
                    item.updated_at if item.updated_at is not None else item.created_at or -1,
                    item.session_id,
                ),
                reverse=True,
            )[:limit]
        )
        return DiagnosticsReport(path, sessions)
    finally:
        connection.close()


def render_report(report: DiagnosticsReport) -> str:
    # Even a caller-supplied filename can contain private text or credentials.
    lines = ["Developer session diagnostics"]
    if report.warning:
        lines.append(f"warning: {report.warning}")
        return "\n".join(lines)
    if not report.sessions:
        lines.append("No Developer sessions found.")
        return "\n".join(lines)
    for session in report.sessions:
        lines.append(f"- session: {session.session_id}")
        lines.append(f"  status: {session.status}")
        lines.append(f"  failure_reason: {session.failure_reason or 'none'}")
        lines.append(
            "  tools: "
            f"completed={session.completed_tools} "
            f"failed={session.failed_tools} "
            f"uncertain={session.uncertain_tools}"
        )
        lines.append(f"  last_tool: {session.last_tool or 'none'}")
        lines.append(
            "  manual_reconciliation: " + ("yes" if session.manual_reconciliation else "no")
        )
        if session.warnings:
            lines.append("  warnings: " + "; ".join(session.warnings))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=Path.home() / ".local/share/jarvis-developer/agent/tasks.sqlite3",
    )
    args = parser.parse_args()
    print(render_report(load_recent_sessions(args.database)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
