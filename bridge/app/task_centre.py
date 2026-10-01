"""Principal-scoped projection over Jarvis's existing durable task engines.

The Task Centre deliberately owns no execution semantics.  It reads the
authoritative stores, normalises their states for clients, and delegates every
mutation to the engine that created the task.  Its only durable state is the
user's notification preference and the exactly-once delivery ledger for that
preference.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.agent_planner import AgentPlan, PlanStatus, StepStatus
from app.connectors.credentials import redact_secrets, redact_text

logger = logging.getLogger("jarvis-core.task-centre")


class TaskCentreStatus(str, Enum):
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_JARVIS = "WAITING_FOR_JARVIS"
    WAITING_FOR_YOU = "WAITING_FOR_YOU"
    SCHEDULED = "SCHEDULED"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_STATUSES = {
    TaskCentreStatus.PLANNING.value,
    TaskCentreStatus.RUNNING.value,
    TaskCentreStatus.WAITING_FOR_JARVIS.value,
    TaskCentreStatus.WAITING_FOR_YOU.value,
    TaskCentreStatus.SCHEDULED.value,
    TaskCentreStatus.PAUSED.value,
}
TERMINAL_STATUSES = {
    TaskCentreStatus.COMPLETED.value,
    TaskCentreStatus.PARTIAL.value,
    TaskCentreStatus.FAILED.value,
    TaskCentreStatus.CANCELLED.value,
}

Notifier = Callable[..., Awaitable[Mapping[str, Any]]]
# Stable provider-neutral mobile/API projection.  Execution engines keep their
# own authoritative models; every adapter emits this same redacted contract.
WorkItem = dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _external_conversation_id(value: str, principal_id: str) -> str:
    prefix = f"usr:{principal_id}:"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _principal_from_conversation(value: str) -> str | None:
    if not value.startswith("usr:"):
        return None
    parts = value.split(":", 2)
    return parts[1] if len(parts) == 3 and parts[1] else None


def _safe_text(value: Any, *, limit: int = 500) -> str | None:
    rendered = " ".join(str(value or "").split()).strip()
    if not rendered:
        return None
    return redact_text(rendered[:limit])


def _recent_task(task: Mapping[str, Any], *, hours: int = 24) -> bool:
    raw = str(task.get("updated_at") or task.get("created_at") or "")
    if not raw:
        return False
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = _now() - observed.astimezone(timezone.utc)
    return timedelta(0) <= age <= timedelta(hours=hours)


class TaskCentre:
    """Normalise and control existing durable work without duplicating it."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        followups: Any,
        executive_store: Any,
        planner: Any,
        email_policies: Any,
        notifier: Notifier | None,
        legacy_task_path: str | Path | None = None,
        legacy_recurring_path: str | Path | None = None,
        poll_seconds: int = 5,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.followups = followups
        self.executive_store = executive_store
        self.planner = planner
        self.email_policies = email_policies
        self.notifier = notifier
        self.legacy_task_path = Path(legacy_task_path) if legacy_task_path else None
        self.legacy_recurring_path = Path(legacy_recurring_path) if legacy_recurring_path else None
        self.poll_seconds = max(2, min(int(poll_seconds), 60))
        self._stop = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._init()

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def _planner_principal_scope(self, principal_id: str):
        """Preserve principal-scoped capability checks for mobile/chat controls."""

        executor = getattr(self.planner, "executor", None)
        setter = getattr(executor, "set_principal", None)
        resetter = getattr(executor, "reset_principal", None)
        token = setter(principal_id) if callable(setter) else None
        try:
            yield
        finally:
            if token is not None and callable(resetter):
                resetter(token)

    def _init(self) -> None:
        with self._db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_notification_subscriptions (
                    principal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    conversation_id TEXT,
                    task_title TEXT NOT NULL,
                    notify_on_completion INTEGER NOT NULL DEFAULT 0,
                    notify_on_failure INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(principal_id, task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_task_subscriptions_pending
                    ON task_notification_subscriptions(principal_id, updated_at);
                CREATE TABLE IF NOT EXISTS task_notification_deliveries (
                    principal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    terminal_status TEXT NOT NULL,
                    delivery_kind TEXT NOT NULL DEFAULT 'completion',
                    state TEXT NOT NULL,
                    provider_reference TEXT,
                    delivery_message TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(principal_id, task_id, terminal_status)
                );
                CREATE TABLE IF NOT EXISTS task_centre_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_centre_events
                    ON task_centre_events(principal_id, task_id, event_id);
                """
            )
            delivery_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(task_notification_deliveries)"
                ).fetchall()
            }
            if "delivery_message" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE task_notification_deliveries ADD COLUMN delivery_message TEXT"
                )
            if "delivery_kind" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE task_notification_deliveries ADD COLUMN "
                    "delivery_kind TEXT NOT NULL DEFAULT 'completion'"
                )
            connection.execute(
                "UPDATE task_notification_deliveries SET state='outcome_unknown',"
                "error='Core restarted after notification submission began',updated_at=? "
                "WHERE state='attempting'",
                (_iso(),),
            )

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._stop = asyncio.Event()
        self._worker = asyncio.create_task(self._run(), name="jarvis-task-centre")

    async def stop(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.process_notifications_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Task completion notification cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    def _subscriptions(self, principal_id: str) -> dict[str, dict[str, Any]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM task_notification_subscriptions WHERE principal_id=?",
                (principal_id,),
            ).fetchall()
        return {str(row["task_id"]): dict(row) for row in rows}

    def _notification_deliveries(self, principal_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM task_notification_deliveries WHERE principal_id=? "
                "ORDER BY updated_at DESC",
                (principal_id,),
            ).fetchall()
        output: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            output.setdefault(str(row["task_id"]), []).append(dict(row))
        return output

    async def list_tasks(
        self,
        *,
        principal_id: str,
        filter_name: str = "ACTIVE",
        limit: int = 100,
    ) -> list[WorkItem]:
        maximum = max(1, min(int(limit), 250))
        tasks: list[WorkItem] = []
        tasks.extend(await self._executive_tasks(principal_id, maximum))
        tasks.extend(await self._followup_tasks(principal_id, maximum))
        tasks.extend(await self._email_bulk_tasks(principal_id, maximum))
        tasks.extend(await self._orphan_plan_tasks(principal_id, tasks, maximum))
        tasks.extend(self._legacy_scheduled_tasks(principal_id, maximum))
        tasks.extend(self._legacy_recurring_tasks(principal_id, maximum))
        subscriptions = self._subscriptions(principal_id)
        deliveries = self._notification_deliveries(principal_id)
        for task in tasks:
            subscription = subscriptions.get(str(task["task_id"]), {})
            task["notification_on_completion"] = bool(subscription.get("notify_on_completion"))
            task["notification_on_failure"] = bool(subscription.get("notify_on_failure"))
            task_deliveries = deliveries.get(str(task["task_id"]), [])
            completion = next(
                (item for item in task_deliveries if item.get("delivery_kind") == "completion"),
                None,
            )
            failure = next(
                (item for item in task_deliveries if item.get("delivery_kind") == "failure"),
                None,
            )
            task["completion_notification_state"] = (
                str(completion.get("state"))
                if completion
                else "pending"
                if task["notification_on_completion"]
                else "not_requested"
            )
            task["failure_notification_state"] = (
                str(failure.get("state"))
                if failure
                else "pending"
                if task["notification_on_failure"]
                else "not_requested"
            )
            latest_delivery = task_deliveries[0] if task_deliveries else None
            task["notification_delivered_at"] = (
                latest_delivery.get("updated_at")
                if latest_delivery and latest_delivery.get("state") == "delivered"
                else None
            )
            task["notification_delivery_message"] = (
                _safe_text(latest_delivery.get("delivery_message"), limit=500)
                if latest_delivery
                else None
            )
        filtered = [task for task in tasks if self.matches_filter(task, filter_name)]
        filtered.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return filtered[:maximum]

    @staticmethod
    def matches_filter(task: Mapping[str, Any], filter_name: str) -> bool:
        status = str(task.get("status") or "")
        selected = str(filter_name or "ACTIVE").strip().upper()
        if selected == "ALL":
            return True
        if selected == "ACTIVE":
            return status in {
                TaskCentreStatus.PLANNING.value,
                TaskCentreStatus.RUNNING.value,
                TaskCentreStatus.WAITING_FOR_JARVIS.value,
                TaskCentreStatus.WAITING_FOR_YOU.value,
            }
        if selected == "WAITING_FOR_YOU":
            return status == TaskCentreStatus.WAITING_FOR_YOU.value
        if selected == "SCHEDULED":
            return status in {
                TaskCentreStatus.SCHEDULED.value,
                TaskCentreStatus.PAUSED.value,
            }
        if selected == "COMPLETED":
            return status in {
                TaskCentreStatus.COMPLETED.value,
                TaskCentreStatus.CANCELLED.value,
            }
        if selected == "PROBLEMS":
            return status in {
                TaskCentreStatus.PARTIAL.value,
                TaskCentreStatus.FAILED.value,
                TaskCentreStatus.WAITING_FOR_JARVIS.value,
            }
        raise ValueError("Unsupported Task Centre filter")

    async def _executive_tasks(self, principal_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.executive_store.list_tasks(principal_id=principal_id, limit=limit)
        output: list[dict[str, Any]] = []
        for row in rows:
            plan = await self.planner.get(str(row.get("plan_id"))) if row.get("plan_id") else None
            output.append(self._project_executive(row, plan))
        return output

    def _project_executive(self, row: Mapping[str, Any], plan: AgentPlan | None) -> dict[str, Any]:
        raw_status = str(row.get("status") or "pending")
        status = {
            "pending": TaskCentreStatus.PLANNING,
            "planning": TaskCentreStatus.PLANNING,
            "running": TaskCentreStatus.RUNNING,
            "waiting_tool": TaskCentreStatus.WAITING_FOR_JARVIS,
            "waiting_user": TaskCentreStatus.WAITING_FOR_YOU,
            "completed": TaskCentreStatus.COMPLETED,
            "partial": TaskCentreStatus.PARTIAL,
            "failed": TaskCentreStatus.FAILED,
            "cancelled": TaskCentreStatus.CANCELLED,
            "superseded": TaskCentreStatus.CANCELLED,
        }.get(raw_status, TaskCentreStatus.WAITING_FOR_JARVIS)
        steps = self._safe_plan_steps(plan)
        completed = sum(1 for item in steps if item["status"] == "succeeded")
        current = next(
            (
                item
                for item in steps
                if item["status"] in {"running", "awaiting_approval", "blocked", "outcome_unknown"}
            ),
            None,
        )
        pending = next((item for item in steps if item["status"] == "pending"), None)
        approval_steps = [item for item in steps if item["status"] == "awaiting_approval"]
        blocked_step = next(
            (item for item in steps if item["status"] in {"blocked", "outcome_unknown"}),
            None,
        )
        waiting = _safe_text(row.get("waiting_reason")) or (blocked_step or {}).get("failure")
        if (
            status is TaskCentreStatus.WAITING_FOR_JARVIS
            and blocked_step is not None
            and (
                not bool(blocked_step.get("retryable"))
                or blocked_step.get("failure_code")
                in {
                    "capability_missing",
                    "capability_access_denied",
                    "verification_unsupported",
                }
            )
        ):
            status = TaskCentreStatus.WAITING_FOR_YOU
        requires_user = status is TaskCentreStatus.WAITING_FOR_YOU
        retryable_failure = any(
            bool(item.get("retryable"))
            for item in steps
            if item.get("status") in {"blocked", "failed"}
        )
        return self._task(
            task_id=f"executive:{row['task_id']}",
            task_type="executive",
            title=_safe_text(row.get("objective"), limit=120) or "Executive task",
            summary=_safe_text(row.get("last_verified_result"), limit=280),
            status=status,
            underlying_status=raw_status,
            conversation_id=str(row.get("conversation_id") or ""),
            created_at=row.get("created_at"),
            started_at=row.get("confirmed_at"),
            updated_at=row.get("updated_at"),
            completed_at=(row.get("updated_at") if status.value in TERMINAL_STATUSES else None),
            current_step=(current or {}).get("title") or _safe_text(row.get("current_step")),
            current_step_index=(steps.index(current) + 1 if current in steps else None),
            step_count=len(steps) or None,
            progress_current=completed if steps else None,
            progress_total=len(steps) if steps else None,
            progress_unit="steps" if steps else None,
            next_step=(pending or {}).get("title"),
            waiting_reason=waiting,
            error_summary=_safe_text(row.get("error_summary"), limit=400),
            providers=self._providers_from_steps(steps),
            capabilities=[item["capability"] for item in steps if item.get("capability")],
            requires_user_action=requires_user,
            user_action_type=(
                "confirmation"
                if requires_user and len(approval_steps) == 1
                else "choose_confirmation"
                if requires_user and approval_steps
                else "capability_setup_or_intervention"
                if requires_user
                else None
            ),
            can_confirm=requires_user and len(approval_steps) == 1,
            can_decline=requires_user and len(approval_steps) == 1,
            can_cancel=status.value in ACTIVE_STATUSES,
            can_retry=(
                status
                in {
                    TaskCentreStatus.WAITING_FOR_JARVIS,
                    TaskCentreStatus.PARTIAL,
                    TaskCentreStatus.FAILED,
                }
                and retryable_failure
            ),
            can_steer=status.value in ACTIVE_STATUSES,
            result_summary=_safe_text(row.get("last_verified_result"), limit=500),
            planned_steps=steps,
            metadata={
                "plan_id": row.get("plan_id"),
                "confirmation_step_ids": [item["step_id"] for item in approval_steps],
            },
        )

    async def _followup_tasks(self, principal_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.followups.list(principal_id=principal_id, limit=limit)
        output: list[dict[str, Any]] = []
        for row in rows:
            raw_status = str(row.get("status") or "pending")
            status = {
                "pending": TaskCentreStatus.SCHEDULED,
                "executing": TaskCentreStatus.RUNNING,
                "delivery_pending": TaskCentreStatus.WAITING_FOR_JARVIS,
                "delivering": TaskCentreStatus.RUNNING,
                "paused": TaskCentreStatus.PAUSED,
                "completed": TaskCentreStatus.COMPLETED,
                "failed": TaskCentreStatus.FAILED,
                "expired": TaskCentreStatus.FAILED,
                "cancelled": TaskCentreStatus.CANCELLED,
            }.get(raw_status, TaskCentreStatus.WAITING_FOR_JARVIS)
            payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
            kind = str(row.get("kind") or "task")
            title = self._followup_title(kind, payload)
            result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
            error = _safe_text(result.get("error") or result.get("reason"), limit=400)
            output.append(
                self._task(
                    task_id=f"followup:{row['job_id']}",
                    task_type="external_monitor" if kind == "external_monitor" else "followup",
                    title=title,
                    summary=_safe_text(result.get("summary") or payload.get("label"), limit=280),
                    status=status,
                    underlying_status=raw_status,
                    conversation_id=str(row.get("conversation_id") or ""),
                    created_at=row.get("created_at"),
                    updated_at=row.get("updated_at") or row.get("created_at"),
                    completed_at=row.get("delivered_at"),
                    current_step=("Checking the condition" if raw_status == "executing" else None),
                    next_step=(
                        "Run at the scheduled time"
                        if status is TaskCentreStatus.SCHEDULED
                        else None
                    ),
                    waiting_reason=(
                        "Waiting for the next scheduled check"
                        if status is TaskCentreStatus.SCHEDULED
                        else None
                    ),
                    error_summary=error,
                    providers=[str(payload.get("provider"))] if payload.get("provider") else [],
                    capabilities=[str(row.get("capability_id"))]
                    if row.get("capability_id")
                    else [],
                    requires_user_action=False,
                    can_cancel=status.value in ACTIVE_STATUSES,
                    can_pause=raw_status == "pending",
                    can_resume=raw_status == "paused",
                    can_reschedule=raw_status in {"pending", "paused"},
                    result_summary=_safe_text(
                        result.get("response")
                        or result.get("message")
                        or row.get("delivery_message"),
                        limit=500,
                    ),
                    scheduled_at=row.get("next_run_at"),
                    recurrence=row.get("schedule"),
                    metadata={
                        "source_notification_requested": payload.get("notify") is True,
                        "source_notification_state": row.get("notification_state"),
                    },
                )
            )
        return output

    async def _email_bulk_tasks(self, principal_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.email_policies.list_bulk_actions(principal_id=principal_id, limit=limit)
        grouped: dict[str, list[dict[str, Any]]] = {}
        singles: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            group_id = str(row.get("task_group_id") or "").strip()
            if group_id:
                grouped.setdefault(group_id, []).append(row)
            else:
                singles.append(row)
        output: list[dict[str, Any]] = []
        output.extend(
            self._project_email_bulk_group(key, values) for key, values in grouped.items()
        )
        output.extend(self._project_email_bulk_row(row) for row in singles)
        return output

    @staticmethod
    def _requires_provider_reconnect(row: Mapping[str, Any]) -> bool:
        reason = str(row.get("halt_reason") or "").casefold()
        return any(
            marker in reason
            for marker in (
                "invalid_grant",
                "needs reconnect",
                "needs reconnecting",
                "not connected",
                "reauthor",
                "sign in again",
                "consent revoked",
            )
        )

    def _project_email_bulk_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw_status = str(row.get("status") or "awaiting_confirmation")
        status = {
            "previewing": TaskCentreStatus.RUNNING,
            "awaiting_confirmation": TaskCentreStatus.WAITING_FOR_YOU,
            "running": TaskCentreStatus.RUNNING,
            "interrupted": TaskCentreStatus.WAITING_FOR_JARVIS,
            "partial": TaskCentreStatus.PARTIAL,
            "completed": TaskCentreStatus.COMPLETED,
            "failed": TaskCentreStatus.FAILED,
            "expired": TaskCentreStatus.FAILED,
            "cancelled": TaskCentreStatus.CANCELLED,
        }.get(raw_status, TaskCentreStatus.WAITING_FOR_JARVIS)
        reconnect = raw_status == "interrupted" and self._requires_provider_reconnect(row)
        if reconnect:
            status = TaskCentreStatus.WAITING_FOR_YOU
        provider = str(row.get("provider") or "")
        intended = int(row.get("intended_count") or 0)
        succeeded = int(row.get("succeeded_count") or 0)
        attempted = int(row.get("attempted_count") or 0)
        destination = (
            "Gmail Bin"
            if provider == "google_gmail" and row.get("operation") == "trash"
            else "Outlook Deleted Items"
            if provider == "microsoft_outlook" and row.get("operation") == "trash"
            else "Archive"
        )
        if raw_status == "awaiting_confirmation":
            activity = f"Waiting for confirmation before moving {intended:,} messages"
        elif raw_status == "running":
            activity = f"Moving verified messages to {destination}"
        else:
            activity = None
        return self._task(
            task_id=f"email_bulk:{row['bulk_action_id']}",
            task_type="email_cleanup",
            title="Inbox cleanup",
            summary=activity,
            status=status,
            underlying_status=raw_status,
            conversation_id=str(row.get("conversation_id") or ""),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            completed_at=row.get("completed_at"),
            current_step=activity,
            progress_current=succeeded,
            progress_total=intended,
            progress_unit="messages",
            next_step=(
                "Move the exact frozen messages after confirmation"
                if raw_status == "awaiting_confirmation"
                else "Verify the remaining messages"
                if raw_status in {"running", "interrupted", "partial"}
                else None
            ),
            waiting_reason=_safe_text(row.get("halt_reason"), limit=400),
            error_summary=(
                _safe_text(row.get("halt_reason"), limit=400)
                if status in {TaskCentreStatus.FAILED, TaskCentreStatus.PARTIAL}
                else None
            ),
            providers=[provider],
            capabilities=[f"{provider}.email_cleanup"],
            requires_user_action=raw_status == "awaiting_confirmation" or reconnect,
            user_action_type=(
                "confirmation"
                if raw_status == "awaiting_confirmation"
                else "provider_reconnect"
                if reconnect
                else None
            ),
            can_confirm=raw_status == "awaiting_confirmation",
            can_decline=raw_status == "awaiting_confirmation",
            can_cancel=raw_status
            in {"previewing", "awaiting_confirmation", "interrupted", "partial"},
            can_retry=raw_status in {"interrupted", "partial", "expired"} and not reconnect,
            result_summary=(
                f"{succeeded:,} of {intended:,} messages verified"
                if attempted or status.value in TERMINAL_STATUSES
                else "No email has been changed yet"
            ),
            metadata={
                "account_id": row.get("account_id"),
                "operation": row.get("operation"),
                "attempted": attempted,
                "failed": int(row.get("failed_count") or 0),
                "bulk_action_ids": [row.get("bulk_action_id")],
            },
        )

    def _project_email_bulk_group(
        self, group_id: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        raw_statuses = {str(row.get("status") or "") for row in rows}
        reconnect_rows = [row for row in rows if self._requires_provider_reconnect(row)]
        confirmation_rows = [
            row for row in rows if str(row.get("status") or "") == "awaiting_confirmation"
        ]
        if raw_statuses & {"previewing", "running"}:
            status = TaskCentreStatus.RUNNING
        elif reconnect_rows:
            status = TaskCentreStatus.WAITING_FOR_YOU
        elif "interrupted" in raw_statuses:
            status = TaskCentreStatus.WAITING_FOR_JARVIS
        elif "awaiting_confirmation" in raw_statuses:
            status = TaskCentreStatus.WAITING_FOR_YOU
        elif raw_statuses == {"completed"}:
            status = TaskCentreStatus.COMPLETED
        elif "completed" in raw_statuses and raw_statuses - {"completed", "cancelled"}:
            status = TaskCentreStatus.PARTIAL
        elif "partial" in raw_statuses:
            status = TaskCentreStatus.PARTIAL
        elif raw_statuses & {"failed", "expired"}:
            status = TaskCentreStatus.FAILED
        elif raw_statuses <= {"cancelled", "completed"} and "completed" in raw_statuses:
            status = TaskCentreStatus.PARTIAL
        else:
            status = TaskCentreStatus.CANCELLED

        providers = list(dict.fromkeys(str(row.get("provider") or "") for row in rows))
        provider_names = [
            "Gmail"
            if provider == "google_gmail"
            else "Outlook"
            if provider == "microsoft_outlook"
            else provider
            for provider in providers
        ]
        intended = sum(int(row.get("intended_count") or 0) for row in rows)
        succeeded = sum(int(row.get("succeeded_count") or 0) for row in rows)
        attempted = sum(int(row.get("attempted_count") or 0) for row in rows)
        waiting_rows = [row for row in rows if str(row.get("status")) in {"interrupted", "failed"}]
        waiting_reason = "; ".join(
            f"{'Gmail' if row.get('provider') == 'google_gmail' else 'Outlook'}: "
            f"{row.get('halt_reason')}"
            for row in waiting_rows
            if row.get("halt_reason")
        )
        if status is TaskCentreStatus.RUNNING and "previewing" in raw_statuses:
            activity = f"Scanning {' and '.join(provider_names)}"
        elif status is TaskCentreStatus.RUNNING:
            activity = f"Applying the confirmed cleanup in {' and '.join(provider_names)}"
        elif status is TaskCentreStatus.WAITING_FOR_YOU and reconnect_rows:
            reconnect_names = [
                "Gmail" if row.get("provider") == "google_gmail" else "Outlook"
                for row in reconnect_rows
            ]
            activity = f"{' and '.join(reconnect_names)} needs reconnecting"
        elif status is TaskCentreStatus.WAITING_FOR_YOU:
            activity = f"Waiting for confirmation before moving {intended:,} messages"
        elif status is TaskCentreStatus.WAITING_FOR_JARVIS:
            blocked = [
                "Gmail" if row.get("provider") == "google_gmail" else "Outlook"
                for row in waiting_rows
            ]
            activity = f"Waiting for {' and '.join(blocked)}" if blocked else "Waiting to retry"
        else:
            activity = None
        planned_steps = []
        for row in rows:
            provider_name = "Gmail" if row.get("provider") == "google_gmail" else "Outlook"
            row_status = str(row.get("status") or "")
            planned_steps.append(
                {
                    "step_id": str(row.get("bulk_action_id") or ""),
                    "title": provider_name,
                    "status": row_status,
                    "result_summary": (
                        f"{int(row.get('succeeded_count') or 0):,} of "
                        f"{int(row.get('intended_count') or 0):,} verified"
                        if int(row.get("attempted_count") or 0)
                        else "No email has been changed yet"
                    ),
                    "failure": _safe_text(row.get("halt_reason"), limit=300),
                }
            )
        latest = max(rows, key=lambda item: str(item.get("updated_at") or ""))
        earliest = min(rows, key=lambda item: str(item.get("created_at") or ""))
        started_values = [str(row["confirmed_at"]) for row in rows if row.get("confirmed_at")]
        return self._task(
            task_id=f"email_group:{group_id}",
            task_type="email_cleanup",
            title="Inbox cleanup",
            summary=activity,
            status=status,
            underlying_status=",".join(sorted(raw_statuses)),
            conversation_id=str(latest.get("conversation_id") or ""),
            created_at=earliest.get("created_at"),
            started_at=min(started_values) if started_values else None,
            updated_at=latest.get("updated_at"),
            completed_at=(
                latest.get("completed_at") if status.value in TERMINAL_STATUSES else None
            ),
            current_step=activity,
            progress_current=succeeded,
            progress_total=intended,
            progress_unit="messages",
            next_step=(
                "Move each provider's exact frozen set after confirmation"
                if status is TaskCentreStatus.WAITING_FOR_YOU
                else "Retry only the provider work that did not complete"
                if status in {TaskCentreStatus.WAITING_FOR_JARVIS, TaskCentreStatus.PARTIAL}
                else None
            ),
            waiting_reason=_safe_text(waiting_reason, limit=500),
            error_summary=(
                _safe_text(waiting_reason, limit=500)
                if status in {TaskCentreStatus.FAILED, TaskCentreStatus.PARTIAL}
                else None
            ),
            providers=providers,
            capabilities=[f"{provider}.email_cleanup" for provider in providers],
            requires_user_action=status is TaskCentreStatus.WAITING_FOR_YOU,
            user_action_type=(
                "provider_reconnect"
                if reconnect_rows
                else "confirmation"
                if status is TaskCentreStatus.WAITING_FOR_YOU
                else None
            ),
            can_confirm=bool(confirmation_rows) and not bool(reconnect_rows),
            can_decline=bool(confirmation_rows) and not bool(reconnect_rows),
            can_cancel=bool(
                raw_statuses & {"awaiting_confirmation", "interrupted", "partial", "previewing"}
            ),
            can_retry=bool(raw_statuses & {"interrupted", "partial", "expired"})
            and not bool(reconnect_rows),
            result_summary=(
                f"{succeeded:,} of {intended:,} messages verified"
                if attempted or status.value in TERMINAL_STATUSES
                else "No email has been changed yet"
            ),
            planned_steps=planned_steps,
            metadata={
                "bulk_action_ids": [str(row.get("bulk_action_id") or "") for row in rows],
                "operation": latest.get("operation"),
                "provider_progress": planned_steps,
                "bulk_action_context": [
                    {
                        "bulk_action_id": str(row.get("bulk_action_id") or ""),
                        "provider": row.get("provider"),
                        "account_id": row.get("account_id"),
                        "intended_count": int(row.get("intended_count") or 0),
                    }
                    for row in rows
                ],
            },
        )

    async def _orphan_plan_tasks(
        self, principal_id: str, existing: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        linked = {
            str(item.get("metadata", {}).get("plan_id"))
            for item in existing
            if isinstance(item.get("metadata"), Mapping) and item.get("metadata", {}).get("plan_id")
        }
        plans = await self.planner.list_plans(limit=limit)
        output: list[dict[str, Any]] = []
        for plan in plans:
            if plan.plan_id in linked:
                continue
            if _principal_from_conversation(plan.conversation_id) != principal_id:
                continue
            output.append(self._project_plan(plan))
        return output

    def _project_plan(self, plan: AgentPlan) -> dict[str, Any]:
        status = {
            PlanStatus.PENDING: TaskCentreStatus.PLANNING,
            PlanStatus.RUNNING: TaskCentreStatus.RUNNING,
            PlanStatus.AWAITING_APPROVAL: TaskCentreStatus.WAITING_FOR_YOU,
            PlanStatus.BLOCKED: TaskCentreStatus.WAITING_FOR_JARVIS,
            PlanStatus.PARTIAL: TaskCentreStatus.PARTIAL,
            PlanStatus.FAILED: TaskCentreStatus.FAILED,
            PlanStatus.COMPLETED: TaskCentreStatus.COMPLETED,
            PlanStatus.CANCELLED: TaskCentreStatus.CANCELLED,
        }[plan.status]
        steps = self._safe_plan_steps(plan)
        completed = sum(1 for item in steps if item["status"] == "succeeded")
        current = next((item for item in steps if item["status"] == "running"), None)
        waiting = next(
            (
                item
                for item in steps
                if item["status"] in {"blocked", "outcome_unknown", "awaiting_approval"}
            ),
            None,
        )
        pending = next((item for item in steps if item["status"] == "pending"), None)
        approval_steps = [item for item in steps if item["status"] == "awaiting_approval"]
        if status is TaskCentreStatus.WAITING_FOR_JARVIS and waiting is not None:
            failure_code = str(waiting.get("failure_code") or "")
            retryable = bool(waiting.get("retryable"))
            if not retryable or failure_code in {
                "capability_missing",
                "capability_access_denied",
                "verification_unsupported",
            }:
                status = TaskCentreStatus.WAITING_FOR_YOU
        requires_user = status is TaskCentreStatus.WAITING_FOR_YOU
        waiting_reason = (waiting or {}).get("failure")
        if approval_steps and not waiting_reason:
            waiting_reason = "Confirmation required before Jarvis can continue"
        retryable_failure = any(
            bool(item.get("retryable"))
            for item in steps
            if item.get("status") in {"blocked", "failed"}
        )
        selected_current = current or waiting
        verified_result = next(
            (
                str(item.get("result_summary") or "").strip()
                for item in reversed(steps)
                if item.get("status") == "succeeded" and item.get("result_summary")
            ),
            None,
        )
        return self._task(
            task_id=f"agent_plan:{plan.plan_id}",
            task_type="multi_tool_plan",
            title=_safe_text(plan.goal, limit=120) or "Jarvis task",
            summary=None,
            status=status,
            underlying_status=plan.status.value,
            conversation_id=plan.conversation_id,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            completed_at=(plan.updated_at if status.value in TERMINAL_STATUSES else None),
            current_step=(selected_current or {}).get("title"),
            current_step_index=(
                steps.index(selected_current) + 1 if selected_current in steps else None
            ),
            step_count=len(steps),
            progress_current=completed,
            progress_total=len(steps),
            progress_unit="steps",
            next_step=(pending or {}).get("title"),
            waiting_reason=waiting_reason,
            error_summary=(waiting or {}).get("failure")
            if status is TaskCentreStatus.FAILED
            else None,
            providers=self._providers_from_steps(steps),
            capabilities=[item["capability"] for item in steps if item.get("capability")],
            requires_user_action=requires_user,
            user_action_type=(
                "confirmation"
                if len(approval_steps) == 1
                else "choose_confirmation"
                if approval_steps
                else "capability_setup_or_intervention"
                if requires_user
                else None
            ),
            can_confirm=len(approval_steps) == 1,
            can_decline=len(approval_steps) == 1,
            can_cancel=status.value in ACTIVE_STATUSES,
            can_retry=(
                status
                in {
                    TaskCentreStatus.WAITING_FOR_JARVIS,
                    TaskCentreStatus.PARTIAL,
                    TaskCentreStatus.FAILED,
                }
                and retryable_failure
            ),
            result_summary=verified_result,
            planned_steps=steps,
            metadata={
                "plan_id": plan.plan_id,
                "confirmation_step_ids": [item["step_id"] for item in approval_steps],
            },
        )

    @staticmethod
    def _safe_plan_steps(plan: AgentPlan | None) -> list[dict[str, Any]]:
        if plan is None:
            return []
        output: list[dict[str, Any]] = []
        for step in plan.steps:
            failure = step.failure.message if step.failure is not None else None
            result_summary = None
            if isinstance(step.result, Mapping):
                result_summary = _safe_text(
                    step.result.get("summary")
                    or step.result.get("response_message")
                    or step.result.get("message"),
                    limit=280,
                )
            output.append(
                {
                    "step_id": step.step_id,
                    "title": _safe_text(step.title, limit=160) or "Task step",
                    "status": step.status.value,
                    "capability": step.capability.capability_id,
                    "capability_access": step.capability.access.value,
                    "evidence_requirement": step.capability.evidence.value,
                    "depends_on": list(step.depends_on),
                    "attempts": step.attempts,
                    "max_attempts": step.max_attempts,
                    "started_at": step.started_at,
                    "completed_at": step.completed_at,
                    "failure": _safe_text(failure, limit=300),
                    "failure_code": (
                        _safe_text(step.failure.code, limit=100)
                        if step.failure is not None
                        else None
                    ),
                    "retryable": bool(step.failure.retryable) if step.failure else False,
                    "result_summary": result_summary,
                    "requires_confirmation": step.required_confirmation,
                    "confirmation_status": step.confirmation_status.value,
                    "verified_receipt": bool(step.action_receipt),
                }
            )
        return output

    @staticmethod
    def _providers_from_steps(steps: Sequence[Mapping[str, Any]]) -> list[str]:
        providers: list[str] = []
        for step in steps:
            capability = str(step.get("capability") or "")
            prefix = capability.split(".", 1)[0]
            if prefix and prefix not in providers:
                providers.append(prefix)
        return providers

    def _legacy_scheduled_tasks(self, principal_id: str, limit: int) -> list[dict[str, Any]]:
        path = self.legacy_task_path
        if path is None or not path.exists():
            return []
        return self._legacy_rows(path, "scheduled_tasks", "owner_key", principal_id, limit, False)

    def _legacy_recurring_tasks(self, principal_id: str, limit: int) -> list[dict[str, Any]]:
        path = self.legacy_recurring_path
        if path is None or not path.exists():
            return []
        return self._legacy_rows(
            path, "recurring_schedules", "owner_key", principal_id, limit, True
        )

    def _legacy_rows(
        self,
        path: Path,
        table: str,
        owner_column: str,
        principal_id: str,
        limit: int,
        recurring: bool,
    ) -> list[dict[str, Any]]:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE {owner_column}=? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (principal_id, limit),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            logger.warning("Could not read legacy Task Centre source %s", table)
            return []
        output: list[dict[str, Any]] = []
        for row_value in rows:
            row = dict(row_value)
            raw_status = str(row.get("status") or "active")
            status = {
                "active": TaskCentreStatus.SCHEDULED,
                "pending": TaskCentreStatus.SCHEDULED,
                "executing": TaskCentreStatus.RUNNING,
                "paused": TaskCentreStatus.PAUSED,
                "completed": TaskCentreStatus.COMPLETED,
                "failed": TaskCentreStatus.FAILED,
                "expired": TaskCentreStatus.FAILED,
                "cancelled": TaskCentreStatus.CANCELLED,
            }.get(raw_status, TaskCentreStatus.WAITING_FOR_JARVIS)
            identity = row.get("schedule_id") if recurring else row.get("task_id")
            output.append(
                self._task(
                    task_id=f"{'recurring' if recurring else 'scheduled'}:{identity}",
                    task_type="recurring" if recurring else "scheduled_action",
                    title=_safe_text(row.get("action_summary"), limit=120)
                    or ("Recurring task" if recurring else "Scheduled task"),
                    summary=_safe_text(row.get("recurrence_description"), limit=280),
                    status=status,
                    underlying_status=raw_status,
                    conversation_id="",
                    created_at=row.get("created_at"),
                    updated_at=row.get("updated_at"),
                    completed_at=row.get("executed_at")
                    if not recurring
                    else row.get("last_run_at"),
                    next_step="Run the scheduled action"
                    if status is TaskCentreStatus.SCHEDULED
                    else None,
                    waiting_reason=_safe_text(row.get("last_error") or row.get("error"), limit=400),
                    error_summary=_safe_text(row.get("last_error") or row.get("error"), limit=400),
                    providers=["home_assistant"],
                    capabilities=[],
                    requires_user_action=False,
                    can_cancel=False,
                    can_pause=False,
                    can_resume=False,
                    can_reschedule=False,
                    scheduled_at=row.get("next_run_at") if recurring else row.get("due_at"),
                    recurrence=(
                        {"description": row.get("recurrence_description")} if recurring else None
                    ),
                )
            )
        return output

    @staticmethod
    def _followup_title(kind: str, payload: Mapping[str, Any]) -> str:
        label = _safe_text(payload.get("label") or payload.get("message"), limit=120)
        if label:
            return label
        return {
            "external_monitor": "External monitor",
            "condition": "Condition monitor",
            "periodic": "Periodic check",
            "recurring": "Recurring reminder",
            "time": "Reminder",
            "scheduled": "Scheduled reminder",
            "completion": "Completion follow-up",
        }.get(kind, "Jarvis task")

    def _task(self, **values: Any) -> WorkItem:
        conversation_id = str(values.pop("conversation_id", "") or "")
        task = {
            "task_id": str(values.pop("task_id")),
            "task_type": str(values.pop("task_type")),
            "title": values.pop("title"),
            "summary": values.pop("summary", None),
            "status": values.pop("status").value,
            "underlying_status": values.pop("underlying_status"),
            "created_at": values.pop("created_at", None),
            "started_at": values.pop("started_at", None),
            "updated_at": values.pop("updated_at", None),
            "completed_at": values.pop("completed_at", None),
            "conversation_id": conversation_id,
            "open_chat_conversation_id": None,
            "current_step": values.pop("current_step", None),
            "current_step_index": values.pop("current_step_index", None),
            "step_count": values.pop("step_count", None),
            "progress_current": values.pop("progress_current", None),
            "progress_total": values.pop("progress_total", None),
            "progress_unit": values.pop("progress_unit", None),
            "next_step": values.pop("next_step", None),
            "waiting_reason": values.pop("waiting_reason", None),
            "error_summary": values.pop("error_summary", None),
            "providers": list(dict.fromkeys(values.pop("providers", []) or [])),
            "capabilities": list(dict.fromkeys(values.pop("capabilities", []) or [])),
            "requires_user_action": bool(values.pop("requires_user_action", False)),
            "user_action_type": values.pop("user_action_type", None),
            "notification_on_completion": False,
            "notification_on_failure": False,
            "can_cancel": bool(values.pop("can_cancel", False)),
            "can_pause": bool(values.pop("can_pause", False)),
            "can_resume": bool(values.pop("can_resume", False)),
            "can_retry": bool(values.pop("can_retry", False)),
            "can_steer": bool(values.pop("can_steer", False)),
            "can_reschedule": bool(values.pop("can_reschedule", False)),
            "can_confirm": bool(values.pop("can_confirm", False)),
            "can_decline": bool(values.pop("can_decline", False)),
            "result_summary": values.pop("result_summary", None),
            "planned_steps": values.pop("planned_steps", []),
            "scheduled_at": values.pop("scheduled_at", None),
            "recurrence": values.pop("recurrence", None),
            "metadata": values.pop("metadata", {}),
        }
        source, source_task_id = task["task_id"].split(":", 1)
        task["source"] = source
        task["source_task_id"] = source_task_id
        principal = _principal_from_conversation(conversation_id)
        if conversation_id and principal:
            task["open_chat_conversation_id"] = _external_conversation_id(
                conversation_id, principal
            )
        safe = redact_secrets(task)
        return dict(safe) if isinstance(safe, Mapping) else task

    async def get_task(self, *, principal_id: str, task_id: str) -> WorkItem | None:
        tasks = await self.list_tasks(principal_id=principal_id, filter_name="ALL", limit=250)
        task = next((item for item in tasks if item["task_id"] == task_id), None)
        if task is None:
            return None
        task["timeline"] = await self.timeline(principal_id=principal_id, task=task)
        return task

    async def timeline(self, *, principal_id: str, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        task_id = str(task["task_id"])
        timeline: list[dict[str, Any]] = [
            {
                "at": task.get("created_at"),
                "type": "created",
                "summary": "Task created",
            }
        ]
        if task.get("updated_at") and task.get("updated_at") != task.get("created_at"):
            timeline.append(
                {
                    "at": task.get("updated_at"),
                    "type": "durable_state",
                    "summary": (
                        "Current durable state: "
                        + str(task.get("status") or "unknown").replace("_", " ").title()
                    ),
                }
            )
        if task_id.startswith("executive:") or task_id.startswith("agent_plan:"):
            for step in task.get("planned_steps") or []:
                if not isinstance(step, Mapping):
                    continue
                if step.get("started_at"):
                    timeline.append(
                        {
                            "at": step.get("started_at"),
                            "type": "step_started",
                            "summary": f"{step.get('title')} started",
                        }
                    )
                if step.get("completed_at"):
                    timeline.append(
                        {
                            "at": step.get("completed_at"),
                            "type": "step_completed",
                            "summary": f"{step.get('title')} {step.get('status')}",
                        }
                    )
        elif task_id.startswith("followup:"):
            source_id = task_id.split(":", 1)[1]
            for event in await self.followups.audit(source_id, principal_id=principal_id):
                timeline.append(
                    {
                        "at": event.get("created_at"),
                        "type": event.get("operation"),
                        "summary": str(event.get("operation") or "Task updated")
                        .replace("_", " ")
                        .title(),
                    }
                )
        with self._db() as connection:
            rows = connection.execute(
                "SELECT event_type,summary,created_at FROM task_centre_events "
                "WHERE principal_id=? AND task_id=? ORDER BY event_id",
                (principal_id, task_id),
            ).fetchall()
        timeline.extend(
            {
                "at": row["created_at"],
                "type": row["event_type"],
                "summary": row["summary"],
            }
            for row in rows
        )
        timeline = [item for item in timeline if item.get("at")]
        timeline.sort(key=lambda item: str(item.get("at") or ""))
        return timeline

    def _record_event(
        self,
        *,
        principal_id: str,
        task_id: str,
        event_type: str,
        summary: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        with self._db() as connection:
            connection.execute(
                "INSERT INTO task_centre_events(principal_id,task_id,event_type,summary,"
                "evidence_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    principal_id,
                    task_id,
                    event_type,
                    _safe_text(summary, limit=300) or "Task updated",
                    json.dumps(redact_secrets(dict(evidence or {})), separators=(",", ":")),
                    _iso(),
                ),
            )

    async def set_notification_preference(
        self,
        *,
        principal_id: str,
        task_id: str,
        notify_on_completion: bool,
        notify_on_failure: bool,
    ) -> dict[str, Any] | None:
        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None:
            return None
        now = _iso()
        with self._db() as connection:
            connection.execute(
                """INSERT INTO task_notification_subscriptions(
                    principal_id,task_id,conversation_id,task_title,
                    notify_on_completion,notify_on_failure,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(principal_id,task_id) DO UPDATE SET
                    notify_on_completion=excluded.notify_on_completion,
                    notify_on_failure=excluded.notify_on_failure,
                    task_title=excluded.task_title,updated_at=excluded.updated_at""",
                (
                    principal_id,
                    task_id,
                    task.get("conversation_id"),
                    task.get("title") or "Jarvis task",
                    int(bool(notify_on_completion)),
                    int(bool(notify_on_failure)),
                    now,
                    now,
                ),
            )
        self._record_event(
            principal_id=principal_id,
            task_id=task_id,
            event_type="notification_preference",
            summary=(
                "Completion notification enabled"
                if notify_on_completion
                else "Failure notification enabled"
                if notify_on_failure
                else "Task notifications disabled"
            ),
            evidence={
                "explicit_user_request": True,
                "notify_on_completion": notify_on_completion,
                "notify_on_failure": notify_on_failure,
            },
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def bind_notification_from_conversation(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        notify_on_completion: bool,
        notify_on_failure: bool,
    ) -> dict[str, Any]:
        all_tasks = await self.list_tasks(principal_id=principal_id, filter_name="ALL", limit=100)
        scoped = [
            item
            for item in all_tasks
            if item.get("conversation_id") == conversation_id
            and str(item.get("status") or "") in ACTIVE_STATUSES
        ]
        active = [
            item
            for item in all_tasks
            if str(item.get("status") or "")
            in {
                TaskCentreStatus.PLANNING.value,
                TaskCentreStatus.RUNNING.value,
                TaskCentreStatus.WAITING_FOR_JARVIS.value,
                TaskCentreStatus.WAITING_FOR_YOU.value,
            }
        ]
        candidates = scoped or active
        if not candidates:
            return {
                "bound": False,
                "reason": "no_active_task",
                "response": "I don't have an active task to attach that notification to.",
            }
        if len(candidates) > 1:
            titles = self._candidate_labels(candidates)
            return {
                "bound": False,
                "reason": "ambiguous",
                "candidates": [item["task_id"] for item in candidates],
                "response": "Which one — " + " or ".join(titles[:3]) + "?",
            }
        task = candidates[0]
        disabling = not notify_on_completion and not notify_on_failure
        updated = await self.set_notification_preference(
            principal_id=principal_id,
            task_id=str(task["task_id"]),
            notify_on_completion=(
                False
                if disabling
                else bool(task.get("notification_on_completion")) or notify_on_completion
            ),
            notify_on_failure=(
                False
                if disabling
                else bool(task.get("notification_on_failure")) or notify_on_failure
            ),
        )
        title = str(task.get("title") or "that task").casefold()
        if notify_on_completion and notify_on_failure:
            reply = f"Yes — I’ll let you know when {title} finishes or if it fails."
        elif notify_on_failure:
            reply = f"Yes — I’ll let you know if {title} fails."
        elif notify_on_completion:
            reply = f"Yes — I’ll let you know when {title} finishes."
        else:
            reply = f"Okay — I won’t notify you about {title}."
        return {"bound": True, "task": updated, "response": reply}

    @staticmethod
    def _candidate_labels(candidates: list[dict[str, Any]]) -> list[str]:
        raw_titles = [str(item.get("title") or "that task") for item in candidates]
        labels: list[str] = []
        for index, item in enumerate(candidates):
            title = raw_titles[index]
            if raw_titles.count(title) > 1:
                providers = [
                    "Gmail"
                    if value == "google_gmail"
                    else "Outlook"
                    if value == "microsoft_outlook"
                    else str(value)
                    for value in item.get("providers") or ()
                ]
                if providers:
                    title += f" ({' and '.join(providers)})"
                else:
                    title += f" ({str(item.get('status') or 'task').replace('_', ' ').casefold()})"
            if title not in labels:
                labels.append(title)
        return labels

    async def retry_from_conversation(
        self, *, principal_id: str, conversation_id: str
    ) -> dict[str, Any]:
        """Retry one obvious durable task and describe only the resulting state."""

        tasks = await self.list_tasks(principal_id=principal_id, filter_name="ALL", limit=100)
        retryable = [item for item in tasks if item.get("can_retry")]
        scoped = [item for item in retryable if item.get("conversation_id") == conversation_id]
        candidates = scoped or [item for item in retryable if _recent_task(item)]
        if not candidates:
            return {"handled": False, "reason": "no_retryable_task"}
        if len(candidates) > 1:
            titles = self._candidate_labels(candidates)
            return {
                "handled": True,
                "retried": False,
                "reason": "ambiguous",
                "response": "Which one should I retry — " + " or ".join(titles[:3]) + "?",
            }
        previous = candidates[0]
        task = await self.retry(principal_id=principal_id, task_id=str(previous["task_id"]))
        if task is None:
            return {"handled": False, "reason": "not_retryable"}
        title = str(task.get("title") or "The task")
        status = str(task.get("status") or "")
        if status == TaskCentreStatus.RUNNING.value:
            response = f"{title} is running now."
        elif status == TaskCentreStatus.WAITING_FOR_YOU.value:
            response = (
                f"{title} finished checking the exact set and is waiting for your confirmation."
            )
        elif status == TaskCentreStatus.WAITING_FOR_JARVIS.value:
            reason = _safe_text(task.get("waiting_reason"), limit=240)
            response = f"{title} is still waiting"
            response += f" — {reason}." if reason else "."
        elif status == TaskCentreStatus.COMPLETED.value:
            response = f"{title} has completed."
        elif status == TaskCentreStatus.PARTIAL.value:
            response = f"{title} retried, but only part of it completed."
        elif status == TaskCentreStatus.FAILED.value:
            reason = _safe_text(task.get("error_summary"), limit=240)
            response = f"{title} still couldn't complete"
            response += f" — {reason}." if reason else "."
        else:
            response = f"{title} is now {status.replace('_', ' ').casefold()}."
        return {"handled": True, "retried": True, "task": task, "response": response}

    async def process_notifications_once(self) -> int:
        if self.notifier is None:
            return 0
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM task_notification_subscriptions "
                "WHERE notify_on_completion=1 OR notify_on_failure=1"
            ).fetchall()
        delivered = 0
        for raw in rows:
            subscription = dict(raw)
            principal_id = str(subscription["principal_id"])
            task_id = str(subscription["task_id"])
            task = await self.get_task(principal_id=principal_id, task_id=task_id)
            if task is None:
                continue
            status = str(task.get("status") or "")
            delivery_kind = ""
            if status == TaskCentreStatus.COMPLETED.value and bool(
                subscription["notify_on_completion"]
            ):
                delivery_kind = "completion"
            elif status == TaskCentreStatus.PARTIAL.value:
                if bool(subscription["notify_on_completion"]):
                    delivery_kind = "completion"
                elif bool(subscription["notify_on_failure"]):
                    delivery_kind = "failure"
            elif status == TaskCentreStatus.FAILED.value and bool(
                subscription["notify_on_failure"]
            ):
                delivery_kind = "failure"
            wants = bool(delivery_kind)
            if not wants:
                continue
            message = self._terminal_notification(task)
            now = _iso()
            metadata: Mapping[str, Any] = (
                task["metadata"] if isinstance(task.get("metadata"), Mapping) else {}
            )
            source_state = str(metadata.get("source_notification_state") or "")
            source_owns_delivery = bool(metadata.get("source_notification_requested"))
            if source_owns_delivery and source_state not in {"", "not_requested"}:
                covered_state = {
                    "accepted_unverified": "source_accepted_unverified",
                    "partially_accepted": "source_partially_accepted",
                    "outcome_unknown": "outcome_unknown",
                    "failed": "source_failed",
                }.get(source_state, f"source_{source_state}")
                with self._db() as connection:
                    inserted = connection.execute(
                        "INSERT OR IGNORE INTO task_notification_deliveries("
                        "principal_id,task_id,terminal_status,delivery_kind,state,"
                        "delivery_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            principal_id,
                            task_id,
                            status,
                            delivery_kind,
                            covered_state,
                            message,
                            now,
                            now,
                        ),
                    ).rowcount
                if inserted:
                    self._record_event(
                        principal_id=principal_id,
                        task_id=task_id,
                        event_type="terminal_notification",
                        summary="Completion notification handled by the task's delivery engine",
                        evidence={
                            "state": covered_state,
                            "terminal_status": status,
                        },
                    )
                continue
            with self._db() as connection:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO task_notification_deliveries("
                    "principal_id,task_id,terminal_status,delivery_kind,state,"
                    "delivery_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        principal_id,
                        task_id,
                        status,
                        delivery_kind,
                        "attempting",
                        message,
                        now,
                        now,
                    ),
                ).rowcount
            if not inserted:
                continue
            try:
                result = await self.notifier(
                    recipient=principal_id,
                    title="Jarvis task",
                    message=message,
                )
                accepted = bool(result.get("success") or result.get("command_accepted"))
                outcome_unknown = bool(result.get("outcome_unknown") or result.get("command_sent"))
                # Home Assistant accepting a notify service call is durable
                # evidence that Jarvis submitted the request, not evidence that
                # Android displayed it.  Keep the same truthful state vocabulary
                # as FollowUpEngine instead of upgrading acceptance to delivery.
                state = (
                    "accepted_unverified"
                    if accepted
                    else "outcome_unknown"
                    if outcome_unknown
                    else "failed"
                )
                reference = _safe_text(
                    result.get("provider_reference") or result.get("action_id"), limit=200
                )
                error = None if accepted else "Notification transport rejected the request"
            except Exception as exc:
                logger.exception("Task notification failed task=%s", task_id)
                state, reference, error = "outcome_unknown", None, type(exc).__name__
            with self._db() as connection:
                connection.execute(
                    "UPDATE task_notification_deliveries SET state=?,provider_reference=?,"
                    "error=?,updated_at=? WHERE principal_id=? AND task_id=? AND terminal_status=?",
                    (state, reference, error, _iso(), principal_id, task_id, status),
                )
            self._record_event(
                principal_id=principal_id,
                task_id=task_id,
                event_type="terminal_notification",
                summary=(
                    "Completion notification accepted by the delivery service"
                    if state == "accepted_unverified"
                    else "Completion notification was not verified"
                ),
                evidence={"state": state, "terminal_status": status},
            )
            delivered += int(state == "accepted_unverified")
        return delivered

    @staticmethod
    def _terminal_notification(task: Mapping[str, Any]) -> str:
        title = str(task.get("title") or "Jarvis task")
        status = str(task.get("status") or "")
        result = _safe_text(task.get("result_summary"), limit=300)
        error = _safe_text(task.get("error_summary") or task.get("waiting_reason"), limit=300)
        if status == TaskCentreStatus.COMPLETED.value:
            message = f"{title} finished."
            return f"{message} {result}" if result else message
        if status == TaskCentreStatus.PARTIAL.value:
            message = f"{title} partially completed."
            return f"{message} {result or error}" if result or error else message
        message = f"{title} needs your attention."
        return f"{message} {error}" if error else message

    async def cancel(
        self, *, principal_id: str, task_id: str, request_id: str
    ) -> dict[str, Any] | None:
        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_cancel"):
            return None
        source, identity = task_id.split(":", 1)
        if source == "followup":
            await self.followups.cancel(identity, principal_id=principal_id, request_id=request_id)
        elif source in {"email_bulk", "email_group"}:
            action_ids = (
                [identity]
                if source == "email_bulk"
                else [str(item) for item in task.get("metadata", {}).get("bulk_action_ids") or ()]
            )
            for action_id in action_ids:
                await self.email_policies.cancel_bulk_action(
                    principal_id=principal_id,
                    conversation_id=str(task.get("conversation_id") or ""),
                    bulk_action_id=action_id,
                )
        elif source in {"executive", "agent_plan"}:
            plan_id = (
                str(task.get("metadata", {}).get("plan_id") or "")
                if source == "executive"
                else identity
            )
            if plan_id:
                await self.planner.cancel(plan_id)
            if source == "executive":
                await self.executive_store.update_task(identity, status="cancelled")
        self._record_event(
            principal_id=principal_id,
            task_id=task_id,
            event_type="cancelled",
            summary="Cancelled by Aaron",
            evidence={"request_id": request_id},
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def pause(
        self, *, principal_id: str, task_id: str, request_id: str
    ) -> dict[str, Any] | None:
        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_pause") or not task_id.startswith("followup:"):
            return None
        await self.followups.pause(
            task_id.split(":", 1)[1], principal_id=principal_id, request_id=request_id
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def resume(
        self, *, principal_id: str, task_id: str, request_id: str
    ) -> dict[str, Any] | None:
        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_resume") or not task_id.startswith("followup:"):
            return None
        await self.followups.resume(
            task_id.split(":", 1)[1], principal_id=principal_id, request_id=request_id
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def reschedule(
        self,
        *,
        principal_id: str,
        task_id: str,
        request_id: str,
        due_at: datetime,
        timezone_name: str,
    ) -> dict[str, Any] | None:
        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_reschedule") or not task_id.startswith("followup:"):
            return None
        await self.followups.reschedule(
            task_id.split(":", 1)[1],
            principal_id=principal_id,
            request_id=request_id,
            due_at=due_at,
            timezone_name=timezone_name,
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def confirm(self, *, principal_id: str, task_id: str) -> dict[str, Any] | None:
        """Confirm exactly one current durable authority gate.

        The Task Centre never invents approval semantics.  It delegates an
        email frozen-set confirmation to the email engine and a generic plan
        step approval to the existing planner.  Any future registered
        capability therefore uses this same control without Android knowing
        its domain.
        """

        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_confirm"):
            return None
        source, identity = task_id.split(":", 1)
        if source in {"email_bulk", "email_group"}:
            action_ids = (
                [identity]
                if source == "email_bulk"
                else [str(item) for item in task.get("metadata", {}).get("bulk_action_ids") or ()]
            )
            for action_id in action_ids:
                await self.email_policies.execute_bulk_action(
                    principal_id=principal_id,
                    conversation_id=str(task.get("conversation_id") or ""),
                    bulk_action_id=action_id,
                )
        elif source in {"agent_plan", "executive"}:
            metadata: Mapping[str, Any] = (
                task["metadata"] if isinstance(task.get("metadata"), Mapping) else {}
            )
            plan_id = identity if source == "agent_plan" else str(metadata.get("plan_id") or "")
            step_ids = [str(item) for item in metadata.get("confirmation_step_ids") or ()]
            if not plan_id or len(step_ids) != 1:
                return None
            with self._planner_principal_scope(principal_id):
                await self.planner.approve(plan_id, step_ids[0], approved=True)
                plan = await self.planner.resume(plan_id)
            if source == "executive":
                await self._sync_executive_task(identity, plan)
        else:
            return None
        self._record_event(
            principal_id=principal_id,
            task_id=task_id,
            event_type="confirmed",
            summary="Confirmed by Aaron",
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def decline(
        self,
        *,
        principal_id: str,
        task_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        """Decline one authority gate without cancelling unrelated plan work."""

        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_decline"):
            return None
        source, identity = task_id.split(":", 1)
        if source in {"email_bulk", "email_group"}:
            return await self.cancel(
                principal_id=principal_id,
                task_id=task_id,
                request_id=request_id,
            )
        if source not in {"agent_plan", "executive"}:
            return None
        metadata: Mapping[str, Any] = (
            task["metadata"] if isinstance(task.get("metadata"), Mapping) else {}
        )
        plan_id = identity if source == "agent_plan" else str(metadata.get("plan_id") or "")
        step_ids = [str(item) for item in metadata.get("confirmation_step_ids") or ()]
        if not plan_id or len(step_ids) != 1:
            return None
        with self._planner_principal_scope(principal_id):
            await self.planner.approve(plan_id, step_ids[0], approved=False)
            plan = await self.planner.resume(plan_id)
        if source == "executive":
            await self._sync_executive_task(identity, plan)
        self._record_event(
            principal_id=principal_id,
            task_id=task_id,
            event_type="declined",
            summary="Confirmation declined by Aaron",
            evidence={"request_id": request_id},
        )
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    # Compatibility for internal callers while the mobile API is generic.
    confirm_email_cleanup = confirm

    async def _sync_executive_task(self, task_id: str, plan: AgentPlan) -> None:
        """Project authoritative plan state back to its executive task index."""

        status = {
            PlanStatus.PENDING: "running",
            PlanStatus.RUNNING: "running",
            PlanStatus.AWAITING_APPROVAL: "waiting_user",
            PlanStatus.BLOCKED: "waiting_tool",
            PlanStatus.PARTIAL: "partial",
            PlanStatus.FAILED: "failed",
            PlanStatus.COMPLETED: "completed",
            PlanStatus.CANCELLED: "cancelled",
        }[plan.status]
        current = next(
            (
                step
                for step in plan.steps
                if step.status
                in {
                    StepStatus.RUNNING,
                    StepStatus.AWAITING_APPROVAL,
                    StepStatus.BLOCKED,
                    StepStatus.OUTCOME_UNKNOWN,
                }
            ),
            None,
        )
        verified: list[str] = []
        for step in plan.steps:
            if step.status is not StepStatus.SUCCEEDED or not isinstance(step.result, Mapping):
                continue
            summary = _safe_text(
                (step.result or {}).get("summary")
                or (step.result or {}).get("response_message")
                or (step.result or {}).get("message"),
                limit=180,
            )
            if summary:
                verified.append(summary)
        await self.executive_store.update_task(
            task_id,
            status=status,
            current_step=current.title if current is not None else None,
            waiting_reason=(
                current.failure.message
                if current is not None and current.failure is not None
                else "Confirmation required before Jarvis can continue"
                if current is not None and current.status is StepStatus.AWAITING_APPROVAL
                else None
            ),
            last_verified_result="; ".join(verified[-3:]) or None,
            error_summary=(
                current.failure.message
                if plan.status in {PlanStatus.FAILED, PlanStatus.PARTIAL}
                and current is not None
                and current.failure is not None
                else None
            ),
        )

    async def retry(self, *, principal_id: str, task_id: str) -> dict[str, Any] | None:
        task = await self.get_task(principal_id=principal_id, task_id=task_id)
        if task is None or not task.get("can_retry"):
            return None
        source, identity = task_id.split(":", 1)
        if source in {"email_bulk", "email_group"}:
            action_ids = (
                [identity]
                if source == "email_bulk"
                else [str(item) for item in task.get("metadata", {}).get("bulk_action_ids") or ()]
            )
            conversation_id = str(task.get("conversation_id") or "")
            for action_id in action_ids:
                action = await self.email_policies.bulk_action(
                    principal_id=principal_id,
                    conversation_id=conversation_id,
                    bulk_action_id=action_id,
                )
                if action is None or action.get("status") not in {
                    "interrupted",
                    "partial",
                    "cancelled",
                    "expired",
                }:
                    continue
                await self.email_policies.retry_bulk_action(
                    principal_id=principal_id,
                    conversation_id=conversation_id,
                    bulk_action_id=action_id,
                )
        elif source in {"agent_plan", "executive"}:
            plan_id = (
                identity
                if source == "agent_plan"
                else str(task.get("metadata", {}).get("plan_id") or "")
            )
            if plan_id:
                with self._planner_principal_scope(principal_id):
                    plan = await self.planner.resume(plan_id)
                if source == "executive":
                    await self._sync_executive_task(identity, plan)
        return await self.get_task(principal_id=principal_id, task_id=task_id)

    async def health(self) -> dict[str, Any]:
        worker_running = self._worker is not None and not self._worker.done()
        try:
            with self._db() as connection:
                integrity = str(connection.execute("PRAGMA quick_check(1)").fetchone()[0])
                subscriptions = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_notification_subscriptions"
                    ).fetchone()[0]
                )
        except Exception as exc:
            return {
                "healthy": False,
                "worker_running": worker_running,
                "database_healthy": False,
                "reason": type(exc).__name__,
            }
        return {
            "healthy": worker_running and integrity.casefold() == "ok",
            "worker_running": worker_running,
            "database_healthy": integrity.casefold() == "ok",
            "subscription_count": subscriptions,
        }
