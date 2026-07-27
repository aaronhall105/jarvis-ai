from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.task_engine import ActionPlan, TaskCommandResult

logger = logging.getLogger("jarvis-core.routines")


class ActorProtocol(Protocol):
    user_key: str
    display_name: str


class ActionEngineProtocol(Protocol):
    async def _resolve_action(
        self,
        text: str,
        actor_key: str | None = None,
    ) -> ActionPlan | str: ...

    async def _execute_action(
        self,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


_CREATE_PATTERNS = (
    re.compile(
        r"^\s*(?:create|make|save)\s+(?:a\s+)?(?:new\s+)?routine"
        r"(?:\s+(?:called|named))?\s+(?P<name>[^:]{1,80})\s*:\s*(?P<actions>.+?)\s*$",
        re.I,
    ),
    re.compile(
        r"^\s*save\s+(?P<name>[^:]{1,80})\s+(?:routine\s+)?as\s+"
        r"(?P<actions>.+?)\s*$",
        re.I,
    ),
)
_LIST_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me|what(?:'s| is| are))\s+(?:me\s+)?(?:my\s+)?"
    r"(?:saved\s+)?(?:routines?|scenes?)\s*[.!?]*\s*$",
    re.I,
)
_SHOW_PATTERN = re.compile(
    r"^\s*(?:show|describe|tell me about)\s+(?:my\s+)?(?:routine|scene)\s*#?"
    r"(?P<routine_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_HISTORY_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me)\s+(?:the\s+)?(?:run\s+)?history\s+(?:for|of)\s+"
    r"(?:routine|scene)\s*#?(?P<routine_id>\d+)\s*[.!?]*\s*$|"
    r"^\s*(?:show|list)\s+(?:routine|scene)\s*#?(?P<routine_id_alt>\d+)\s+"
    r"(?:run\s+)?history\s*[.!?]*\s*$",
    re.I,
)
_RUN_ID_PATTERN = re.compile(
    r"^\s*(?:run|start|activate|execute)\s+(?:my\s+)?(?:routine|scene)\s*#?"
    r"(?P<routine_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_DELETE_PATTERN = re.compile(
    r"^\s*(?:delete|remove|cancel)\s+(?:my\s+)?(?:routine|scene)\s*#?"
    r"(?P<routine_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_DISABLE_PATTERN = re.compile(
    r"^\s*(?:disable|pause)\s+(?:my\s+)?(?:routine|scene)\s*#?"
    r"(?P<routine_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_ENABLE_PATTERN = re.compile(
    r"^\s*(?:enable|resume)\s+(?:my\s+)?(?:routine|scene)\s*#?"
    r"(?P<routine_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_RENAME_PATTERN = re.compile(
    r"^\s*rename\s+(?:my\s+)?(?:routine|scene)\s*#?(?P<routine_id>\d+)\s+"
    r"to\s+(?P<name>.+?)\s*[.!?]*\s*$",
    re.I,
)
_RUN_NAME_PATTERN = re.compile(
    r"^\s*(?:run|start|activate|execute)\s+(?:my\s+|the\s+)?(?P<name>.+?)"
    r"(?:\s+(?:routine|scene))?\s*[.!?]*\s*$",
    re.I,
)


class RoutineEngine:
    """Persistent, owner-scoped multi-step routines and immediate scenes."""

    ACTIVE_STATUSES = {"active", "disabled"}

    def __init__(
        self,
        *,
        action_engine: ActionEngineProtocol,
        database_path: str,
        enabled: bool = True,
        max_steps: int = 8,
    ) -> None:
        self.action_engine = action_engine
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(enabled)
        self.max_steps = max(2, min(int(max_steps), 12))
        self._run_lock = asyncio.Lock()
        self._running_ids: set[int] = set()
        self._last_error: str | None = None
        self._initialise_database()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _normalise(value: str) -> str:
        value = str(value or "").casefold().replace("_", " ")
        value = re.sub(r"\b(?:routine|scene)\b", " ", value)
        value = re.sub(r"[^a-z0-9\s'-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_name(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;!?\"'")
        cleaned = re.sub(r"\s+(?:routine|scene)$", "", cleaned, flags=re.I).strip()
        return cleaned

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialise_database(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS routines (
                    routine_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    normalised_name TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL,
                    action_summary TEXT NOT NULL,
                    step_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    run_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_routines_owner_name
                ON routines(owner_key, normalised_name, status);

                CREATE INDEX IF NOT EXISTS idx_routines_owner_status
                ON routines(owner_key, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS routine_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    routine_id INTEGER,
                    owner_key TEXT NOT NULL,
                    routine_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    FOREIGN KEY(routine_id) REFERENCES routines(routine_id)
                );

                CREATE INDEX IF NOT EXISTS idx_routine_runs_routine
                ON routine_runs(routine_id, started_at DESC, run_id DESC);

                CREATE INDEX IF NOT EXISTS idx_routine_runs_owner
                ON routine_runs(owner_key, started_at DESC, run_id DESC);
                """
            )

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        if "action_payload_json" in item:
            try:
                item["action_payload"] = json.loads(item.pop("action_payload_json"))
            except (TypeError, ValueError):
                item["action_payload"] = {}
                item.pop("action_payload_json", None)
        if "result_json" in item:
            try:
                item["result"] = json.loads(item.pop("result_json"))
            except (TypeError, ValueError):
                item["result"] = {}
                item.pop("result_json", None)
        return item

    @staticmethod
    def _step_count(plan: ActionPlan) -> int:
        if plan.action_type != "sequence":
            return 1
        steps = plan.payload.get("steps")
        return len(steps) if isinstance(steps, list) else 0

    @staticmethod
    def _step_summaries(routine: dict[str, Any]) -> list[str]:
        payload = routine.get("action_payload") or {}
        if routine.get("action_type") != "sequence":
            return [str(routine.get("action_summary") or "action")]
        steps = payload.get("steps")
        if not isinstance(steps, list):
            return []
        return [
            str(step.get("summary") or f"step {index}")
            for index, step in enumerate(steps, start=1)
            if isinstance(step, dict)
        ]

    async def status(self) -> dict[str, Any]:
        with self._connection() as connection:
            routine_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM routines GROUP BY status"
            ).fetchall()
            run_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM routine_runs GROUP BY status"
            ).fetchall()
        return {
            "version": "16.3.0",
            "enabled": self.enabled,
            "database": str(self.database_path),
            "max_steps": self.max_steps,
            "running_count": len(self._running_ids),
            "last_error": self._last_error,
            "routine_counts": {
                str(row["status"]): int(row["count"]) for row in routine_rows
            },
            "run_counts": {
                str(row["status"]): int(row["count"]) for row in run_rows
            },
        }

    async def list_routines(
        self,
        *,
        owner_key: str | None = None,
        statuses: set[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["status != 'deleted'"]
        values: list[Any] = []
        if owner_key:
            clauses.append("owner_key = ?")
            values.append(owner_key)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(sorted(statuses))
        values.append(max(1, min(int(limit), 200)))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM routines
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, routine_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    async def get_routine(self, routine_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM routines WHERE routine_id = ? AND status != 'deleted'",
                (int(routine_id),),
            ).fetchone()
        return self._decode_row(row) if row else None

    async def get_owned_routine(
        self,
        routine_id: int,
        *,
        owner_key: str,
    ) -> dict[str, Any] | None:
        item = await self.get_routine(routine_id)
        if item is None or str(item.get("owner_key")) != owner_key:
            return None
        return item

    async def find_owned_routine(
        self,
        name: str,
        *,
        owner_key: str,
    ) -> dict[str, Any] | str | None:
        key = self._normalise(name)
        if not key:
            return None
        routines = await self.list_routines(owner_key=owner_key, limit=200)
        exact = [item for item in routines if item.get("normalised_name") == key]
        if len(exact) == 1:
            return exact[0]
        partial = [
            item for item in routines
            if key in str(item.get("normalised_name") or "")
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(str(item["name"]) for item in partial[:4])
            return f"I found more than one matching routine — {names}."
        return None

    async def list_runs(
        self,
        routine_id: int,
        *,
        owner_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["routine_id = ?"]
        values: list[Any] = [int(routine_id)]
        if owner_key:
            clauses.append("owner_key = ?")
            values.append(owner_key)
        values.append(max(1, min(int(limit), 200)))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM routine_runs
                WHERE {' AND '.join(clauses)}
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    async def create_routine(
        self,
        *,
        actor: ActorProtocol,
        name: str,
        source_text: str,
        plan: ActionPlan,
    ) -> tuple[dict[str, Any], bool]:
        clean_name = self._clean_name(name)
        if not clean_name:
            raise ValueError("Please give the routine a name.")
        if len(clean_name) > 80:
            raise ValueError("Routine names can contain no more than 80 characters.")
        step_count = self._step_count(plan)
        if step_count < 1:
            raise ValueError("The routine contains no actions.")
        if step_count > self.max_steps:
            raise ValueError(f"A routine can contain no more than {self.max_steps} steps.")
        key = self._normalise(clean_name)
        now = self._iso(self._utc_now())
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM routines
                WHERE owner_key = ? AND normalised_name = ? AND status != 'deleted'
                ORDER BY routine_id DESC LIMIT 1
                """,
                (actor.user_key, key),
            ).fetchone()
            if existing:
                return self._decode_row(existing), True
            cursor = connection.execute(
                """
                INSERT INTO routines(
                    owner_key, owner_name, name, normalised_name, source_text,
                    action_type, action_payload_json, action_summary, step_count,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    actor.user_key,
                    actor.display_name,
                    clean_name,
                    key,
                    source_text,
                    plan.action_type,
                    json.dumps(plan.payload, ensure_ascii=False, default=str),
                    plan.summary,
                    step_count,
                    now,
                    now,
                ),
            )
            routine_id = int(cursor.lastrowid)
        item = await self.get_routine(routine_id)
        assert item is not None
        return item, False

    async def _set_status(
        self,
        routine_id: int,
        *,
        owner_key: str,
        from_statuses: set[str],
        to_status: str,
    ) -> bool:
        placeholders = ",".join("?" for _ in from_statuses)
        values: list[Any] = [to_status, self._iso(self._utc_now()), int(routine_id), owner_key]
        values.extend(sorted(from_statuses))
        with self._connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE routines SET status = ?, updated_at = ?
                WHERE routine_id = ? AND owner_key = ?
                  AND status IN ({placeholders})
                """,
                values,
            )
        return cursor.rowcount > 0

    async def disable_routine(self, routine_id: int, *, owner_key: str) -> bool:
        return await self._set_status(
            routine_id,
            owner_key=owner_key,
            from_statuses={"active"},
            to_status="disabled",
        )

    async def enable_routine(self, routine_id: int, *, owner_key: str) -> bool:
        return await self._set_status(
            routine_id,
            owner_key=owner_key,
            from_statuses={"disabled"},
            to_status="active",
        )

    async def delete_routine(self, routine_id: int, *, owner_key: str) -> bool:
        return await self._set_status(
            routine_id,
            owner_key=owner_key,
            from_statuses={"active", "disabled"},
            to_status="deleted",
        )

    async def rename_routine(
        self,
        routine_id: int,
        *,
        owner_key: str,
        new_name: str,
    ) -> tuple[bool, str | None]:
        clean_name = self._clean_name(new_name)
        if not clean_name:
            return False, "Please give the routine a name."
        key = self._normalise(clean_name)
        with self._connection() as connection:
            duplicate = connection.execute(
                """
                SELECT routine_id FROM routines
                WHERE owner_key = ? AND normalised_name = ?
                  AND status != 'deleted' AND routine_id != ?
                LIMIT 1
                """,
                (owner_key, key, int(routine_id)),
            ).fetchone()
            if duplicate:
                return False, "You already have a routine with that name."
            cursor = connection.execute(
                """
                UPDATE routines
                SET name = ?, normalised_name = ?, updated_at = ?
                WHERE routine_id = ? AND owner_key = ? AND status != 'deleted'
                """,
                (
                    clean_name,
                    key,
                    self._iso(self._utc_now()),
                    int(routine_id),
                    owner_key,
                ),
            )
        if cursor.rowcount == 0:
            return False, None
        return True, clean_name

    async def _record_run(
        self,
        *,
        routine_id: int | None,
        owner_key: str,
        routine_name: str,
        source: str,
        started_at: datetime,
        status: str,
        result: dict[str, Any],
        error: str | None,
    ) -> dict[str, Any]:
        finished = self._utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO routine_runs(
                    routine_id, owner_key, routine_name, source, started_at,
                    finished_at, status, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    routine_id,
                    owner_key,
                    routine_name,
                    source,
                    self._iso(started_at),
                    self._iso(finished),
                    status,
                    json.dumps(result, ensure_ascii=False, default=str),
                    error,
                ),
            )
            run_id = int(cursor.lastrowid)
            if routine_id is not None:
                connection.execute(
                    """
                    UPDATE routines
                    SET last_run_at = ?, run_count = run_count + 1, updated_at = ?
                    WHERE routine_id = ?
                    """,
                    (self._iso(finished), self._iso(finished), routine_id),
                )
            row = connection.execute(
                "SELECT * FROM routine_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        assert row is not None
        return self._decode_row(row)

    async def execute_plan(
        self,
        *,
        plan: ActionPlan,
        owner_key: str,
        routine_name: str,
        source: str,
        routine_id: int | None = None,
    ) -> dict[str, Any]:
        started = self._utc_now()
        try:
            result = await self.action_engine._execute_action(
                plan.action_type,
                dict(plan.payload),
            )
            success = result.get("success") is True
            if result.get("verified") is False and result.get("command_accepted") is not True:
                success = False
            status = "completed" if success else "failed"
            error = None if success else str(
                result.get("response_message")
                or result.get("error")
                or "The routine did not complete."
            )
        except Exception as exc:
            logger.exception("Routine execution failed")
            self._last_error = str(exc)
            result = {"success": False, "verified": False, "error": str(exc)}
            status = "failed"
            error = str(exc)
        run = await self._record_run(
            routine_id=routine_id,
            owner_key=owner_key,
            routine_name=routine_name,
            source=source,
            started_at=started,
            status=status,
            result=result,
            error=error,
        )
        return {"success": status == "completed", "result": result, "run": run}

    async def run_routine(
        self,
        routine_id: int,
        *,
        owner_key: str,
        source: str = "voice",
    ) -> dict[str, Any] | None:
        routine = await self.get_owned_routine(routine_id, owner_key=owner_key)
        if routine is None:
            return None
        if routine.get("status") != "active":
            return {"success": False, "routine": routine, "disabled": True}
        async with self._run_lock:
            if routine_id in self._running_ids:
                return {"success": False, "routine": routine, "already_running": True}
            self._running_ids.add(routine_id)
        try:
            plan = ActionPlan(
                action_type=str(routine["action_type"]),
                payload=dict(routine.get("action_payload") or {}),
                summary=str(routine["action_summary"]),
            )
            outcome = await self.execute_plan(
                plan=plan,
                owner_key=owner_key,
                routine_name=str(routine["name"]),
                source=source,
                routine_id=routine_id,
            )
            refreshed = await self.get_routine(routine_id)
            return {**outcome, "routine": refreshed or routine}
        finally:
            async with self._run_lock:
                self._running_ids.discard(routine_id)

    def _describe(self, routine: dict[str, Any]) -> str:
        routine_id = int(routine["routine_id"])
        name = str(routine["name"])
        status = str(routine.get("status") or "unknown")
        count = int(routine.get("step_count") or 0)
        return (
            f"Routine {routine_id}, {name}, is {status} and has {count} "
            f"step{'s' if count != 1 else ''}."
        )

    @staticmethod
    def _outcome_response(name: str, outcome: dict[str, Any]) -> str:
        result = outcome.get("result") or {}
        if outcome.get("success"):
            completed = result.get("completed_steps")
            total = result.get("total_steps")
            if isinstance(completed, int) and isinstance(total, int):
                return f"{name} completed all {completed} steps."
            return f"{name} completed."
        if outcome.get("already_running"):
            return f"{name} is already running."
        if outcome.get("disabled"):
            return f"{name} is disabled."
        message = str(
            result.get("response_message")
            or result.get("error")
            or "The routine failed."
        )
        return f"{name} did not complete. {message}"

    async def _run_by_name(
        self,
        name: str,
        actor: ActorProtocol,
    ) -> TaskCommandResult:
        found = await self.find_owned_routine(name, owner_key=actor.user_key)
        if isinstance(found, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=found,
                intent="routine_run",
            )
        if isinstance(found, dict):
            outcome = await self.run_routine(
                int(found["routine_id"]),
                owner_key=actor.user_key,
            )
            assert outcome is not None
            return TaskCommandResult(
                handled=True,
                success=bool(outcome.get("success")),
                response=self._outcome_response(str(found["name"]), outcome),
                intent="routine_run",
                details=outcome,
            )

        # Fall back to an exact Home Assistant script or automation. This lets
        # commands such as “run bedtime routine” use existing HA routines without
        # copying their logic into Jarvis.
        plan = await self.action_engine._resolve_action(
            f"run {name}",
            actor_key=actor.user_key,
        )
        if isinstance(plan, ActionPlan) and plan.action_type == "home_routine":
            outcome = await self.execute_plan(
                plan=plan,
                owner_key=actor.user_key,
                routine_name=plan.summary.removeprefix("run "),
                source="home_assistant_routine",
            )
            return TaskCommandResult(
                handled=True,
                success=bool(outcome.get("success")),
                response=self._outcome_response(
                    plan.summary.removeprefix("run "),
                    outcome,
                ),
                intent="routine_external_run",
                details={"plan": asdict(plan), **outcome},
            )
        return TaskCommandResult(handled=False)

    async def handle_command(
        self,
        text: str,
        actor: ActorProtocol,
    ) -> TaskCommandResult:
        value = re.sub(r"\s+", " ", str(text or "")).strip()

        for pattern in _CREATE_PATTERNS:
            match = pattern.match(value)
            if not match:
                continue
            if not self.enabled:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response="Routines are currently disabled.",
                    intent="routine_create",
                )
            name = self._clean_name(match.group("name"))
            actions = match.group("actions").strip()
            plan = await self.action_engine._resolve_action(
                actions,
                actor_key=actor.user_key,
            )
            if isinstance(plan, str):
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=plan,
                    intent="routine_create",
                )
            try:
                routine, duplicate = await self.create_routine(
                    actor=actor,
                    name=name,
                    source_text=text,
                    plan=plan,
                )
            except ValueError as exc:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=str(exc),
                    intent="routine_create",
                )
            if duplicate:
                response = (
                    f"You already have routine {routine['routine_id']} called "
                    f"{routine['name']}."
                )
            else:
                response = (
                    f"Saved routine {routine['routine_id']} as {routine['name']} with "
                    f"{routine['step_count']} step"
                    f"{'s' if routine['step_count'] != 1 else ''}."
                )
            return TaskCommandResult(
                handled=True,
                response=response,
                intent="routine_create",
                details={"routine": routine, "duplicate": duplicate},
            )

        if _LIST_PATTERN.match(value):
            routines = await self.list_routines(owner_key=actor.user_key, limit=20)
            if not routines:
                return TaskCommandResult(
                    handled=True,
                    response="You have no saved routines.",
                    intent="routine_list",
                    details={"routines": []},
                )
            descriptions = [
                f"Routine {item['routine_id']}: {item['name']}, "
                f"{item['step_count']} step{'s' if item['step_count'] != 1 else ''}, "
                f"{item['status']}"
                for item in routines[:8]
            ]
            return TaskCommandResult(
                handled=True,
                response=(
                    f"You have {len(routines)} saved routine"
                    f"{'s' if len(routines) != 1 else ''}. "
                    + "; ".join(descriptions)
                    + "."
                ),
                intent="routine_list",
                details={"routines": routines},
            )

        match = _SHOW_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id"))
            routine = await self.get_owned_routine(routine_id, owner_key=actor.user_key)
            if routine is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find routine {routine_id}.",
                    intent="routine_show",
                )
            steps = self._step_summaries(routine)
            response = self._describe(routine)
            if steps:
                response += " Steps: " + "; ".join(
                    f"{index}, {summary}" for index, summary in enumerate(steps, start=1)
                ) + "."
            return TaskCommandResult(
                handled=True,
                response=response,
                intent="routine_show",
                details={"routine": routine, "steps": steps},
            )

        match = _HISTORY_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id") or match.group("routine_id_alt"))
            routine = await self.get_owned_routine(routine_id, owner_key=actor.user_key)
            if routine is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find routine {routine_id}.",
                    intent="routine_history",
                )
            runs = await self.list_runs(
                routine_id,
                owner_key=actor.user_key,
                limit=10,
            )
            if not runs:
                response = f"{routine['name']} has not run yet."
            else:
                latest = runs[0]
                response = (
                    f"{routine['name']} has run {len(runs)} time"
                    f"{'s' if len(runs) != 1 else ''} in the available history. "
                    f"The latest run {latest['status']}."
                )
            return TaskCommandResult(
                handled=True,
                response=response,
                intent="routine_history",
                details={"routine": routine, "runs": runs},
            )

        match = _RUN_ID_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id"))
            outcome = await self.run_routine(routine_id, owner_key=actor.user_key)
            if outcome is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find routine {routine_id}.",
                    intent="routine_run",
                )
            routine = outcome["routine"]
            return TaskCommandResult(
                handled=True,
                success=bool(outcome.get("success")),
                response=self._outcome_response(str(routine["name"]), outcome),
                intent="routine_run",
                details=outcome,
            )

        match = _DELETE_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id"))
            routine = await self.get_owned_routine(routine_id, owner_key=actor.user_key)
            updated = await self.delete_routine(routine_id, owner_key=actor.user_key)
            if not updated or routine is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t delete routine {routine_id}.",
                    intent="routine_delete",
                )
            return TaskCommandResult(
                handled=True,
                response=f"Deleted routine {routine_id}: {routine['name']}.",
                intent="routine_delete",
                details={"routine": routine},
            )

        match = _DISABLE_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id"))
            updated = await self.disable_routine(routine_id, owner_key=actor.user_key)
            return TaskCommandResult(
                handled=True,
                success=updated,
                response=(
                    f"Disabled routine {routine_id}."
                    if updated else f"I couldn’t disable routine {routine_id}."
                ),
                intent="routine_disable",
            )

        match = _ENABLE_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id"))
            updated = await self.enable_routine(routine_id, owner_key=actor.user_key)
            return TaskCommandResult(
                handled=True,
                success=updated,
                response=(
                    f"Enabled routine {routine_id}."
                    if updated else f"I couldn’t enable routine {routine_id}."
                ),
                intent="routine_enable",
            )

        match = _RENAME_PATTERN.match(value)
        if match:
            routine_id = int(match.group("routine_id"))
            updated, detail = await self.rename_routine(
                routine_id,
                owner_key=actor.user_key,
                new_name=match.group("name"),
            )
            if not updated:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=detail or f"I couldn’t rename routine {routine_id}.",
                    intent="routine_rename",
                )
            return TaskCommandResult(
                handled=True,
                response=f"Renamed routine {routine_id} to {detail}.",
                intent="routine_rename",
            )

        match = _RUN_NAME_PATTERN.match(value)
        if match:
            result = await self._run_by_name(match.group("name"), actor)
            if result.handled:
                return result

        # Recurring, timed and conditional commands must continue to their own
        # deterministic engines. Only plain immediate compound actions become
        # ad-hoc scenes here.
        if re.match(r"^(?:when|whenever|if|every|each|at|tomorrow|in\s+\d|after\s+\d)\b", value, re.I):
            return TaskCommandResult(handled=False)

        plan = await self.action_engine._resolve_action(
            value,
            actor_key=actor.user_key,
        )
        if isinstance(plan, ActionPlan) and plan.action_type == "sequence":
            if not self.enabled:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response="Routines are currently disabled.",
                    intent="scene_run",
                )
            outcome = await self.execute_plan(
                plan=plan,
                owner_key=actor.user_key,
                routine_name="Ad-hoc scene",
                source="ad_hoc_scene",
            )
            return TaskCommandResult(
                handled=True,
                success=bool(outcome.get("success")),
                response=self._outcome_response("The scene", outcome),
                intent="scene_run",
                details={"plan": asdict(plan), **outcome},
            )

        return TaskCommandResult(handled=False)
