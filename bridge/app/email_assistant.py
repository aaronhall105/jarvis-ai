"""Durable Gmail policies built on the connector receipt boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Any, Protocol

from app.connectors import CapabilityRequest, ConnectorRegistry, ExecutionStatus, ReceiptStatus
from app.connectors.credentials import redact_text
from app.response_presentation import clean_email_reply_body, present_user_response


logger = logging.getLogger("jarvis-email-assistant")


class ConversationWriter(Protocol):
    async def ensure_conversation(
        self, conversation_id: str, *, source: str = "unknown"
    ) -> Mapping[str, Any]: ...

    async def add_assistant_message(
        self,
        conversation_id: str,
        content: str,
        *,
        delivery_key: str | None = None,
    ) -> Mapping[str, Any]: ...


NotificationSender = Callable[[str, str, str], Awaitable[Mapping[str, Any]]]


_PRIORITY_LEVELS = {"critical": 4, "important": 3, "normal": 2, "low": 1}
_LOW_VALUE_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
_PROTECTED_CATEGORIES = {
    "account/security",
    "finance/bill/receipt",
    "appointment/calendar",
    "delivery/order",
    "work/action",
    "legal/government",
    "medical",
    "travel/booking",
    "personal",
    "reply received",
}


class EmailAssistantPolicyEngine:
    """Persist and execute principal-scoped mailbox policies safely."""

    def __init__(
        self,
        database_path: str | Path,
        registry: ConnectorRegistry,
        *,
        poll_seconds: int = 60,
        conversations: ConversationWriter | None = None,
        notifier: NotificationSender | None = None,
        focus_recorder: Callable[[str, Mapping[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.poll_seconds = max(10, min(int(poll_seconds), 3600))
        self.conversations = conversations
        self.notifier = notifier
        self.focus_recorder = focus_recorder
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()
        self._init()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init(self) -> None:
        with self._db() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_policies (
                  policy_id TEXT PRIMARY KEY,
                  principal_id TEXT NOT NULL,
                  conversation_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  status TEXT NOT NULL,
                  retention_days INTEGER NOT NULL,
                  interval_seconds INTEGER NOT NULL,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  next_run_at TEXT NOT NULL,
                  last_run_at TEXT,
                  last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_email_policies_due
                  ON email_policies(status, next_run_at);
                CREATE INDEX IF NOT EXISTS idx_email_policies_principal
                  ON email_policies(principal_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS email_policy_items (
                  policy_id TEXT NOT NULL,
                  message_id TEXT NOT NULL,
                  eligibility_key TEXT NOT NULL,
                  status TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  action_id TEXT,
                  provider_reference TEXT,
                  evidence_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(policy_id, message_id, eligibility_key)
                );
                CREATE INDEX IF NOT EXISTS idx_email_policy_items_open
                  ON email_policy_items(policy_id, status, updated_at);

                CREATE TABLE IF NOT EXISTS email_policy_audit (
                  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  policy_id TEXT NOT NULL,
                  principal_id TEXT NOT NULL,
                  operation TEXT NOT NULL,
                  state TEXT NOT NULL,
                  evidence_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_email_policy_audit
                  ON email_policy_audit(policy_id, audit_id DESC);

                CREATE TABLE IF NOT EXISTS email_briefing_state (
                  principal_id TEXT NOT NULL,
                  conversation_id TEXT NOT NULL,
                  last_observed_epoch INTEGER NOT NULL,
                  provider_reference TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(principal_id, conversation_id)
                );

                CREATE TABLE IF NOT EXISTS email_assistant_profiles (
                  principal_id TEXT PRIMARY KEY,
                  conversation_id TEXT NOT NULL,
                  important_alerts_enabled INTEGER NOT NULL DEFAULT 0,
                  reply_alerts_enabled INTEGER NOT NULL DEFAULT 0,
                  cleanup_enabled INTEGER NOT NULL DEFAULT 0,
                  cleanup_dry_run INTEGER NOT NULL DEFAULT 1,
                  importance_threshold TEXT NOT NULL DEFAULT 'important',
                  cleanup_mode TEXT NOT NULL DEFAULT 'trash',
                  cleanup_age_days INTEGER NOT NULL DEFAULT 30,
                  protected_senders_json TEXT NOT NULL DEFAULT '[]',
                  poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
                  cleanup_interval_seconds INTEGER NOT NULL DEFAULT 86400,
                  history_id TEXT,
                  account_email TEXT,
                  status TEXT NOT NULL DEFAULT 'paused',
                  last_check_at TEXT,
                  next_check_at TEXT NOT NULL,
                  last_cleanup_at TEXT,
                  next_cleanup_at TEXT NOT NULL,
                  consecutive_failures INTEGER NOT NULL DEFAULT 0,
                  outage_fingerprint TEXT,
                  outage_notified_at TEXT,
                  last_error TEXT,
                  evaluated_count INTEGER NOT NULL DEFAULT 0,
                  important_detected_count INTEGER NOT NULL DEFAULT 0,
                  notified_count INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_email_assistant_due
                  ON email_assistant_profiles(status, next_check_at);

                CREATE TABLE IF NOT EXISTS email_assistant_events (
                  principal_id TEXT NOT NULL,
                  message_id TEXT NOT NULL,
                  thread_id TEXT,
                  event_kind TEXT NOT NULL,
                  priority TEXT NOT NULL,
                  conversation_id TEXT NOT NULL,
                  classification_json TEXT NOT NULL DEFAULT '{}',
                  notification_text TEXT NOT NULL,
                  status TEXT NOT NULL,
                  delivery_attempts INTEGER NOT NULL DEFAULT 0,
                  notification_state TEXT NOT NULL DEFAULT 'not_requested',
                  notification_result_json TEXT NOT NULL DEFAULT '{}',
                  next_attempt_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  delivered_at TEXT,
                  PRIMARY KEY(principal_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_email_assistant_events_delivery
                  ON email_assistant_events(status, next_attempt_at);

                CREATE TABLE IF NOT EXISTS email_reply_watches (
                  principal_id TEXT NOT NULL,
                  thread_id TEXT NOT NULL,
                  anchor_message_id TEXT NOT NULL,
                  anchor_epoch_ms INTEGER,
                  conversation_id TEXT NOT NULL,
                  recipient TEXT,
                  display_name TEXT,
                  source TEXT NOT NULL,
                  status TEXT NOT NULL,
                  last_reply_message_id TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(principal_id, thread_id)
                );
                CREATE INDEX IF NOT EXISTS idx_email_reply_watches_active
                  ON email_reply_watches(principal_id, status, updated_at);

                CREATE TABLE IF NOT EXISTS email_cleanup_protections (
                  principal_id TEXT NOT NULL,
                  message_id TEXT NOT NULL,
                  thread_id TEXT,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(principal_id, message_id)
                );
                """
            )
            policy_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(email_policies)").fetchall()
            }
            for column, definition in {
                "cleanup_mode": "TEXT NOT NULL DEFAULT 'trash'",
                "dry_run": "INTEGER NOT NULL DEFAULT 0",
                "protected_senders_json": "TEXT NOT NULL DEFAULT '[]'",
                "classification_version": "TEXT NOT NULL DEFAULT 'legacy-inbox-age-v1'",
                "last_result_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in policy_columns:
                    connection.execute(
                        f"ALTER TABLE email_policies ADD COLUMN {column} {definition}"
                    )
            profile_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(email_assistant_profiles)"
                ).fetchall()
            }
            for column in (
                "evaluated_count",
                "important_detected_count",
                "notified_count",
            ):
                if column not in profile_columns:
                    connection.execute(
                        f"ALTER TABLE email_assistant_profiles ADD COLUMN {column} "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
            # A process may stop between item persistence and provider execution.
            # Reconciliation always reads Gmail state before deciding what follows.
            connection.execute(
                "UPDATE email_policy_items SET status='outcome_unknown',"
                "error='Core restarted while the policy item was executing',updated_at=? "
                "WHERE status='executing'",
                (self._iso(self._now()),),
            )
            # If Core ended after handing a notification to Home Assistant, its
            # delivery outcome is unknowable.  Do not retry and risk a duplicate.
            connection.execute(
                "UPDATE email_assistant_events SET status='delivered',"
                "notification_state='outcome_unknown',updated_at=? "
                "WHERE status='delivering' AND notification_state='attempting'",
                (self._iso(self._now()),),
            )
            connection.execute(
                "UPDATE email_assistant_events SET status='pending',updated_at=? "
                "WHERE status='delivering' AND notification_state!='attempting'",
                (self._iso(self._now()),),
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        values = set(row.keys())
        return {
            "policy_id": str(row["policy_id"]),
            "principal_id": str(row["principal_id"]),
            "conversation_id": str(row["conversation_id"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "retention_days": int(row["retention_days"]),
            "interval_seconds": int(row["interval_seconds"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "next_run_at": str(row["next_run_at"]),
            "last_run_at": row["last_run_at"],
            "last_error": row["last_error"],
            "cleanup_mode": (str(row["cleanup_mode"]) if "cleanup_mode" in values else "trash"),
            "dry_run": bool(row["dry_run"]) if "dry_run" in values else False,
            "protected_senders": (
                json.loads(str(row["protected_senders_json"] or "[]"))
                if "protected_senders_json" in values
                else []
            ),
            "classification_version": (
                str(row["classification_version"])
                if "classification_version" in values
                else "legacy-inbox-age-v1"
            ),
            "last_result": (
                json.loads(str(row["last_result_json"] or "{}"))
                if "last_result_json" in values
                else {}
            ),
        }

    def _audit(
        self,
        connection: sqlite3.Connection,
        policy: Mapping[str, Any],
        operation: str,
        state: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO email_policy_audit "
            "(policy_id,principal_id,operation,state,evidence_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                policy["policy_id"],
                policy["principal_id"],
                operation,
                state,
                json.dumps(dict(evidence or {}), sort_keys=True, separators=(",", ":")),
                self._iso(self._now()),
            ),
        )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker(), name="jarvis-email-policy-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Email policy worker pass failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def create_retention_policy(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        retention_days: int = 30,
        interval_seconds: int = 86_400,
        request_id: str | None = None,
        kind: str = "inbox_retention",
        cleanup_mode: str = "trash",
        dry_run: bool = False,
        protected_senders: Sequence[str] = (),
    ) -> dict[str, Any]:
        principal = str(principal_id or "").strip()
        conversation = str(conversation_id or "").strip()
        if not principal or not conversation:
            raise ValueError("A principal and conversation are required")
        if conversation.startswith("usr:") and not conversation.startswith(f"usr:{principal}:"):
            raise ValueError("Email policy principal does not own its conversation")
        days = int(retention_days)
        interval = int(interval_seconds)
        if days < 1 or days > 3650:
            raise ValueError("Retention days must be between 1 and 3650")
        if interval < 3600 or interval > 30 * 86_400:
            raise ValueError("Retention interval must be between 1 hour and 30 days")
        policy_kind = str(kind or "").strip()
        if policy_kind not in {"inbox_retention", "safe_cleanup"}:
            raise ValueError("Unsupported email retention policy kind")
        mode = str(cleanup_mode or "").strip().casefold()
        if mode not in {"trash", "archive"}:
            raise ValueError("Cleanup mode must be archive or trash")
        protected = self._normalise_protected_senders(protected_senders)
        classification_version = (
            "safe-low-value-v1" if policy_kind == "safe_cleanup" else "legacy-inbox-age-v1"
        )
        key = str(request_id or uuid.uuid4()).strip()
        if not key or len(key) > 255:
            raise ValueError("A valid retention request ID is required")
        now = self._now()
        policy_id = str(uuid.uuid4())
        try:
            with self._db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                enabled = connection.execute(
                    "SELECT * FROM email_policies WHERE principal_id=? "
                    "AND kind=? AND status IN ('active','paused') "
                    "ORDER BY created_at LIMIT 1",
                    (principal, policy_kind),
                ).fetchone()
                if enabled is not None:
                    existing = self._row(enabled)
                    if (
                        existing["conversation_id"] == conversation
                        and existing["retention_days"] == days
                        and existing["interval_seconds"] == interval
                        and existing["cleanup_mode"] == mode
                        and existing["dry_run"] == bool(dry_run)
                        and existing["protected_senders"] == protected
                    ):
                        existing["reused"] = True
                        return existing
                    raise ValueError(
                        "An enabled Inbox retention policy already exists for this principal; "
                        "change or disable it instead"
                    )
                connection.execute(
                    "INSERT INTO email_policies "
                    "(policy_id,principal_id,conversation_id,kind,status,retention_days,"
                    "interval_seconds,idempotency_key,created_at,updated_at,next_run_at,"
                    "cleanup_mode,dry_run,protected_senders_json,classification_version) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        policy_id,
                        principal,
                        conversation,
                        policy_kind,
                        "active",
                        days,
                        interval,
                        key,
                        self._iso(now),
                        self._iso(now),
                        self._iso(now),
                        mode,
                        int(bool(dry_run)),
                        json.dumps(protected, separators=(",", ":")),
                        classification_version,
                    ),
                )
                created = {
                    "policy_id": policy_id,
                    "principal_id": principal,
                }
                self._audit(
                    connection,
                    created,
                    "create",
                    "persisted",
                    {
                        "retention_days": days,
                        "operation": "dry_run" if dry_run else f"gmail.{mode}",
                        "permanent_delete": False,
                        "classification_version": classification_version,
                        "protected_senders": protected,
                    },
                )
        except sqlite3.IntegrityError:
            with self._db() as connection:
                row = connection.execute(
                    "SELECT * FROM email_policies WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
            if row is None:
                raise
            existing = self._row(row)
            if (
                existing["principal_id"] != principal
                or existing["conversation_id"] != conversation
                or existing["retention_days"] != days
                or existing["interval_seconds"] != interval
                or existing["kind"] != policy_kind
                or existing["cleanup_mode"] != mode
                or existing["dry_run"] != bool(dry_run)
                or existing["protected_senders"] != protected
            ):
                raise ValueError("Retention request ID was reused for a different policy")
            existing["reused"] = True
            return existing
        persisted = await self.get(policy_id, principal_id=principal)
        if persisted is None:
            raise RuntimeError("Retention policy persistence could not be verified")
        persisted["reused"] = False
        return persisted

    async def get(self, policy_id: str, *, principal_id: str) -> dict[str, Any] | None:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM email_policies WHERE policy_id=? AND principal_id=?",
                (str(policy_id), str(principal_id)),
            ).fetchone()
        return self._row(row) if row is not None else None

    async def list(
        self,
        *,
        principal_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        values: list[Any] = [str(principal_id)]
        where = "principal_id=?"
        if status is not None:
            if status not in {"active", "paused", "disabled"}:
                raise ValueError("Unsupported email policy status")
            where += " AND status=?"
            values.append(status)
        with self._db() as connection:
            rows = connection.execute(
                f"SELECT * FROM email_policies WHERE {where} ORDER BY created_at DESC",
                values,
            ).fetchall()
        return [self._row(row) for row in rows]

    async def _change_state(
        self,
        policy_id: str,
        *,
        principal_id: str,
        operation: str,
        target: str,
    ) -> dict[str, Any] | None:
        current = await self.get(policy_id, principal_id=principal_id)
        if current is None:
            return None
        now = self._now()
        with self._db() as connection:
            connection.execute(
                "UPDATE email_policies SET status=?,updated_at=?,next_run_at=? "
                "WHERE policy_id=? AND principal_id=?",
                (
                    target,
                    self._iso(now),
                    self._iso(now) if target == "active" else current["next_run_at"],
                    policy_id,
                    principal_id,
                ),
            )
            self._audit(connection, current, operation, "verified", {"status": target})
        return await self.get(policy_id, principal_id=principal_id)

    async def pause(self, policy_id: str, *, principal_id: str) -> dict[str, Any] | None:
        return await self._change_state(
            policy_id,
            principal_id=principal_id,
            operation="pause",
            target="paused",
        )

    async def resume(self, policy_id: str, *, principal_id: str) -> dict[str, Any] | None:
        return await self._change_state(
            policy_id,
            principal_id=principal_id,
            operation="resume",
            target="active",
        )

    async def disable(self, policy_id: str, *, principal_id: str) -> dict[str, Any] | None:
        return await self._change_state(
            policy_id,
            principal_id=principal_id,
            operation="disable",
            target="disabled",
        )

    async def change(
        self,
        policy_id: str,
        *,
        principal_id: str,
        retention_days: int,
    ) -> dict[str, Any] | None:
        days = int(retention_days)
        if days < 1 or days > 3650:
            raise ValueError("Retention days must be between 1 and 3650")
        current = await self.get(policy_id, principal_id=principal_id)
        if current is None:
            return None
        now = self._now()
        with self._db() as connection:
            connection.execute(
                "UPDATE email_policies SET retention_days=?,updated_at=?,next_run_at=? "
                "WHERE policy_id=? AND principal_id=?",
                (days, self._iso(now), self._iso(now), policy_id, principal_id),
            )
            self._audit(
                connection,
                current,
                "change",
                "verified",
                {"retention_days": days},
            )
        return await self.get(policy_id, principal_id=principal_id)

    @staticmethod
    def _normalise_protected_senders(values: Sequence[str]) -> Sequence[str]:
        protected: list[str] = []
        for raw in values:
            value = str(raw or "").strip().casefold()
            if not value or len(value) > 320 or any(character.isspace() for character in value):
                raise ValueError("Protected senders must be exact addresses or domains")
            if value.startswith("@"):
                domain = value[1:]
                if (
                    not domain
                    or "." not in domain
                    or domain.startswith(".")
                    or domain.endswith(".")
                ):
                    raise ValueError("Protected sender domains must be exact")
            else:
                addresses = getaddresses([value])
                if len(addresses) != 1 or addresses[0][1].casefold() != value or "@" not in value:
                    raise ValueError("Protected sender addresses must be exact")
            if value not in protected:
                protected.append(value)
        if len(protected) > 100:
            raise ValueError("At most 100 protected senders or domains are supported")
        return protected

    @staticmethod
    def _profile_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "principal_id": str(row["principal_id"]),
            "conversation_id": str(row["conversation_id"]),
            "important_email_alerts": bool(row["important_alerts_enabled"]),
            "reply_alerts": bool(row["reply_alerts_enabled"]),
            "inbox_cleanup": bool(row["cleanup_enabled"]),
            "cleanup_dry_run": bool(row["cleanup_dry_run"]),
            "importance_threshold": str(row["importance_threshold"]),
            "cleanup_mode": str(row["cleanup_mode"]),
            "cleanup_age_days": int(row["cleanup_age_days"]),
            "protected_senders": json.loads(str(row["protected_senders_json"] or "[]")),
            "poll_interval_seconds": int(row["poll_interval_seconds"]),
            "cleanup_interval_seconds": int(row["cleanup_interval_seconds"]),
            "history_cursor_present": bool(row["history_id"]),
            "status": str(row["status"]),
            "last_gmail_check": row["last_check_at"],
            "next_gmail_check": row["next_check_at"],
            "last_cleanup": row["last_cleanup_at"],
            "next_cleanup": row["next_cleanup_at"],
            "provider_error": row["last_error"],
            "evaluated_count": int(row["evaluated_count"]),
            "important_detected_count": int(row["important_detected_count"]),
            "notified_count": int(row["notified_count"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    async def configure_assistant(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        important_email_alerts: bool | None = None,
        reply_alerts: bool | None = None,
        inbox_cleanup: bool | None = None,
        cleanup_dry_run: bool | None = None,
        importance_threshold: str | None = None,
        cleanup_mode: str | None = None,
        cleanup_age_days: int | None = None,
        protected_senders: Sequence[str] | None = None,
        poll_interval_seconds: int | None = None,
        cleanup_interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Persist one principal's coherent Email Assistant configuration."""

        principal = str(principal_id or "").strip()
        conversation = str(conversation_id or "").strip()
        if not principal or not conversation:
            raise ValueError("A principal and conversation are required")
        if conversation.startswith("usr:") and not conversation.startswith(f"usr:{principal}:"):
            raise ValueError("Email Assistant principal does not own its conversation")
        current = await self.assistant_status(principal_id=principal)
        important = (
            bool(important_email_alerts)
            if important_email_alerts is not None
            else bool(current and current["important_email_alerts"])
        )
        replies = (
            bool(reply_alerts)
            if reply_alerts is not None
            else bool(current and current["reply_alerts"])
        )
        cleanup = (
            bool(inbox_cleanup)
            if inbox_cleanup is not None
            else bool(current and current["inbox_cleanup"])
        )
        dry_run = (
            bool(cleanup_dry_run)
            if cleanup_dry_run is not None
            else (bool(current["cleanup_dry_run"]) if current else True)
        )
        threshold = str(
            importance_threshold
            if importance_threshold is not None
            else (current["importance_threshold"] if current else "important")
        ).casefold()
        if threshold not in _PRIORITY_LEVELS:
            raise ValueError("Importance threshold must be critical, important, normal or low")
        mode = str(
            cleanup_mode
            if cleanup_mode is not None
            else (current["cleanup_mode"] if current else "trash")
        ).casefold()
        if mode not in {"archive", "trash"}:
            raise ValueError("Cleanup mode must be archive or trash")
        age = int(
            cleanup_age_days
            if cleanup_age_days is not None
            else (current["cleanup_age_days"] if current else 30)
        )
        if age < 1 or age > 3650:
            raise ValueError("Cleanup age must be between 1 and 3650 days")
        protected = self._normalise_protected_senders(
            protected_senders
            if protected_senders is not None
            else (current["protected_senders"] if current else ())
        )
        poll = int(
            poll_interval_seconds
            if poll_interval_seconds is not None
            else (current["poll_interval_seconds"] if current else 300)
        )
        if poll < 60 or poll > 3600:
            raise ValueError("Email check interval must be between 60 seconds and 1 hour")
        cleanup_interval = int(
            cleanup_interval_seconds
            if cleanup_interval_seconds is not None
            else (current["cleanup_interval_seconds"] if current else 86_400)
        )
        if cleanup_interval < 3600 or cleanup_interval > 30 * 86_400:
            raise ValueError("Cleanup interval must be between 1 hour and 30 days")
        status = "active" if important or replies or cleanup else "paused"
        now = self._now()
        now_text = self._iso(now)
        with self._db() as connection:
            connection.execute(
                "INSERT INTO email_assistant_profiles "
                "(principal_id,conversation_id,important_alerts_enabled,reply_alerts_enabled,"
                "cleanup_enabled,cleanup_dry_run,importance_threshold,cleanup_mode,"
                "cleanup_age_days,protected_senders_json,poll_interval_seconds,"
                "cleanup_interval_seconds,status,next_check_at,next_cleanup_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(principal_id) DO UPDATE SET conversation_id=excluded.conversation_id,"
                "important_alerts_enabled=excluded.important_alerts_enabled,"
                "reply_alerts_enabled=excluded.reply_alerts_enabled,"
                "cleanup_enabled=excluded.cleanup_enabled,cleanup_dry_run=excluded.cleanup_dry_run,"
                "importance_threshold=excluded.importance_threshold,cleanup_mode=excluded.cleanup_mode,"
                "cleanup_age_days=excluded.cleanup_age_days,"
                "protected_senders_json=excluded.protected_senders_json,"
                "poll_interval_seconds=excluded.poll_interval_seconds,"
                "cleanup_interval_seconds=excluded.cleanup_interval_seconds,status=excluded.status,"
                "next_check_at=CASE WHEN "
                "(excluded.important_alerts_enabled=1 OR excluded.reply_alerts_enabled=1) AND "
                "(email_assistant_profiles.important_alerts_enabled=0 AND "
                "email_assistant_profiles.reply_alerts_enabled=0 OR "
                "excluded.poll_interval_seconds!=email_assistant_profiles.poll_interval_seconds) "
                "THEN excluded.next_check_at "
                "ELSE email_assistant_profiles.next_check_at END,"
                "next_cleanup_at=CASE WHEN excluded.cleanup_enabled=1 AND "
                "(email_assistant_profiles.cleanup_enabled=0 OR "
                "excluded.cleanup_dry_run!=email_assistant_profiles.cleanup_dry_run OR "
                "excluded.cleanup_mode!=email_assistant_profiles.cleanup_mode OR "
                "excluded.cleanup_age_days!=email_assistant_profiles.cleanup_age_days OR "
                "excluded.protected_senders_json!=email_assistant_profiles.protected_senders_json OR "
                "excluded.cleanup_interval_seconds!="
                "email_assistant_profiles.cleanup_interval_seconds) THEN excluded.next_cleanup_at "
                "ELSE email_assistant_profiles.next_cleanup_at END,updated_at=excluded.updated_at",
                (
                    principal,
                    conversation,
                    int(important),
                    int(replies),
                    int(cleanup),
                    int(dry_run),
                    threshold,
                    mode,
                    age,
                    json.dumps(protected, separators=(",", ":")),
                    poll,
                    cleanup_interval,
                    status,
                    now_text,
                    now_text,
                    now_text,
                    now_text,
                ),
            )
        await self._sync_safe_cleanup_policy(principal)
        persisted = await self.assistant_status(principal_id=principal)
        if persisted is None:
            raise RuntimeError("Email Assistant settings were not persisted")
        return persisted

    async def assistant_status(self, *, principal_id: str) -> dict[str, Any] | None:
        principal = str(principal_id or "").strip()
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM email_assistant_profiles WHERE principal_id=?",
                (principal,),
            ).fetchone()
            watch_count = connection.execute(
                "SELECT COUNT(*) FROM email_reply_watches WHERE principal_id=? AND status='active'",
                (principal,),
            ).fetchone()
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM email_assistant_events WHERE principal_id=? "
                "AND status IN ('pending','delivering')",
                (principal,),
            ).fetchone()
        if row is None:
            return None
        result = self._profile_row(row)
        result["active_reply_watches"] = int(watch_count[0]) if watch_count else 0
        result["pending_notifications"] = int(pending_count[0]) if pending_count else 0
        return result

    async def list_reply_watches(self, *, principal_id: str) -> Sequence[dict[str, Any]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT thread_id,recipient,display_name,source,status,created_at,updated_at "
                "FROM email_reply_watches WHERE principal_id=? ORDER BY updated_at DESC",
                (str(principal_id),),
            ).fetchall()
        return [
            {
                "thread_id": str(row["thread_id"]),
                "recipient": row["recipient"],
                "display_name": row["display_name"],
                "source": str(row["source"]),
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    async def set_reply_watch_status(
        self, *, principal_id: str, thread_id: str, status: str
    ) -> bool:
        target = str(status or "").casefold()
        if target not in {"active", "paused", "cancelled"}:
            raise ValueError("Reply watch status must be active, paused or cancelled")
        with self._db() as connection:
            changed = connection.execute(
                "UPDATE email_reply_watches SET status=?,updated_at=? "
                "WHERE principal_id=? AND thread_id=?",
                (target, self._iso(self._now()), str(principal_id), str(thread_id)),
            ).rowcount
        return bool(changed)

    async def watch_reply(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        thread_id: str,
        sent_message_id: str,
        recipient: str | None = None,
        display_name: str | None = None,
        source: str = "explicit_user_monitor",
        poll_interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        if conversation_id.startswith("usr:") and not conversation_id.startswith(
            f"usr:{principal_id}:"
        ):
            raise ValueError("Reply watch principal does not own its conversation")
        created = self._upsert_watch(
            principal_id=str(principal_id),
            thread_id=str(thread_id),
            anchor_message_id=str(sent_message_id),
            anchor_epoch_ms=None,
            conversation_id=str(conversation_id),
            recipient=self._address(recipient) or None,
            display_name=str(display_name or "").strip() or None,
            source=source,
        )
        if not created:
            raise ValueError("Exact Gmail thread and sent-message evidence are required")
        await self.set_reply_watch_status(
            principal_id=principal_id,
            thread_id=thread_id,
            status="active",
        )
        await self.configure_assistant(
            principal_id=principal_id,
            conversation_id=conversation_id,
            reply_alerts=True,
            poll_interval_seconds=poll_interval_seconds,
        )
        return {
            "success": True,
            "status": "active",
            "thread_id": thread_id,
            "recipient": self._address(recipient) or None,
            "durable": True,
        }

    async def cleanup_history(
        self, *, principal_id: str, since: datetime | None = None
    ) -> dict[str, Any]:
        values: list[Any] = [str(principal_id)]
        since_clause = ""
        if since is not None:
            since_clause = " AND a.created_at>=?"
            values.append(self._iso(since.astimezone(timezone.utc)))
        with self._db() as connection:
            rows = connection.execute(
                "SELECT a.operation,a.state,a.evidence_json,a.created_at "
                "FROM email_policy_audit a JOIN email_policies p ON p.policy_id=a.policy_id "
                "WHERE a.principal_id=? AND p.kind='safe_cleanup'"
                + since_clause
                + " ORDER BY a.audit_id DESC LIMIT 100",
                values,
            ).fetchall()
        totals = {"trashed": 0, "archived": 0, "dry_run_candidates": 0}
        entries: list[dict[str, Any]] = []
        for row in rows:
            evidence = json.loads(str(row["evidence_json"] or "{}"))
            if str(row["operation"]) == "run":
                for key in totals:
                    totals[key] += int(evidence.get(key) or 0)
            entries.append(
                {
                    "operation": str(row["operation"]),
                    "state": str(row["state"]),
                    "evidence": evidence,
                    "created_at": str(row["created_at"]),
                }
            )
        return {"totals": totals, "entries": entries}

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        candidate = str(value or "").strip()
        if not candidate:
            return None
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _history_item(row: Mapping[str, Any]) -> dict[str, Any]:
        """Return one safe cleanup-history record, including legacy fallbacks."""

        try:
            evidence = json.loads(str(row.get("evidence_json") or "{}"))
        except json.JSONDecodeError:
            evidence = {}
        if not isinstance(evidence, dict):
            evidence = {}
        metadata = evidence.get("message_metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        classification = evidence.get("classification")
        classification = dict(classification) if isinstance(classification, Mapping) else {}
        verification = evidence.get("verification")
        verification = dict(verification) if isinstance(verification, Mapping) else {}
        operation = str(evidence.get("cleanup_mode") or row.get("cleanup_mode") or "trash")
        action_at = (
            EmailAssistantPolicyEngine._parse_timestamp(evidence.get("cleanup_at"))
            or EmailAssistantPolicyEngine._parse_timestamp(verification.get("verified_at"))
            or EmailAssistantPolicyEngine._parse_timestamp(row.get("updated_at"))
        )
        sender_address = str(
            metadata.get("sender_address") or classification.get("sender") or ""
        ).strip()
        sender_name = str(metadata.get("sender_display_name") or "").strip()
        subject = str(metadata.get("subject") or "").strip()
        thread_id = str(metadata.get("thread_id") or "").strip()
        previous_labels = metadata.get("previous_labels")
        if not isinstance(previous_labels, list):
            previous_labels = evidence.get("provider_labels")
        return {
            "message_id": str(row.get("message_id") or ""),
            "thread_id": thread_id or None,
            "sender_display_name": sender_name or None,
            "sender_address": sender_address or None,
            "subject": subject or None,
            "previous_labels": [str(item) for item in previous_labels or ()],
            "operation": operation,
            "classification": str(classification.get("category") or "").strip() or None,
            "eligibility_reason": str(evidence.get("eligibility") or "").strip() or None,
            "policy_id": str(row.get("policy_id") or ""),
            "policy_version": str(evidence.get("policy_version") or "legacy").strip(),
            "action_id": str(
                evidence.get("action_receipt_id")
                or evidence.get("cleanup_action_id")
                or row.get("action_id")
                or ""
            ).strip()
            or None,
            "provider_reference": str(
                evidence.get("cleanup_provider_reference") or row.get("provider_reference") or ""
            ).strip()
            or None,
            "verification": verification,
            "verified": bool(
                str(row.get("status") or "") in {"verified", "verified_after_unknown", "restored"}
                and (verification or evidence.get("reconciliation"))
            ),
            "restored": str(row.get("status") or "") == "restored"
            or bool(evidence.get("undone_at")),
            "action_at": EmailAssistantPolicyEngine._iso(action_at) if action_at else None,
            "cleanup_run_at": str(evidence.get("cleanup_run_at") or "").strip() or None,
            "metadata_complete": bool(sender_address and subject and thread_id),
        }

    async def cleanup_history_items(
        self,
        *,
        principal_id: str,
        since: datetime | None = None,
        operation: str | None = None,
        offset: int = 0,
        limit: int = 10,
        latest_run: bool = False,
    ) -> dict[str, Any]:
        """Page through verified, principal-owned cleanup mutations."""

        principal = str(principal_id or "").strip()
        selected_operation = str(operation or "").casefold().strip() or None
        if selected_operation not in {None, "trash", "archive"}:
            raise ValueError("Cleanup history operation must be trash or archive")
        start = max(0, int(offset))
        page_size = max(1, min(int(limit), 25))
        with self._db() as connection:
            rows = connection.execute(
                "SELECT i.*,p.principal_id,p.cleanup_mode,p.classification_version "
                "FROM email_policy_items i JOIN email_policies p ON p.policy_id=i.policy_id "
                "WHERE p.principal_id=? AND p.kind='safe_cleanup' "
                "AND i.status IN ('verified','verified_after_unknown','restored') "
                "ORDER BY i.updated_at DESC LIMIT 1000",
                (principal,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for raw in rows:
            item = self._history_item(dict(raw))
            action_at = self._parse_timestamp(item.get("action_at"))
            if since is not None and (
                action_at is None or action_at < since.astimezone(timezone.utc)
            ):
                continue
            if selected_operation and item["operation"] != selected_operation:
                continue
            items.append(item)
        items.sort(
            key=lambda item: (
                self._parse_timestamp(item.get("action_at"))
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )
        if latest_run and items:
            run_times = [self._parse_timestamp(item.get("cleanup_run_at")) for item in items]
            newest_run = max((item for item in run_times if item is not None), default=None)
            if newest_run is not None:
                items = [
                    item
                    for item in items
                    if self._parse_timestamp(item.get("cleanup_run_at")) == newest_run
                ]
            else:
                newest_action = self._parse_timestamp(items[0].get("action_at"))
                if newest_action is not None:
                    oldest = newest_action - timedelta(minutes=15)
                    items = [
                        item
                        for item in items
                        if (self._parse_timestamp(item.get("action_at")) or oldest) >= oldest
                    ]
        page = items[start : start + page_size]
        totals = {
            "trashed": sum(1 for item in items if item["operation"] == "trash"),
            "archived": sum(1 for item in items if item["operation"] == "archive"),
        }
        return {
            "total": len(items),
            "totals": totals,
            "items": page,
            "offset": start,
            "limit": page_size,
            "has_more": start + len(page) < len(items),
            "next_offset": start + len(page),
            "operation": selected_operation,
            "latest_run": bool(latest_run),
        }

    async def verify_cleanup_items_state(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        message_ids: Sequence[str],
        expected_operation: str,
    ) -> dict[str, Any]:
        """Verify current Gmail state for exact principal-owned cleanup records."""

        principal = str(principal_id or "").strip()
        requested = [str(item).strip() for item in message_ids if str(item).strip()][:25]
        if not requested:
            return {"checked": 0, "matching": 0, "unavailable": 0, "states": []}
        placeholders = ",".join("?" for _ in requested)
        with self._db() as connection:
            owned = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT i.message_id FROM email_policy_items i "
                    "JOIN email_policies p ON p.policy_id=i.policy_id "
                    f"WHERE p.principal_id=? AND p.kind='safe_cleanup' AND i.message_id IN ({placeholders}) "
                    "AND i.status IN ('verified','verified_after_unknown','restored')",
                    (principal, *requested),
                ).fetchall()
            }
        policy = {"principal_id": principal, "conversation_id": conversation_id}
        states: list[dict[str, Any]] = []
        for message_id in requested:
            if message_id not in owned:
                continue
            current = await self._read_message(policy=policy, message_id=message_id)
            if current is None:
                states.append({"message_id": message_id, "available": False, "matches": False})
                continue
            labels = {str(item) for item in current.get("label_ids") or ()}
            matches = (
                "TRASH" in labels
                if expected_operation == "trash"
                else "INBOX" not in labels and "TRASH" not in labels
            )
            states.append({"message_id": message_id, "available": True, "matches": matches})
        return {
            "checked": len(states),
            "matching": sum(1 for item in states if item["matches"]),
            "unavailable": sum(1 for item in states if not item["available"]),
            "states": states,
        }

    async def send_cleanup_history_notification(
        self, *, principal_id: str, text: str
    ) -> dict[str, Any]:
        """Send an explicitly requested cleanup list to the owner's configured phone."""

        if self.notifier is None:
            return {"success": False, "command_accepted": False, "error": "unavailable"}
        message = str(text or "").strip()
        if not message:
            return {"success": False, "command_accepted": False, "error": "empty"}
        result = await self.notifier(
            str(principal_id),
            message[:1000],
            "Jarvis Email Cleanup",
        )
        return dict(result)

    async def protect_message(
        self, *, principal_id: str, message_id: str, thread_id: str | None = None
    ) -> bool:
        principal = str(principal_id or "").strip()
        message = str(message_id or "").strip()
        if not principal or not message:
            return False
        with self._db() as connection:
            connection.execute(
                "INSERT INTO email_cleanup_protections "
                "(principal_id,message_id,thread_id,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(principal_id,message_id) DO UPDATE SET "
                "thread_id=excluded.thread_id",
                (
                    principal,
                    message,
                    str(thread_id or "").strip() or None,
                    self._iso(self._now()),
                ),
            )
        return True

    async def preview_cleanup(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        now: datetime | None = None,
        cleanup_mode: str | None = None,
        cleanup_age_days: int | None = None,
    ) -> dict[str, Any]:
        profile = await self.assistant_status(principal_id=principal_id)
        if profile is None:
            await self.configure_assistant(
                principal_id=principal_id,
                conversation_id=conversation_id,
            )
        await self._sync_safe_cleanup_policy(principal_id)
        with self._db() as connection:
            row = connection.execute(
                "SELECT policy_id FROM email_policies WHERE principal_id=? "
                "AND kind='safe_cleanup' ORDER BY created_at LIMIT 1",
                (str(principal_id),),
            ).fetchone()
        if row is None:
            raise RuntimeError("Email cleanup policy could not be prepared")
        return await self.run_policy(
            str(row["policy_id"]),
            now=now,
            force_dry_run=True,
            allow_paused=True,
            preview_cleanup_mode=cleanup_mode,
            preview_retention_days=cleanup_age_days,
        )

    async def undo_last_cleanup(
        self, *, principal_id: str, conversation_id: str, request_id: str
    ) -> dict[str, Any]:
        """Restore the last verified Trash cleanup using its exact durable ID."""

        principal = str(principal_id or "").strip()
        with self._db() as connection:
            rows = connection.execute(
                "SELECT i.*,p.cleanup_mode,p.conversation_id,p.principal_id "
                "FROM email_policy_items i JOIN email_policies p ON p.policy_id=i.policy_id "
                "WHERE p.principal_id=? AND p.kind='safe_cleanup' AND p.cleanup_mode='trash' "
                "AND i.status IN ('verified','verified_after_unknown') "
                "ORDER BY i.updated_at DESC LIMIT 20",
                (principal,),
            ).fetchall()
        for raw in rows:
            row = dict(raw)
            evidence = json.loads(str(row.get("evidence_json") or "{}"))
            if evidence.get("undone_at"):
                continue
            policy = {
                "principal_id": principal,
                "conversation_id": conversation_id,
            }
            current = await self._read_message(policy=policy, message_id=str(row["message_id"]))
            if current is None:
                continue
            labels = {str(item) for item in current.get("label_ids") or ()}
            if "TRASH" not in labels:
                continue
            key = f"email-cleanup-undo:{row['eligibility_key']}"
            action = await self.registry.execute(
                CapabilityRequest(
                    capability_id="gmail.restore",
                    payload={"message_id": str(row["message_id"])},
                    request_id=request_id,
                    conversation_id=conversation_id,
                    principal_id=principal,
                    operation="undo_last_email_cleanup",
                    target=str(row["message_id"]),
                    confirmed=True,
                    idempotency_key=key,
                ),
                refresh_health=True,
            )
            evidence["undo_status"] = action.status.value
            evidence["undo_verification"] = dict(action.verification)
            if action.status is ExecutionStatus.VERIFIED:
                evidence["undone_at"] = self._iso(self._now())
                self._record_item(
                    policy_id=str(row["policy_id"]),
                    message_id=str(row["message_id"]),
                    eligibility_key=str(row["eligibility_key"]),
                    status="restored",
                    attempts=int(row["attempts"]),
                    action_id=(action.receipt.action_id if action.receipt else row["action_id"]),
                    provider_reference=action.provider_reference,
                    evidence=evidence,
                )
                with self._db() as connection:
                    self._audit(
                        connection,
                        {
                            "policy_id": str(row["policy_id"]),
                            "principal_id": principal,
                        },
                        "undo",
                        "verified",
                        {
                            "message_id": str(row["message_id"]),
                            "provider_reference": action.provider_reference,
                            "permanent_delete": False,
                        },
                    )
                return {"success": True, "restored": 1}
            return {
                "success": False,
                "restored": 0,
                "error": redact_text(action.error or "Restore could not be verified"),
            }
        return {"success": True, "restored": 0}

    async def restore_cleanup_item(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        message_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Restore one exact, principal-owned, verified Trash cleanup item."""

        principal = str(principal_id or "").strip()
        selected = str(message_id or "").strip()
        if not principal or not selected:
            return {"success": False, "restored": 0, "reason": "missing_target"}
        with self._db() as connection:
            raws = connection.execute(
                "SELECT i.*,p.cleanup_mode,p.conversation_id,p.principal_id "
                "FROM email_policy_items i JOIN email_policies p ON p.policy_id=i.policy_id "
                "WHERE p.principal_id=? AND p.kind='safe_cleanup' AND i.message_id=? "
                "AND i.status IN "
                "('verified','verified_after_unknown','restored') "
                "ORDER BY i.updated_at DESC LIMIT 50",
                (principal, selected),
            ).fetchall()
        raw = next(
            (
                candidate
                for candidate in raws
                if self._history_item(dict(candidate))["operation"] == "trash"
            ),
            None,
        )
        if raw is None:
            return {"success": False, "restored": 0, "reason": "not_verified_cleanup"}
        row = dict(raw)
        try:
            evidence = json.loads(str(row.get("evidence_json") or "{}"))
        except json.JSONDecodeError:
            evidence = {}
        if not isinstance(evidence, dict):
            evidence = {}
        if evidence.get("undone_at") or str(row.get("status")) == "restored":
            return {"success": True, "restored": 0, "reason": "already_restored"}
        policy = {"principal_id": principal, "conversation_id": conversation_id}
        current = await self._read_message(policy=policy, message_id=selected)
        if current is None:
            return {"success": False, "restored": 0, "reason": "state_unavailable"}
        labels = {str(item) for item in current.get("label_ids") or ()}
        if "TRASH" not in labels:
            return {"success": True, "restored": 0, "reason": "not_in_trash"}
        key = f"email-cleanup-undo:{row['eligibility_key']}"
        action = await self.registry.execute(
            CapabilityRequest(
                capability_id="gmail.restore",
                payload={"message_id": selected},
                request_id=request_id,
                conversation_id=conversation_id,
                principal_id=principal,
                operation="restore_selected_email_cleanup",
                target=selected,
                confirmed=True,
                idempotency_key=key,
            ),
            refresh_health=True,
        )
        evidence["undo_status"] = action.status.value
        evidence["undo_verification"] = dict(action.verification)
        if action.status is not ExecutionStatus.VERIFIED:
            return {
                "success": False,
                "restored": 0,
                "reason": "restore_not_verified",
                "error": redact_text(action.error or "Restore could not be verified"),
            }
        evidence["undone_at"] = self._iso(self._now())
        evidence.setdefault("cleanup_action_id", row.get("action_id"))
        evidence.setdefault("cleanup_provider_reference", row.get("provider_reference"))
        evidence["restore_action_id"] = action.receipt.action_id if action.receipt else None
        evidence["restore_provider_reference"] = action.provider_reference
        self._record_item(
            policy_id=str(row["policy_id"]),
            message_id=selected,
            eligibility_key=str(row["eligibility_key"]),
            status="restored",
            attempts=int(row["attempts"]),
            action_id=(action.receipt.action_id if action.receipt else row.get("action_id")),
            provider_reference=action.provider_reference,
            evidence=evidence,
        )
        with self._db() as connection:
            self._audit(
                connection,
                {"policy_id": str(row["policy_id"]), "principal_id": principal},
                "undo",
                "verified",
                {
                    "message_id": selected,
                    "provider_reference": action.provider_reference,
                    "permanent_delete": False,
                },
            )
        return {
            "success": True,
            "restored": 1,
            "reason": "verified",
            "item": self._history_item(
                {**row, "status": "restored", "evidence_json": json.dumps(evidence)}
            ),
        }

    async def _sync_safe_cleanup_policy(self, principal_id: str) -> None:
        profile = await self.assistant_status(principal_id=principal_id)
        if profile is None:
            return
        now = self._iso(self._now())
        policy_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jarvis:email-cleanup:{principal_id}"))
        key = f"email-assistant-cleanup:{principal_id}"
        with self._db() as connection:
            connection.execute(
                "INSERT INTO email_policies "
                "(policy_id,principal_id,conversation_id,kind,status,retention_days,"
                "interval_seconds,idempotency_key,created_at,updated_at,next_run_at,"
                "cleanup_mode,dry_run,protected_senders_json,classification_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(policy_id) DO UPDATE SET conversation_id=excluded.conversation_id,"
                "status=excluded.status,retention_days=excluded.retention_days,"
                "interval_seconds=excluded.interval_seconds,updated_at=excluded.updated_at,"
                "cleanup_mode=excluded.cleanup_mode,dry_run=excluded.dry_run,"
                "protected_senders_json=excluded.protected_senders_json,"
                "classification_version=excluded.classification_version,"
                "next_run_at=CASE WHEN excluded.status='active' AND "
                "(email_policies.status!='active' OR "
                "excluded.retention_days!=email_policies.retention_days OR "
                "excluded.interval_seconds!=email_policies.interval_seconds OR "
                "excluded.cleanup_mode!=email_policies.cleanup_mode OR "
                "excluded.dry_run!=email_policies.dry_run OR "
                "excluded.protected_senders_json!=email_policies.protected_senders_json) "
                "THEN excluded.next_run_at "
                "ELSE email_policies.next_run_at END",
                (
                    policy_id,
                    principal_id,
                    profile["conversation_id"],
                    "safe_cleanup",
                    "active" if profile["inbox_cleanup"] else "paused",
                    profile["cleanup_age_days"],
                    profile["cleanup_interval_seconds"],
                    key,
                    now,
                    now,
                    now,
                    profile["cleanup_mode"],
                    int(profile["cleanup_dry_run"]),
                    json.dumps(profile["protected_senders"], separators=(",", ":")),
                    "safe-low-value-v1",
                ),
            )
            self._audit(
                connection,
                {"policy_id": policy_id, "principal_id": principal_id},
                "configure",
                "persisted",
                {
                    "status": "active" if profile["inbox_cleanup"] else "paused",
                    "cleanup_mode": profile["cleanup_mode"],
                    "dry_run": profile["cleanup_dry_run"],
                    "retention_days": profile["cleanup_age_days"],
                    "protected_senders": profile["protected_senders"],
                    "permanent_delete": False,
                },
            )

    async def run_due(self, *, now: datetime | None = None) -> Sequence[dict[str, Any]]:
        current = (now or self._now()).astimezone(timezone.utc)
        results: list[dict[str, Any]] = []
        with self._db() as connection:
            profiles = connection.execute(
                "SELECT principal_id FROM email_assistant_profiles "
                "WHERE status='active' AND "
                "(important_alerts_enabled=1 OR reply_alerts_enabled=1) "
                "AND next_check_at<=? ORDER BY next_check_at LIMIT 20",
                (self._iso(current),),
            ).fetchall()
        for profile in profiles:
            principal_id = str(profile["principal_id"])
            try:
                results.append(await self.run_assistant(principal_id, now=current))
            except Exception as exc:
                logger.exception("Email Assistant pass failed principal=%s", principal_id)
                results.append(
                    {
                        "service": "email_assistant",
                        "principal_id": principal_id,
                        "status": "failed",
                        "error": redact_text(exc, max_length=300),
                    }
                )
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM email_policies WHERE status='active' AND next_run_at<=? "
                "ORDER BY next_run_at LIMIT 20",
                (self._iso(current),),
            ).fetchall()
        for row in rows:
            policy_id = str(row["policy_id"])
            try:
                results.append(await self.run_policy(policy_id, now=current))
            except Exception as exc:
                logger.exception("Email cleanup pass failed policy=%s", policy_id)
                results.append(
                    {
                        "service": "email_cleanup",
                        "policy_id": policy_id,
                        "status": "failed",
                        "error": redact_text(exc, max_length=300),
                    }
                )
        return results

    async def _execute_read(
        self,
        profile: Mapping[str, Any],
        capability_id: str,
        payload: Mapping[str, Any],
        *,
        operation: str,
        target: str | None = None,
    ) -> Any:
        return await self.registry.execute(
            CapabilityRequest(
                capability_id=capability_id,
                payload=dict(payload),
                request_id=str(uuid.uuid4()),
                conversation_id=str(profile["conversation_id"]),
                principal_id=str(profile["principal_id"]),
                operation=operation,
                target=target,
            ),
            refresh_health=True,
        )

    @staticmethod
    def _address(value: Any) -> str:
        addresses = getaddresses([str(value or "")[:1000]])
        if len(addresses) != 1:
            return ""
        address = addresses[0][1].strip().casefold()
        local, separator, domain = address.rpartition("@")
        if (
            not separator
            or not local
            or not domain
            or any(character.isspace() for character in address)
        ):
            return ""
        return address

    @staticmethod
    def _sender_name(message: Mapping[str, Any]) -> str:
        display, address = parseaddr(str(message.get("from") or "")[:1000])
        if display.strip():
            return display.strip().split()[0]
        local = address.split("@", 1)[0]
        first = local.replace(".", " ").replace("_", " ").strip().split()
        return first[0].title() if first else "Someone"

    async def _known_contact(
        self, profile: Mapping[str, Any], message: Mapping[str, Any]
    ) -> bool | None:
        sender = self._address(message.get("from"))
        if not sender:
            return False
        execution = await self._execute_read(
            profile,
            "contacts.search",
            {"query": sender, "limit": 5},
            operation="email_assistant_contact_check",
            target=sender,
        )
        if not execution.success:
            return None
        for contact in execution.data.get("contacts") or ():
            if not isinstance(contact, Mapping):
                continue
            addresses = {self._address(item) for item in contact.get("email_addresses") or ()}
            if sender in addresses:
                return True
        return False

    @staticmethod
    def classify_message(
        message: Mapping[str, Any],
        *,
        owner_email: str = "",
        known_contact: bool = False,
        watched_reply: bool = False,
    ) -> dict[str, Any]:
        """Classify untrusted mail with bounded, explainable rules only."""

        labels = {str(item) for item in message.get("label_ids") or ()}
        subject = " ".join(str(message.get("subject") or "").casefold().split())[:1000]
        raw_body = str(message.get("body") or message.get("snippet") or "")[:100_000]
        body = " ".join(clean_email_reply_body(raw_body).casefold().split())[:4000]
        text = f" {subject} {body} "
        sender = EmailAssistantPolicyEngine._address(message.get("from"))
        recipient_headers = [
            value
            for header in ("to", "cc", "delivered_to")
            if (value := str(message.get(header) or "")[:5_000].strip())
        ]
        recipients = {
            EmailAssistantPolicyEngine._address(address)
            for _, address in getaddresses(recipient_headers)
        }
        owner = EmailAssistantPolicyEngine._address(owner_email)
        direct = bool(owner and owner in recipients)
        trusted_bulk = bool(
            labels & _LOW_VALUE_LABELS
            or message.get("list_unsubscribe")
            or str(message.get("precedence") or "").casefold() in {"bulk", "list", "junk"}
        )
        content_bulk = " unsubscribe " in text or " newsletter " in text
        bulk = trusted_bulk
        reasons: list[dict[str, Any]] = []
        score = 0

        def add(points: int, signal: str, source: str) -> None:
            nonlocal score
            score += points
            reasons.append({"signal": signal, "weight": points, "source": source})

        if "IMPORTANT" in labels:
            add(55, "gmail_important", "gmail_label")
        if "STARRED" in labels:
            add(45, "gmail_starred", "gmail_label")
        if "UNREAD" in labels:
            add(10, "unread", "gmail_label")
        if direct:
            add(10, "addressed_directly", "verified_recipient_header")
        if known_contact:
            add(45, "known_contact", "google_contacts")
        if watched_reply:
            add(80, "reply_to_watched_thread", "gmail_thread")

        categories: tuple[tuple[str, tuple[str, ...], int], ...] = (
            (
                "account/security",
                (
                    "security alert",
                    "account locked",
                    "unusual sign-in",
                    "password reset",
                    "verification code",
                    "two-factor",
                    "2fa",
                ),
                65,
            ),
            (
                "finance/bill/receipt",
                (
                    "payment due",
                    "invoice",
                    "bill due",
                    "overdue",
                    "payment failed",
                    "receipt",
                    "bank statement",
                ),
                45,
            ),
            (
                "appointment/calendar",
                ("appointment changed", "appointment cancelled", "meeting moved"),
                45,
            ),
            (
                "travel/booking",
                ("flight cancelled", "booking changed", "reservation cancelled"),
                60,
            ),
            (
                "legal/government",
                ("court", "hmrc", "government", "legal notice"),
                45,
            ),
            ("medical", ("hospital", "medical", "doctor", "prescription"), 45),
            (
                "delivery/order",
                ("order confirmation", "delivery", "tracking number", "dispatched"),
                35,
            ),
            (
                "work/action",
                (
                    "action required",
                    "please confirm",
                    "respond by",
                    "deadline",
                    "can you",
                    "could you",
                    "please reply",
                ),
                45,
            ),
        )
        category = "reply received" if watched_reply else "other"
        if not watched_reply:
            for candidate, markers, points in categories:
                marker = next((item for item in markers if item in text), None)
                if marker:
                    category = candidate
                    # Content is evidence, never authority.  Require a separate
                    # trusted signal before content can trigger an interruption.
                    if direct or known_contact or "IMPORTANT" in labels or "STARRED" in labels:
                        add(points, marker, "bounded_content_classification")
                    else:
                        reasons.append(
                            {
                                "signal": marker,
                                "weight": 0,
                                "source": "untrusted_content_unconfirmed",
                            }
                        )
                    break
        if category == "other" and "CATEGORY_PERSONAL" in labels:
            category = "personal"
            add(20, "gmail_personal", "gmail_label")
        if bulk:
            add(-20 if known_contact else -70, "bulk_or_marketing", "gmail_label_or_headers")
            if category == "other":
                category = "newsletter/low priority"

        score = max(0, min(score, 100))
        critical = category in {"account/security", "travel/booking"} and score >= 80
        if critical:
            level = "critical"
        elif score >= 60:
            level = "important"
        elif bulk:
            level = "low"
        else:
            level = "normal"
        return {
            "level": level,
            "score": score,
            "category": category,
            "reasons": reasons,
            "provider_labels": sorted(labels),
            "sender": sender or None,
            "known_contact": known_contact,
            "addressed_directly": direct,
            "watched_reply": watched_reply,
            "bulk_signal": bulk,
            "untrusted_content_bulk_signal": content_bulk,
            "method": "bounded-email-assistant-v1",
            "provider_truth": False,
            "content_is_authority": False,
        }

    @classmethod
    def _notification_text(
        cls, message: Mapping[str, Any], classification: Mapping[str, Any]
    ) -> str:
        sender = cls._sender_name(message)
        body = clean_email_reply_body(
            str(message.get("body") or message.get("snippet") or "")[:100_000]
        )
        compact = " ".join(body.split())[:280].strip()
        subject = " ".join(str(message.get("subject") or "").split())[:160].strip()
        category = str(classification.get("category") or "other")
        if classification.get("watched_reply") is True:
            if compact:
                return f"{sender} replied — {compact}"
            return f"{sender} replied, but there wasn’t any message text."
        if category == "account/security":
            detail = subject or compact or "There’s a new account security warning."
            return f"There’s a security alert from {sender}: {detail}"
        if category == "finance/bill/receipt":
            return f"{sender} sent something about a payment or bill: {subject or compact}"
        if category in {"appointment/calendar", "travel/booking"}:
            return f"{sender} sent an important update: {subject or compact}"
        if category == "work/action":
            return f"There’s an important email from {sender}: {subject or compact}"
        if compact:
            return f"There’s an important email from {sender}: {compact}"
        return (
            f"There’s an important email from {sender}: {subject or 'it may need your attention.'}"
        )

    def _upsert_watch(
        self,
        *,
        principal_id: str,
        thread_id: str,
        anchor_message_id: str,
        anchor_epoch_ms: int | None,
        conversation_id: str,
        recipient: str | None,
        display_name: str | None,
        source: str,
    ) -> bool:
        if not principal_id or not thread_id or not anchor_message_id:
            return False
        now = self._iso(self._now())
        with self._db() as connection:
            connection.execute(
                "INSERT INTO email_reply_watches "
                "(principal_id,thread_id,anchor_message_id,anchor_epoch_ms,conversation_id,"
                "recipient,display_name,source,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(principal_id,thread_id) DO UPDATE SET "
                "anchor_message_id=CASE WHEN excluded.anchor_epoch_ms IS NULL OR "
                "email_reply_watches.anchor_epoch_ms IS NULL OR "
                "excluded.anchor_epoch_ms>=email_reply_watches.anchor_epoch_ms "
                "THEN excluded.anchor_message_id ELSE email_reply_watches.anchor_message_id END,"
                "anchor_epoch_ms=CASE WHEN excluded.anchor_epoch_ms IS NULL THEN "
                "email_reply_watches.anchor_epoch_ms WHEN email_reply_watches.anchor_epoch_ms IS NULL "
                "OR excluded.anchor_epoch_ms>=email_reply_watches.anchor_epoch_ms "
                "THEN excluded.anchor_epoch_ms ELSE email_reply_watches.anchor_epoch_ms END,"
                "conversation_id=excluded.conversation_id,"
                "recipient=COALESCE(excluded.recipient,email_reply_watches.recipient),"
                "display_name=COALESCE(excluded.display_name,email_reply_watches.display_name),"
                "status=CASE WHEN email_reply_watches.anchor_message_id=excluded.anchor_message_id "
                "THEN email_reply_watches.status WHEN excluded.anchor_epoch_ms IS NOT NULL AND "
                "(email_reply_watches.anchor_epoch_ms IS NULL OR "
                "excluded.anchor_epoch_ms>=email_reply_watches.anchor_epoch_ms) "
                "THEN 'active' ELSE email_reply_watches.status END,"
                "updated_at=excluded.updated_at",
                (
                    principal_id,
                    thread_id,
                    anchor_message_id,
                    anchor_epoch_ms,
                    conversation_id,
                    recipient,
                    display_name,
                    source,
                    "active",
                    now,
                    now,
                ),
            )
        return True

    async def _sync_verified_send_watches(self, profile: Mapping[str, Any]) -> int:
        receipt_store = getattr(self.registry, "receipt_store", None)
        if receipt_store is None:
            return 0
        receipts = await receipt_store.list_recent(limit=500)
        principal = str(profile["principal_id"])
        owner_prefix = f"usr:{principal}:"
        added = 0
        for receipt in receipts:
            if (
                receipt.capability_id != "gmail.send"
                or receipt.status is not ReceiptStatus.VERIFIED
                or not str(receipt.conversation_id or "").startswith(owner_prefix)
            ):
                continue
            result = receipt.result
            message_id = str(result.get("message_id") or receipt.provider_reference or "").strip()
            thread_id = str(result.get("thread_id") or "").strip()
            recipient = self._address(result.get("recipient")) or None
            try:
                completed = datetime.fromisoformat(
                    str(receipt.completed_at or receipt.started_at).replace("Z", "+00:00")
                )
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                anchor_epoch_ms = int(completed.timestamp() * 1000)
            except (TypeError, ValueError):
                anchor_epoch_ms = None
            if self._upsert_watch(
                principal_id=principal,
                thread_id=thread_id,
                anchor_message_id=message_id,
                anchor_epoch_ms=anchor_epoch_ms,
                conversation_id=str(receipt.conversation_id),
                recipient=recipient,
                display_name=None,
                source="verified_gmail_send_receipt",
            ):
                added += 1
        return added

    async def _bootstrap_sent_watches(self, profile: Mapping[str, Any]) -> int:
        execution = await self._execute_read(
            profile,
            "gmail.search",
            {"query": "in:sent newer_than:30d", "limit": 100},
            operation="email_assistant_bootstrap_sent_threads",
        )
        if not execution.success:
            return 0
        added = 0
        summaries = {
            str(item.get("message_id") or ""): dict(item)
            for item in execution.data.get("messages") or ()
            if isinstance(item, Mapping) and item.get("message_id")
        }
        ordered_ids = [
            str(item) for item in execution.data.get("message_ids") or () if str(item).strip()
        ][:100]
        for message_id in ordered_ids:
            message = summaries.get(message_id)
            if message is None:
                read = await self._execute_read(
                    profile,
                    "gmail.read",
                    {"message_id": message_id},
                    operation="email_assistant_bootstrap_sent_thread",
                    target=message_id,
                )
                if not read.success:
                    continue
                message = dict(read.data)
            if not isinstance(message, Mapping):
                continue
            labels = {str(item) for item in message.get("label_ids") or ()}
            if "SENT" not in labels:
                continue
            if self._upsert_watch(
                principal_id=str(profile["principal_id"]),
                thread_id=str(message.get("thread_id") or ""),
                anchor_message_id=str(message.get("message_id") or ""),
                anchor_epoch_ms=(
                    int(message["internal_date_ms"])
                    if isinstance(message.get("internal_date_ms"), int)
                    else None
                ),
                conversation_id=str(profile["conversation_id"]),
                recipient=self._address(message.get("to")) or None,
                display_name=None,
                source="bounded_recent_sent_bootstrap",
            ):
                added += 1
        return added

    def _active_watch(self, principal_id: str, thread_id: str) -> dict[str, Any] | None:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM email_reply_watches WHERE principal_id=? AND thread_id=? "
                "AND status='active'",
                (principal_id, thread_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def _queue_event(
        self,
        profile: Mapping[str, Any],
        message: Mapping[str, Any],
        *,
        event_kind: str,
        classification: Mapping[str, Any],
    ) -> bool:
        message_id = str(message.get("message_id") or "").strip()
        if not message_id:
            return False
        now = self._iso(self._now())
        text = present_user_response(
            self._notification_text(message, classification), allow_technical=False
        )
        with self._db() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO email_assistant_events "
                "(principal_id,message_id,thread_id,event_kind,priority,conversation_id,"
                "classification_json,notification_text,status,next_attempt_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    profile["principal_id"],
                    message_id,
                    str(message.get("thread_id") or "") or None,
                    event_kind,
                    classification["level"],
                    profile["conversation_id"],
                    json.dumps(dict(classification), sort_keys=True, separators=(",", ":")),
                    text,
                    "pending",
                    now,
                    now,
                    now,
                ),
            ).rowcount
        return bool(inserted)

    async def _deliver_event(self, principal_id: str, message_id: str) -> bool:
        with self._db() as connection:
            claimed = connection.execute(
                "UPDATE email_assistant_events SET status='delivering',updated_at=? "
                "WHERE principal_id=? AND message_id=? AND status='pending'",
                (self._iso(self._now()), principal_id, message_id),
            ).rowcount
            row = connection.execute(
                "SELECT * FROM email_assistant_events WHERE principal_id=? AND message_id=?",
                (principal_id, message_id),
            ).fetchone()
        if not claimed or row is None:
            return False
        event = dict(row)
        text = present_user_response(str(event["notification_text"]), allow_technical=False)
        try:
            if self.conversations is not None:
                await self.conversations.ensure_conversation(
                    str(event["conversation_id"]), source="email_assistant"
                )
                await self.conversations.add_assistant_message(
                    str(event["conversation_id"]),
                    text,
                    delivery_key=f"email-assistant:{principal_id}:{message_id}",
                )
            if self.focus_recorder is not None and event.get("thread_id"):
                watch = self._active_watch(principal_id, str(event["thread_id"]))
                await self.focus_recorder(
                    str(event["conversation_id"]),
                    {
                        "message_id": message_id,
                        "thread_id": str(event["thread_id"]),
                        "sent_message_id": (
                            str(watch["anchor_message_id"]) if watch is not None else None
                        ),
                        "recipient": watch.get("recipient") if watch is not None else None,
                        "recipient_name": (
                            watch.get("display_name") if watch is not None else None
                        ),
                        "event_kind": str(event["event_kind"]),
                        "observed_at": self._iso(self._now()),
                    },
                )
        except Exception as exc:
            await self._retry_event(event, exc)
            return False

        notification_state = "not_requested"
        notification_result: Mapping[str, Any] = {}
        if self.notifier is not None:
            with self._db() as connection:
                connection.execute(
                    "UPDATE email_assistant_events SET notification_state='attempting',updated_at=? "
                    "WHERE principal_id=? AND message_id=? AND status='delivering'",
                    (self._iso(self._now()), principal_id, message_id),
                )
            try:
                notification_result = await self.notifier(
                    principal_id, text, "Jarvis Email Assistant"
                )
            except Exception as exc:
                # The transport may have accepted the request before the client
                # observed an exception.  Preserve an outcome-unknown result and
                # never blindly send the same notification again.
                notification_state = "outcome_unknown"
                notification_result = {
                    "outcome_unknown": True,
                    "error": redact_text(exc, max_length=300),
                }
            if bool(notification_result.get("success")) or bool(
                notification_result.get("command_accepted")
            ):
                notification_state = "accepted_unverified"
            elif bool(notification_result.get("outcome_unknown")):
                notification_state = "outcome_unknown"
            else:
                await self._retry_event(event, RuntimeError("Notification was rejected"))
                return False
        now = self._iso(self._now())
        with self._db() as connection:
            connection.execute(
                "UPDATE email_assistant_events SET status='delivered',notification_state=?,"
                "notification_result_json=?,delivered_at=?,updated_at=? "
                "WHERE principal_id=? AND message_id=? AND status='delivering'",
                (
                    notification_state,
                    json.dumps(
                        {
                            "accepted": notification_state == "accepted_unverified",
                            "outcome_unknown": notification_state == "outcome_unknown",
                        },
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                    principal_id,
                    message_id,
                ),
            )
            if event.get("event_kind") == "reply" and event.get("thread_id"):
                connection.execute(
                    "UPDATE email_reply_watches SET last_reply_message_id=?,updated_at=? "
                    "WHERE principal_id=? AND thread_id=?",
                    (message_id, now, principal_id, event["thread_id"]),
                )
        return True

    async def _retry_event(self, event: Mapping[str, Any], exc: BaseException) -> None:
        attempts = int(event.get("delivery_attempts") or 0) + 1
        now = self._now()
        status = "failed" if attempts >= 3 else "pending"
        next_attempt = now + timedelta(seconds=min(3600, 60 * (2**attempts)))
        with self._db() as connection:
            connection.execute(
                "UPDATE email_assistant_events SET status=?,delivery_attempts=?,"
                "notification_state='failed',notification_result_json=?,next_attempt_at=?,updated_at=? "
                "WHERE principal_id=? AND message_id=?",
                (
                    status,
                    attempts,
                    json.dumps({"error": redact_text(exc, max_length=300)}, separators=(",", ":")),
                    self._iso(next_attempt),
                    self._iso(now),
                    event["principal_id"],
                    event["message_id"],
                ),
            )

    async def _deliver_due_events(self, principal_id: str, now: datetime) -> int:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT message_id FROM email_assistant_events WHERE principal_id=? "
                "AND status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY created_at LIMIT 20",
                (principal_id, self._iso(now)),
            ).fetchall()
        delivered = 0
        for row in rows:
            delivered += int(await self._deliver_event(principal_id, str(row["message_id"])))
        return delivered

    async def _service_failed(
        self, profile: Mapping[str, Any], error: str, now: datetime
    ) -> dict[str, Any]:
        safe_error = redact_text(error, max_length=500)
        fingerprint = hashlib.sha256(safe_error.casefold().encode()).hexdigest()[:24]
        failures = int(profile.get("consecutive_failures") or 0) + 1
        next_run = now + timedelta(seconds=min(3600, 60 * (2 ** min(failures, 6))))
        with self._db() as connection:
            row = connection.execute(
                "SELECT outage_fingerprint FROM email_assistant_profiles WHERE principal_id=?",
                (profile["principal_id"],),
            ).fetchone()
            existing = str(row[0] or "") if row else ""
            connection.execute(
                "UPDATE email_assistant_profiles SET consecutive_failures=?,last_error=?,"
                "outage_fingerprint=?,next_check_at=?,updated_at=? WHERE principal_id=?",
                (
                    failures,
                    safe_error,
                    fingerprint,
                    self._iso(next_run),
                    self._iso(now),
                    profile["principal_id"],
                ),
            )
        queued = False
        if existing != fingerprint:
            reconnect = any(
                value in safe_error.casefold()
                for value in ("oauth", "token", "reconnect", "authentication", "not connected")
            )
            text = (
                "I can’t keep an eye on Gmail right now because Google needs reconnecting."
                if reconnect
                else "I can’t keep an eye on Gmail properly right now. I’ll retry shortly."
            )
            occurrence = hashlib.sha256(self._iso(now).encode()).hexdigest()[:12]
            synthetic = {
                "message_id": f"provider-outage:{fingerprint}:{occurrence}",
                "thread_id": None,
                "from": "Jarvis",
                "subject": text,
            }
            classification = {
                "level": "important",
                "category": "provider_outage",
                "reasons": [{"signal": "provider_unavailable", "source": "connector"}],
                "provider_truth": True,
                "content_is_authority": False,
            }
            # Keep the actionable wording rather than running it through email
            # subject formatting.
            queued = self._queue_event(
                profile, synthetic, event_kind="provider_outage", classification=classification
            )
            if queued:
                with self._db() as connection:
                    connection.execute(
                        "UPDATE email_assistant_events SET notification_text=? "
                        "WHERE principal_id=? AND message_id=?",
                        (text, profile["principal_id"], synthetic["message_id"]),
                    )
                    connection.execute(
                        "UPDATE email_assistant_profiles SET outage_notified_at=? "
                        "WHERE principal_id=?",
                        (self._iso(now), profile["principal_id"]),
                    )
        await self._deliver_due_events(str(profile["principal_id"]), self._now())
        return {
            "service": "email_assistant",
            "principal_id": profile["principal_id"],
            "status": "provider_failed",
            "notifications_queued": int(queued),
            "next_check_at": self._iso(next_run),
        }

    async def run_assistant(
        self, principal_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        current = (now or self._now()).astimezone(timezone.utc)
        profile = await self.assistant_status(principal_id=principal_id)
        if profile is None or profile["status"] != "active":
            return {
                "service": "email_assistant",
                "principal_id": principal_id,
                "status": "paused",
                "ran": False,
            }
        profile = dict(profile)
        with self._db() as connection:
            raw = connection.execute(
                "SELECT history_id,account_email,consecutive_failures FROM "
                "email_assistant_profiles WHERE principal_id=?",
                (principal_id,),
            ).fetchone()
        if raw is None:
            raise RuntimeError("Email Assistant profile disappeared")
        profile.update(
            history_id=raw["history_id"],
            account_email=raw["account_email"],
            consecutive_failures=int(raw["consecutive_failures"]),
        )
        delivered = await self._deliver_due_events(principal_id, current)
        watches = await self._sync_verified_send_watches(profile) if profile["reply_alerts"] else 0
        changes = await self._execute_read(
            profile,
            "gmail.changes",
            {"history_id": profile.get("history_id"), "limit": 100},
            operation="email_assistant_incremental_check",
        )
        if not changes.success:
            return await self._service_failed(
                profile, changes.error or "Gmail incremental check failed", current
            )
        data = dict(changes.data)
        history_id = str(data.get("history_id") or "").strip()
        if not history_id:
            return await self._service_failed(profile, "Gmail history cursor is missing", current)
        if (data.get("bootstrap") is True or data.get("cursor_expired") is True) and profile[
            "reply_alerts"
        ]:
            watches += await self._bootstrap_sent_watches(profile)
        account_email = str(data.get("account_email") or profile.get("account_email") or "")
        queued = 0
        processed = 0
        threshold = _PRIORITY_LEVELS[str(profile["importance_threshold"])]
        for raw_message in data.get("messages") or ():
            if not isinstance(raw_message, Mapping):
                continue
            message = dict(raw_message)
            message_id = str(message.get("message_id") or "").strip()
            thread_id = str(message.get("thread_id") or "").strip()
            if not message_id or not thread_id:
                continue
            processed += 1
            labels = {str(item) for item in message.get("label_ids") or ()}
            timestamp = (
                int(message["internal_date_ms"])
                if isinstance(message.get("internal_date_ms"), int)
                else None
            )
            if "SENT" in labels:
                if profile["reply_alerts"]:
                    watches += int(
                        self._upsert_watch(
                            principal_id=principal_id,
                            thread_id=thread_id,
                            anchor_message_id=message_id,
                            anchor_epoch_ms=timestamp,
                            conversation_id=str(profile["conversation_id"]),
                            recipient=self._address(message.get("to")) or None,
                            display_name=None,
                            source="gmail_incremental_sent_message",
                        )
                    )
                continue
            if labels & {"DRAFT", "TRASH"}:
                continue
            watch = self._active_watch(principal_id, thread_id) if profile["reply_alerts"] else None
            watched_reply = bool(
                watch
                and message_id != str(watch.get("anchor_message_id") or "")
                and (
                    watch.get("anchor_epoch_ms") is None
                    or timestamp is None
                    or timestamp > int(watch["anchor_epoch_ms"])
                )
            )
            known_contact = await self._known_contact(profile, message)
            classification = self.classify_message(
                message,
                owner_email=account_email,
                known_contact=known_contact is True,
                watched_reply=watched_reply,
            )
            should_notify = watched_reply or (
                profile["important_email_alerts"]
                and _PRIORITY_LEVELS[str(classification["level"])] >= threshold
            )
            if should_notify:
                delivery_profile = profile
                if watched_reply and watch is not None:
                    delivery_profile = {
                        **profile,
                        "conversation_id": str(watch["conversation_id"]),
                    }
                queued += int(
                    self._queue_event(
                        delivery_profile,
                        message,
                        event_kind="reply" if watched_reply else "important",
                        classification=classification,
                    )
                )
        next_check = current + timedelta(seconds=int(profile["poll_interval_seconds"]))
        with self._db() as connection:
            connection.execute(
                "UPDATE email_assistant_profiles SET history_id=?,account_email=?,last_check_at=?,"
                "next_check_at=?,consecutive_failures=0,outage_fingerprint=NULL,"
                "outage_notified_at=NULL,last_error=NULL,updated_at=? WHERE principal_id=?",
                (
                    history_id,
                    account_email or None,
                    self._iso(current),
                    self._iso(next_check),
                    self._iso(current),
                    principal_id,
                ),
            )
        # Events are timestamped while this pass is running, so use a fresh
        # clock value rather than the pass start time when claiming them.
        delivered += await self._deliver_due_events(principal_id, self._now())
        with self._db() as connection:
            connection.execute(
                "UPDATE email_assistant_profiles SET evaluated_count=evaluated_count+?,"
                "important_detected_count=important_detected_count+?,"
                "notified_count=notified_count+?,updated_at=? WHERE principal_id=?",
                (processed, queued, delivered, self._iso(self._now()), principal_id),
            )
        return {
            "service": "email_assistant",
            "principal_id": principal_id,
            "status": "healthy",
            "ran": True,
            "bootstrap": bool(data.get("bootstrap")),
            "cursor_expired": bool(data.get("cursor_expired")),
            "processed": processed,
            "notifications_queued": queued,
            "notifications_delivered": delivered,
            "reply_watches_observed": watches,
            "next_check_at": self._iso(next_check),
        }

    async def _read_message(
        self,
        *,
        policy: Mapping[str, Any],
        message_id: str,
    ) -> dict[str, Any] | None:
        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="gmail.read",
                payload={"message_id": message_id},
                request_id=str(uuid.uuid4()),
                conversation_id=str(policy["conversation_id"]),
                principal_id=str(policy["principal_id"]),
                operation="email_retention_inspect",
                target=message_id,
            ),
            refresh_health=True,
        )
        return dict(execution.data) if execution.success else None

    @staticmethod
    def _eligible(message: Mapping[str, Any], cutoff: datetime) -> tuple[bool, str]:
        labels = {str(item) for item in message.get("label_ids") or ()}
        if "INBOX" not in labels:
            return False, "not_in_inbox"
        forbidden = labels & {"SENT", "DRAFT", "TRASH"}
        if forbidden:
            return False, "forbidden_label:" + ",".join(sorted(forbidden))
        internal = message.get("internal_date_ms")
        if not isinstance(internal, int) or internal < 0:
            return False, "provider_timestamp_missing"
        try:
            received = datetime.fromtimestamp(internal / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return False, "provider_timestamp_invalid"
        return (received <= cutoff, "eligible" if received <= cutoff else "newer_than_cutoff")

    @classmethod
    def _safe_cleanup_eligible(
        cls,
        message: Mapping[str, Any],
        cutoff: datetime,
        *,
        protected_senders: Sequence[str],
        watched_threads: set[str],
        known_contact: bool | None,
        owner_email: str = "",
    ) -> tuple[bool, str, dict[str, Any]]:
        age_eligible, age_reason = cls._eligible(message, cutoff)
        classification = cls.classify_message(
            message,
            owner_email=owner_email,
            known_contact=known_contact is True,
            watched_reply=False,
        )
        if not age_eligible:
            return False, age_reason, classification
        if not cls._address(owner_email):
            return False, "account_identity_unavailable", classification
        labels = {str(item) for item in message.get("label_ids") or ()}
        protected_labels = labels & {"UNREAD", "STARRED", "IMPORTANT", "CATEGORY_PERSONAL"}
        if protected_labels:
            return (
                False,
                "protected_label:" + ",".join(sorted(protected_labels)),
                classification,
            )
        thread_id = str(message.get("thread_id") or "").strip()
        if thread_id and thread_id in watched_threads:
            return False, "active_reply_watch", classification
        sender = cls._address(message.get("from"))
        for protected in protected_senders:
            if sender == protected or (protected.startswith("@") and sender.endswith(protected)):
                return False, "protected_sender", classification
        if known_contact is None:
            return False, "contact_evidence_unavailable", classification
        if known_contact:
            return False, "known_personal_sender", classification
        if message.get("attachments"):
            return False, "message_has_attachments", classification
        category = str(classification.get("category") or "other")
        if category in _PROTECTED_CATEGORIES:
            return False, f"protected_category:{category}", classification
        if classification.get("level") != "low" or not classification.get("bulk_signal"):
            return False, "classification_uncertain_keep", classification
        return True, "read_low_value_old_mail", classification

    @staticmethod
    def _eligibility_key(policy: Mapping[str, Any], message_id: str, cutoff: datetime) -> str:
        material = (
            f"{policy['policy_id']}:{message_id}:{policy['retention_days']}:"
            f"{cutoff.date().isoformat()}:{policy.get('cleanup_mode', 'trash')}:"
            f"{policy.get('classification_version', 'legacy-inbox-age-v1')}:"
            "inbox-not-sent-draft-trash"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @classmethod
    def _cleanup_message_metadata(cls, message: Mapping[str, Any]) -> dict[str, Any]:
        """Keep the small, safe envelope needed to explain a later cleanup."""

        raw_sender = " ".join(str(message.get("from") or "").split())[:500]
        display_name, parsed_address = parseaddr(raw_sender)
        sender_address = cls._address(parsed_address or raw_sender)
        subject = " ".join(str(message.get("subject") or "").split())[:500]
        return {
            "thread_id": str(message.get("thread_id") or "").strip()[:300] or None,
            "sender_display_name": " ".join(display_name.split())[:200] or None,
            "sender_address": sender_address or None,
            "subject": subject or None,
            "previous_labels": sorted(str(item) for item in message.get("label_ids") or ()),
        }

    def _record_item(
        self,
        *,
        policy_id: str,
        message_id: str,
        eligibility_key: str,
        status: str,
        attempts: int,
        evidence: Mapping[str, Any],
        action_id: str | None = None,
        provider_reference: str | None = None,
        error: str | None = None,
    ) -> None:
        now = self._iso(self._now())
        with self._db() as connection:
            connection.execute(
                "INSERT INTO email_policy_items "
                "(policy_id,message_id,eligibility_key,status,attempts,action_id,"
                "provider_reference,evidence_json,error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(policy_id,message_id,eligibility_key) DO UPDATE SET "
                "status=excluded.status,attempts=excluded.attempts,action_id=excluded.action_id,"
                "provider_reference=excluded.provider_reference,evidence_json=excluded.evidence_json,"
                "error=excluded.error,updated_at=excluded.updated_at",
                (
                    policy_id,
                    message_id,
                    eligibility_key,
                    status,
                    attempts,
                    action_id,
                    provider_reference,
                    json.dumps(dict(evidence), sort_keys=True, separators=(",", ":")),
                    redact_text(error, max_length=1000) if error else None,
                    now,
                    now,
                ),
            )

    async def _reconcile_unknown_items(self, policy: Mapping[str, Any]) -> int:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM email_policy_items WHERE policy_id=? "
                "AND status IN ('executing','outcome_unknown')",
                (policy["policy_id"],),
            ).fetchall()
        reconciled = 0
        for row in rows:
            message_id = str(row["message_id"])
            receipt = None
            receipt_store = getattr(self.registry, "receipt_store", None)
            if receipt_store is not None:
                action_key = (
                    f"email-retention:{row['eligibility_key']}:attempt:{int(row['attempts'])}"
                )
                receipt = await receipt_store.get_by_idempotency_key(action_key)
            current = await self._read_message(policy=policy, message_id=message_id)
            if current is None:
                continue
            labels = {str(item) for item in current.get("label_ids") or ()}
            mode = str(policy.get("cleanup_mode") or "trash")
            effect_present = "TRASH" in labels if mode == "trash" else "INBOX" not in labels
            if effect_present:
                evidence = json.loads(str(row["evidence_json"] or "{}"))
                evidence["reconciliation"] = {
                    "expected_effect_present": True,
                    "cleanup_mode": mode,
                    "trash_label_present": True if mode == "trash" else None,
                    "observed_at": self._iso(self._now()),
                    "receipt_status_preserved": (
                        receipt.status.value if receipt is not None else "outcome_unknown"
                    ),
                }
                self._record_item(
                    policy_id=str(row["policy_id"]),
                    message_id=message_id,
                    eligibility_key=str(row["eligibility_key"]),
                    status="verified_after_unknown",
                    attempts=int(row["attempts"]),
                    action_id=(receipt.action_id if receipt is not None else row["action_id"]),
                    provider_reference=(
                        receipt.provider_reference
                        if receipt is not None
                        else row["provider_reference"]
                    ),
                    evidence=evidence,
                )
                reconciled += 1
            else:
                evidence = json.loads(str(row["evidence_json"] or "{}"))
                evidence["reconciliation"] = {
                    "expected_effect_present": False,
                    "cleanup_mode": mode,
                    "trash_label_present": False if mode == "trash" else None,
                    "observed_at": self._iso(self._now()),
                    "fresh_provider_read": True,
                    "retry_requires_new_receipt": True,
                    "prior_receipt_status": (
                        receipt.status.value if receipt is not None else "outcome_unknown"
                    ),
                }
                self._record_item(
                    policy_id=str(row["policy_id"]),
                    message_id=message_id,
                    eligibility_key=str(row["eligibility_key"]),
                    status="retryable_after_readback",
                    attempts=int(row["attempts"]),
                    action_id=(receipt.action_id if receipt is not None else row["action_id"]),
                    provider_reference=(
                        receipt.provider_reference
                        if receipt is not None
                        else row["provider_reference"]
                    ),
                    evidence=evidence,
                    error="Prior write outcome was unknown; fresh readback did not show the expected state",
                )
                reconciled += 1
        return reconciled

    async def run_policy(
        self,
        policy_id: str,
        *,
        now: datetime | None = None,
        force_dry_run: bool = False,
        allow_paused: bool = False,
        preview_cleanup_mode: str | None = None,
        preview_retention_days: int | None = None,
    ) -> dict[str, Any]:
        async with self._run_lock:
            with self._db() as connection:
                row = connection.execute(
                    "SELECT * FROM email_policies WHERE policy_id=?",
                    (str(policy_id),),
                ).fetchone()
            if row is None:
                raise KeyError("Email retention policy not found")
            policy = self._row(row)
            if policy["status"] != "active" and not (
                allow_paused and policy["status"] == "paused" and force_dry_run
            ):
                return {"policy_id": policy_id, "status": policy["status"], "ran": False}
            if (preview_cleanup_mode is not None or preview_retention_days is not None) and not (
                force_dry_run and policy["kind"] == "safe_cleanup"
            ):
                raise ValueError("Cleanup preview overrides require a safe dry run")
            if preview_cleanup_mode is not None:
                mode = str(preview_cleanup_mode).casefold()
                if mode not in {"archive", "trash"}:
                    raise ValueError("Cleanup mode must be archive or trash")
                policy["cleanup_mode"] = mode
            if preview_retention_days is not None:
                days = int(preview_retention_days)
                if days < 1 or days > 3650:
                    raise ValueError("Cleanup age must be between 1 and 3650 days")
                policy["retention_days"] = days

            current = (now or self._now()).astimezone(timezone.utc)
            cutoff = current - timedelta(days=int(policy["retention_days"]))
            safe_cleanup = policy["kind"] == "safe_cleanup"
            dry_run = bool(force_dry_run or policy["dry_run"])
            reconciled = 0 if dry_run else await self._reconcile_unknown_items(policy)
            query = f"in:inbox before:{int(cutoff.timestamp()) + 1}"
            if safe_cleanup:
                query = f"in:inbox is:read before:{int(cutoff.timestamp()) + 1}"
            search = await self.registry.execute(
                CapabilityRequest(
                    capability_id="gmail.search",
                    payload={
                        # Gmail accepts Unix seconds for before:. Query through the
                        # inclusive cutoff second, then enforce the exact timestamp
                        # and live labels again in _eligible before every write.
                        "query": query,
                        "limit": 100,
                    },
                    request_id=str(uuid.uuid4()),
                    conversation_id=policy["conversation_id"],
                    principal_id=policy["principal_id"],
                    operation="email_retention_candidates",
                ),
                refresh_health=True,
            )
            if not search.success:
                error = redact_text(
                    search.error or "Gmail retention search failed", max_length=1000
                )
                retry_at = current + timedelta(minutes=15)
                with self._db() as connection:
                    connection.execute(
                        "UPDATE email_policies SET last_run_at=?,last_error=?,next_run_at=?,updated_at=? "
                        "WHERE policy_id=?",
                        (
                            self._iso(current),
                            error,
                            self._iso(retry_at),
                            self._iso(current),
                            policy_id,
                        ),
                    )
                    self._audit(connection, policy, "run", "provider_failed", {"error": error})
                return {
                    "policy_id": policy_id,
                    "status": "provider_failed",
                    "ran": True,
                    "error": error,
                    "trashed": 0,
                    "archived": 0,
                    "dry_run_candidates": 0,
                }

            candidate_ids = [
                str(item) for item in search.data.get("message_ids") or () if str(item).strip()
            ]
            candidate_estimate = int(search.data.get("result_size_estimate") or len(candidate_ids))
            coverage_partial = candidate_estimate > len(candidate_ids)
            counts = {
                "trashed": 0,
                "archived": 0,
                "dry_run_candidates": 0,
                "skipped": 0,
                "failed": 0,
                "outcome_unknown": 0,
            }
            with self._db() as connection:
                watched_threads = {
                    str(item[0])
                    for item in connection.execute(
                        "SELECT thread_id FROM email_reply_watches WHERE principal_id=? "
                        "AND status='active'",
                        (policy["principal_id"],),
                    ).fetchall()
                }
                profile_row = connection.execute(
                    "SELECT account_email FROM email_assistant_profiles WHERE principal_id=?",
                    (policy["principal_id"],),
                ).fetchone()
                protection_rows = connection.execute(
                    "SELECT message_id,thread_id FROM email_cleanup_protections "
                    "WHERE principal_id=?",
                    (policy["principal_id"],),
                ).fetchall()
            owner_email = str(profile_row[0] or "") if profile_row else ""
            protected_message_ids = {str(item[0]) for item in protection_rows}
            protected_thread_ids = {str(item[1]) for item in protection_rows if item[1] is not None}
            if safe_cleanup and not self._address(owner_email):
                account = await self._execute_read(
                    policy,
                    "gmail.changes",
                    {"history_id": None, "limit": 1},
                    operation="email_cleanup_account_identity",
                )
                if account.success:
                    owner_email = str(account.data.get("account_email") or "")
                    if self._address(owner_email):
                        with self._db() as connection:
                            connection.execute(
                                "UPDATE email_assistant_profiles SET account_email=?,updated_at=? "
                                "WHERE principal_id=?",
                                (
                                    owner_email,
                                    self._iso(current),
                                    policy["principal_id"],
                                ),
                            )
            halted_reason: str | None = None
            for message_id in dict.fromkeys(candidate_ids):
                message = await self._read_message(policy=policy, message_id=message_id)
                if message is None:
                    counts["failed"] += 1
                    continue
                if (
                    message_id in protected_message_ids
                    or str(message.get("thread_id") or "") in protected_thread_ids
                ):
                    counts["skipped"] += 1
                    continue
                classification: Mapping[str, Any] = {}
                if safe_cleanup:
                    known_contact = await self._known_contact(policy, message)
                    eligible, reason, classification = self._safe_cleanup_eligible(
                        message,
                        cutoff,
                        protected_senders=policy["protected_senders"],
                        watched_threads=watched_threads,
                        known_contact=known_contact,
                        owner_email=owner_email,
                    )
                else:
                    eligible, reason = self._eligible(message, cutoff)
                if not eligible:
                    counts["skipped"] += 1
                    continue
                with self._db() as connection:
                    latest_policy = connection.execute(
                        "SELECT status,retention_days,cleanup_mode,dry_run,"
                        "protected_senders_json FROM email_policies WHERE policy_id=?",
                        (policy_id,),
                    ).fetchone()
                if latest_policy is None:
                    halted_reason = "policy_removed"
                    break
                if str(latest_policy["status"]) != "active" and not (
                    allow_paused and force_dry_run and str(latest_policy["status"]) == "paused"
                ):
                    halted_reason = f"policy_{latest_policy['status']}"
                    break
                if not force_dry_run and int(latest_policy["retention_days"]) != int(
                    policy["retention_days"]
                ):
                    halted_reason = "policy_changed"
                    break
                if not force_dry_run and (
                    str(latest_policy["cleanup_mode"]) != policy["cleanup_mode"]
                    or bool(latest_policy["dry_run"]) != bool(policy["dry_run"])
                    or json.loads(str(latest_policy["protected_senders_json"] or "[]"))
                    != policy["protected_senders"]
                ):
                    halted_reason = "policy_changed"
                    break
                eligibility_key = self._eligibility_key(policy, message_id, cutoff)
                with self._db() as connection:
                    existing = connection.execute(
                        "SELECT * FROM email_policy_items WHERE policy_id=? AND message_id=? "
                        "AND eligibility_key=?",
                        (policy_id, message_id, eligibility_key),
                    ).fetchone()
                if existing is not None and str(existing["status"]) in {
                    "verified",
                    "verified_after_unknown",
                    "outcome_unknown",
                }:
                    counts["skipped"] += 1
                    continue

                attempts = int(existing["attempts"] if existing is not None else 0) + 1
                evidence = {
                    "eligibility": reason,
                    "cutoff": self._iso(cutoff),
                    "provider_labels": sorted(str(item) for item in message.get("label_ids") or ()),
                    "internal_date_ms": message.get("internal_date_ms"),
                    "fresh_inspection": True,
                    "cleanup_mode": policy["cleanup_mode"],
                    "dry_run": dry_run,
                    "classification": dict(classification),
                    "permanent_delete": False,
                    "message_metadata": self._cleanup_message_metadata(message),
                    "policy_id": policy_id,
                    "policy_version": str(
                        policy.get("classification_version") or "legacy-inbox-age-v1"
                    ),
                    "cleanup_run_at": self._iso(current),
                }
                if dry_run:
                    counts["dry_run_candidates"] += 1
                    self._record_item(
                        policy_id=policy_id,
                        message_id=message_id,
                        eligibility_key=eligibility_key,
                        status="dry_run_candidate",
                        attempts=int(existing["attempts"] if existing is not None else 0),
                        evidence=evidence,
                    )
                    continue
                self._record_item(
                    policy_id=policy_id,
                    message_id=message_id,
                    eligibility_key=eligibility_key,
                    status="executing",
                    attempts=attempts,
                    evidence=evidence,
                )
                action_key = f"email-retention:{eligibility_key}:attempt:{attempts}"
                capability = (
                    "gmail.archive" if policy["cleanup_mode"] == "archive" else "gmail.trash"
                )
                action = await self.registry.execute(
                    CapabilityRequest(
                        capability_id=capability,
                        payload={"message_id": message_id},
                        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, action_key)),
                        conversation_id=policy["conversation_id"],
                        principal_id=policy["principal_id"],
                        operation=(
                            f"safe_cleanup_{policy['cleanup_mode']}"
                            if safe_cleanup
                            else "retention_inbox_to_trash"
                        ),
                        target=message_id,
                        confirmed=True,
                        standing_permission=True,
                        idempotency_key=action_key,
                    ),
                    refresh_health=True,
                )
                receipt = action.receipt
                evidence["action_status"] = action.status.value
                evidence["verification"] = dict(action.verification)
                evidence["cleanup_at"] = self._iso(self._now())
                evidence["action_receipt_id"] = receipt.action_id if receipt is not None else None
                if action.status is ExecutionStatus.VERIFIED:
                    item_status = "verified"
                    counts["archived" if policy["cleanup_mode"] == "archive" else "trashed"] += 1
                elif action.status is ExecutionStatus.OUTCOME_UNKNOWN:
                    item_status = "outcome_unknown"
                    counts["outcome_unknown"] += 1
                    observed = await self._read_message(policy=policy, message_id=message_id)
                    observed_labels = (
                        {str(item) for item in observed.get("label_ids") or ()}
                        if observed is not None
                        else set()
                    )
                    effect_present = (
                        "TRASH" in observed_labels
                        if policy["cleanup_mode"] == "trash"
                        else bool(observed is not None and "INBOX" not in observed_labels)
                    )
                    if effect_present:
                        item_status = "verified_after_unknown"
                        counts["outcome_unknown"] -= 1
                        counts[
                            "archived" if policy["cleanup_mode"] == "archive" else "trashed"
                        ] += 1
                        evidence["reconciliation"] = {
                            "expected_effect_present": True,
                            "cleanup_mode": policy["cleanup_mode"],
                            "receipt_status_preserved": "outcome_unknown",
                        }
                else:
                    item_status = "failed"
                    counts["failed"] += 1
                self._record_item(
                    policy_id=policy_id,
                    message_id=message_id,
                    eligibility_key=eligibility_key,
                    status=item_status,
                    attempts=attempts,
                    action_id=receipt.action_id if receipt is not None else None,
                    provider_reference=action.provider_reference,
                    evidence=evidence,
                    error=action.error,
                )

            if halted_reason is not None:
                with self._db() as connection:
                    connection.execute(
                        "UPDATE email_policies SET last_run_at=?,updated_at=? WHERE policy_id=?",
                        (self._iso(current), self._iso(current), policy_id),
                    )
                    self._audit(
                        connection,
                        policy,
                        "run",
                        "halted",
                        {**counts, "reason": halted_reason, "candidates": len(candidate_ids)},
                    )
                return {
                    "policy_id": policy_id,
                    "status": "halted",
                    "reason": halted_reason,
                    "ran": True,
                    "candidates": len(candidate_ids),
                    "candidate_estimate": candidate_estimate,
                    "coverage_partial": coverage_partial,
                    "reconciled": reconciled,
                    **counts,
                }

            incomplete = counts["failed"] + counts["outcome_unknown"]
            run_state = "completed" if incomplete == 0 else "partial"
            last_error = (
                None
                if incomplete == 0
                else (
                    f"{counts['failed']} retention item(s) failed and "
                    f"{counts['outcome_unknown']} outcome(s) remain unknown"
                )
            )
            next_run = current + timedelta(
                seconds=(900 if incomplete else int(policy["interval_seconds"]))
            )
            result_evidence = {
                **counts,
                "candidates": len(candidate_ids),
                "candidate_estimate": candidate_estimate,
                "coverage_partial": coverage_partial,
                "reconciled": reconciled,
                "cutoff": self._iso(cutoff),
                "cleanup_mode": policy["cleanup_mode"],
                "dry_run": dry_run,
                "permanent_delete": False,
            }
            if force_dry_run:
                with self._db() as connection:
                    self._audit(connection, policy, "preview", run_state, result_evidence)
                return {
                    "policy_id": policy_id,
                    "status": run_state,
                    "ran": True,
                    "candidates": len(candidate_ids),
                    "candidate_estimate": candidate_estimate,
                    "coverage_partial": coverage_partial,
                    "reconciled": reconciled,
                    **counts,
                    "error": last_error,
                    "next_run_at": policy["next_run_at"],
                }
            with self._db() as connection:
                connection.execute(
                    "UPDATE email_policies SET last_run_at=?,last_error=?,next_run_at=?,"
                    "last_result_json=?,updated_at=? "
                    "WHERE policy_id=?",
                    (
                        self._iso(current),
                        last_error,
                        self._iso(next_run),
                        json.dumps(result_evidence, sort_keys=True, separators=(",", ":")),
                        self._iso(current),
                        policy_id,
                    ),
                )
                if safe_cleanup:
                    connection.execute(
                        "UPDATE email_assistant_profiles SET last_cleanup_at=?,next_cleanup_at=?,"
                        "updated_at=? WHERE principal_id=?",
                        (
                            self._iso(current),
                            self._iso(next_run),
                            self._iso(current),
                            policy["principal_id"],
                        ),
                    )
                self._audit(
                    connection,
                    policy,
                    "run",
                    run_state,
                    result_evidence,
                )
            return {
                "policy_id": policy_id,
                "status": run_state,
                "ran": True,
                "candidates": len(candidate_ids),
                "candidate_estimate": candidate_estimate,
                "coverage_partial": coverage_partial,
                "reconciled": reconciled,
                **counts,
                "error": last_error,
                "next_run_at": self._iso(next_run),
            }

    async def audit(self, policy_id: str, *, principal_id: str) -> Sequence[dict[str, Any]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM email_policy_audit WHERE policy_id=? AND principal_id=? "
                "ORDER BY audit_id",
                (str(policy_id), str(principal_id)),
            ).fetchall()
        return [
            {
                "operation": str(row["operation"]),
                "state": str(row["state"]),
                "evidence": json.loads(str(row["evidence_json"] or "{}")),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    async def briefing(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        query: str = "in:inbox",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read Gmail and advance a durable per-conversation briefing watermark."""

        principal = str(principal_id or "").strip()
        conversation = str(conversation_id or "").strip()
        if not principal or not conversation:
            raise ValueError("A principal and conversation are required for an email briefing")
        if conversation.startswith("usr:") and not conversation.startswith(f"usr:{principal}:"):
            raise ValueError("Email briefing principal does not own its conversation")
        with self._db() as connection:
            state = connection.execute(
                "SELECT * FROM email_briefing_state WHERE principal_id=? AND conversation_id=?",
                (principal, conversation),
            ).fetchone()
        since_epoch = int(state["last_observed_epoch"]) if state is not None else None
        payload: dict[str, Any] = {
            "query": str(query or "in:inbox").strip() or "in:inbox",
            "limit": max(1, min(int(limit), 100)),
        }
        if since_epoch is not None:
            payload["since_epoch"] = since_epoch
        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="gmail.briefing",
                payload=payload,
                request_id=str(uuid.uuid4()),
                conversation_id=conversation,
                principal_id=principal,
                operation="email_inbox_briefing",
                target=payload["query"],
            ),
            refresh_health=True,
        )
        if not execution.success:
            return {
                "success": False,
                "live_evidence_available": False,
                "since_previous_briefing": since_epoch is not None,
                "error": redact_text(
                    execution.error or "Gmail briefing could not be verified",
                    max_length=1000,
                ),
            }
        data = dict(execution.data)
        observed_at = str(data.get("observed_at") or "").strip()
        try:
            parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Gmail briefing returned an invalid observation time") from exc
        if parsed.tzinfo is None:
            raise RuntimeError("Gmail briefing observation time has no timezone")
        observed_epoch = int(parsed.astimezone(timezone.utc).timestamp())
        if since_epoch is not None and observed_epoch < since_epoch:
            raise RuntimeError("Gmail briefing observation time moved backwards")
        provider_reference = (
            redact_text(execution.provider_reference, max_length=1000)
            if execution.provider_reference
            else None
        )
        with self._db() as connection:
            connection.execute(
                "INSERT INTO email_briefing_state "
                "(principal_id,conversation_id,last_observed_epoch,provider_reference,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(principal_id,conversation_id) DO UPDATE SET "
                "last_observed_epoch=excluded.last_observed_epoch,"
                "provider_reference=excluded.provider_reference,updated_at=excluded.updated_at",
                (
                    principal,
                    conversation,
                    observed_epoch,
                    provider_reference,
                    self._iso(self._now()),
                ),
            )
            persisted = connection.execute(
                "SELECT last_observed_epoch FROM email_briefing_state "
                "WHERE principal_id=? AND conversation_id=?",
                (principal, conversation),
            ).fetchone()
        if persisted is None or int(persisted[0]) != observed_epoch:
            raise RuntimeError("Email briefing watermark persistence could not be verified")
        return {
            "success": True,
            "live_evidence_available": True,
            "since_previous_briefing": since_epoch is not None,
            "previous_briefing_epoch": since_epoch,
            "watermark_persisted": True,
            "provider_reference": provider_reference,
            "briefing": data,
        }

    async def health_snapshot(self) -> dict[str, Any]:
        try:
            with self._db() as connection:
                quick = connection.execute("PRAGMA quick_check(1)").fetchone()
                active = connection.execute(
                    "SELECT COUNT(*) FROM email_policies WHERE status='active'"
                ).fetchone()
                profiles = connection.execute(
                    "SELECT COUNT(*) FROM email_assistant_profiles WHERE status='active'"
                ).fetchone()
                watches = connection.execute(
                    "SELECT COUNT(*) FROM email_reply_watches WHERE status='active'"
                ).fetchone()
                pending_events = connection.execute(
                    "SELECT COUNT(*) FROM email_assistant_events "
                    "WHERE status IN ('pending','delivering')"
                ).fetchone()
                counters = connection.execute(
                    "SELECT COALESCE(SUM(evaluated_count),0),"
                    "COALESCE(SUM(important_detected_count),0),"
                    "COALESCE(SUM(notified_count),0) FROM email_assistant_profiles"
                ).fetchone()
            database_healthy = quick is not None and str(quick[0]).casefold() == "ok"
            worker_running = self._task is not None and not self._task.done()
            healthy = database_healthy and worker_running
            return {
                "healthy": healthy,
                "status": "healthy" if healthy else "degraded",
                "worker_running": worker_running,
                "database_healthy": database_healthy,
                "database": {"healthy": database_healthy},
                "active_policies": int(active[0]) if active is not None else 0,
                "active_assistants": int(profiles[0]) if profiles is not None else 0,
                "active_reply_watches": int(watches[0]) if watches is not None else 0,
                "pending_notifications": (
                    int(pending_events[0]) if pending_events is not None else 0
                ),
                "evaluated_count": int(counters[0]) if counters is not None else 0,
                "important_detected_count": int(counters[1]) if counters is not None else 0,
                "notified_count": int(counters[2]) if counters is not None else 0,
                "reason": (
                    None
                    if healthy
                    else (
                        "Durable email policy storage is unavailable."
                        if not database_healthy
                        else "Email policy worker is not running."
                    )
                ),
            }
        except sqlite3.Error as exc:
            return {
                "healthy": False,
                "status": "degraded",
                "worker_running": self._task is not None and not self._task.done(),
                "database_healthy": False,
                "database": {"healthy": False},
                "error": redact_text(exc, max_length=500),
            }
