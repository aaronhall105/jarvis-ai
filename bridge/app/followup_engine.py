"""Durable, idempotent same-conversation follow-ups for Jarvis Core."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.connectors.audit import ActionReceiptStore, ReceiptStatus
from app.connectors.credentials import redact_secrets, redact_text
from app.followup_schedule import next_recurrence, resolve_recurrence, resolve_schedule


MIN_EXTERNAL_INTERVAL_SECONDS = 10
MAX_EXTERNAL_INTERVAL_SECONDS = 30 * 86400
VALID_EXTERNAL_COMPARISONS = {
    "changed",
    "equals",
    "not_equals",
    "decreased",
    "increased",
    "less_than",
    "greater_than",
    "contains",
    "truthy",
}
VALID_FOLLOWUP_KINDS = {
    "time",
    "scheduled",
    "condition",
    "periodic",
    "completion",
    "external_monitor",
    "recurring",
}
VALID_FOLLOWUP_STATUSES = {
    "pending",
    "executing",
    "delivery_pending",
    "delivering",
    "completed",
    "failed",
    "cancelled",
    "expired",
    "paused",
}
EXPLICIT_TARGET_COMPARISONS = {
    "equals",
    "not_equals",
    "less_than",
    "greater_than",
    "contains",
}
MAX_EXTERNAL_POLLS = 1_000_000


class ConversationWriter(Protocol):
    async def add_assistant_message(
        self,
        conversation_id: str,
        content: str,
        *,
        delivery_key: str | None = None,
    ) -> dict[str, Any]: ...


class StateReader(Protocol):
    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]: ...


class NotificationSender(Protocol):
    async def __call__(
        self, recipient: str, message: str, title: str = "Jarvis"
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ExternalMonitorEvaluation:
    """Verified observation returned by an injected external evaluator."""

    verified: bool
    value: Any = None
    message: str | None = None
    provider_reference: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, slots=True)
class FollowUpCommandResult:
    handled: bool
    success: bool = True
    response: str = ""
    intent: str = "personal_task"
    details: Mapping[str, Any] | None = None


class ExternalMonitorEvaluator(Protocol):
    async def evaluate_external_monitor(
        self, monitor: Mapping[str, Any]
    ) -> ExternalMonitorEvaluation | Mapping[str, Any]: ...


ExternalEvaluatorCallback = Callable[
    [Mapping[str, Any]],
    Awaitable[ExternalMonitorEvaluation | Mapping[str, Any]]
    | ExternalMonitorEvaluation
    | Mapping[str, Any],
]


class FollowUpEngine:
    """SQLite-backed worker with verified, exactly-once chat delivery."""

    def __init__(
        self,
        database_path: str,
        conversations: ConversationWriter,
        states: StateReader,
        poll_seconds: int = 2,
        external_evaluator: ExternalMonitorEvaluator | ExternalEvaluatorCallback | None = None,
        max_attempts: int = 3,
        receipts: ActionReceiptStore | None = None,
        notifier: NotificationSender | None = None,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations, self.states = conversations, states
        self.external_evaluator = external_evaluator
        self.poll_seconds = max(1, min(poll_seconds, 60))
        self.max_attempts = max(1, min(int(max_attempts), 10))
        self.receipts = receipts
        self.notifier = notifier
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._init()

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self) -> None:
        with self._db() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS followup_jobs (
                  job_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                  kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                  status TEXT NOT NULL, created_at TEXT NOT NULL,
                  next_run_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL DEFAULT 3, delivered_at TEXT,
                  result_json TEXT, idempotency_key TEXT NOT NULL UNIQUE,
                  delivery_attempts INTEGER NOT NULL DEFAULT 0,
                  delivery_state TEXT NOT NULL DEFAULT 'pending',
                  delivery_message TEXT, completion_status TEXT, cancelled_at TEXT,
                  request_fingerprint TEXT, poll_count INTEGER NOT NULL DEFAULT 0,
                  max_polls INTEGER, expires_at TEXT,
                  principal_id TEXT NOT NULL DEFAULT 'aaron', device_id TEXT,
                  originating_endpoint TEXT, capability_id TEXT NOT NULL DEFAULT 'personal.reminder',
                  schedule_json TEXT, occurrence_index INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT, paused_at TEXT, last_evaluated_at TEXT,
                  last_observed_state_json TEXT, verified_at TEXT,
                  notification_state TEXT NOT NULL DEFAULT 'not_requested'
                );
                CREATE INDEX IF NOT EXISTS idx_followup_due
                  ON followup_jobs(status, next_run_at);
                CREATE TABLE IF NOT EXISTS followup_job_audit (
                  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL, principal_id TEXT NOT NULL,
                  operation TEXT NOT NULL, state TEXT NOT NULL,
                  evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_followup_audit_job
                  ON followup_job_audit(job_id, audit_id DESC);
                """
            )
            columns = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(followup_jobs)").fetchall()
            }
            legacy_request_fingerprints = "principal_id" not in columns
            migrations = {
                "max_attempts": "INTEGER NOT NULL DEFAULT 3",
                "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
                "delivery_state": "TEXT NOT NULL DEFAULT 'pending'",
                "delivery_message": "TEXT",
                "completion_status": "TEXT",
                "cancelled_at": "TEXT",
                "request_fingerprint": "TEXT",
                "poll_count": "INTEGER NOT NULL DEFAULT 0",
                "max_polls": "INTEGER",
                "expires_at": "TEXT",
                "principal_id": "TEXT NOT NULL DEFAULT 'aaron'",
                "device_id": "TEXT",
                "originating_endpoint": "TEXT",
                "capability_id": "TEXT NOT NULL DEFAULT 'personal.reminder'",
                "schedule_json": "TEXT",
                "occurrence_index": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "TEXT",
                "paused_at": "TEXT",
                "last_evaluated_at": "TEXT",
                "last_observed_state_json": "TEXT",
                "verified_at": "TEXT",
                "notification_state": "TEXT NOT NULL DEFAULT 'not_requested'",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    con.execute(f"ALTER TABLE followup_jobs ADD COLUMN {column} {definition}")
            if legacy_request_fingerprints:
                # The fingerprint input gained principal/schedule ownership.
                # Recompute lazily on the next idempotent request rather than
                # rejecting valid retries of pre-v1 jobs.
                con.execute("UPDATE followup_jobs SET request_fingerprint=NULL")
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_followup_principal
                ON followup_jobs(principal_id, status, next_run_at)
                """
            )

            # Evaluation is safe to repeat. Delivery reuses a stable conversation
            # key, closing the crash window after a message commit.
            con.execute(
                "UPDATE followup_jobs SET status='pending',delivery_state='pending' "
                "WHERE status='executing'"
            )
            con.execute(
                "UPDATE followup_jobs SET status='delivery_pending',"
                "delivery_state='pending' WHERE status='delivering'"
            )
            con.execute(
                "UPDATE followup_jobs SET delivery_state='delivered' "
                "WHERE delivered_at IS NOT NULL "
                "AND status IN ('completed','failed','expired') "
                "AND delivery_state='pending'"
            )
            con.execute("UPDATE followup_jobs SET updated_at=created_at WHERE updated_at IS NULL")
            # Recover the principal from the existing user-scoped conversation
            # identifiers without changing any historical job identity.
            rows = con.execute(
                "SELECT job_id,conversation_id,principal_id FROM followup_jobs"
            ).fetchall()
            for row in rows:
                if str(row["principal_id"] or "") in {"", "aaron"}:
                    inferred = self._principal_from_conversation(str(row["conversation_id"]))
                    con.execute(
                        "UPDATE followup_jobs SET principal_id=? WHERE job_id=?",
                        (inferred, row["job_id"]),
                    )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    async def create(
        self,
        *,
        conversation_id: str,
        kind: str,
        payload: dict[str, Any],
        due_at: datetime,
        idempotency_key: str | None = None,
        principal_id: str | None = None,
        device_id: str | None = None,
        originating_endpoint: str | None = None,
        capability_id: str | None = None,
        schedule: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation_id = str(conversation_id).strip()
        if not conversation_id:
            raise ValueError("A conversation is required for a follow-up")
        if kind not in VALID_FOLLOWUP_KINDS:
            raise ValueError("Unsupported follow-up type")
        if not isinstance(payload, dict):
            raise ValueError("Follow-up payload must be an object")
        principal = str(principal_id or self._principal_from_conversation(conversation_id)).strip()
        if not principal or len(principal) > 64:
            raise ValueError("A valid principal is required for a follow-up")
        inferred_principal = self._principal_from_conversation(conversation_id)
        if conversation_id.startswith("usr:") and inferred_principal != principal:
            raise ValueError("Follow-up principal does not own its conversation")
        resolved_capability = str(
            capability_id
            or (
                "personal.monitor"
                if kind in {"condition", "periodic", "external_monitor"}
                else "personal.reminder"
            )
        ).strip()
        if not resolved_capability or len(resolved_capability) > 150:
            raise ValueError("A valid capability is required for a follow-up")
        schedule_json: str | None = None
        if kind == "recurring":
            if not isinstance(schedule, Mapping):
                raise ValueError("Recurring follow-ups require a structured schedule")
            schedule_json = json.dumps(dict(schedule), separators=(",", ":"), sort_keys=True)
            if next_recurrence(schedule, after_utc=self._now()) is None:
                raise ValueError("Recurring follow-up schedule is invalid")
        elif schedule is not None:
            raise ValueError("Only recurring follow-ups accept a recurrence schedule")

        resolved_payload = dict(payload)
        maximum = self.max_attempts
        if kind == "external_monitor":
            if self.external_evaluator is None:
                raise RuntimeError("External monitor evaluator is not configured")
            resolved_payload, maximum = self._normalise_external_payload(
                conversation_id, resolved_payload
            )
        try:
            payload_json = json.dumps(resolved_payload, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Follow-up payload must be JSON serializable") from exc

        key = str(idempotency_key or uuid.uuid4()).strip()
        if not key or len(key) > 255:
            raise ValueError("Follow-up idempotency key is invalid")
        if redact_text(key, max_length=255) != key:
            raise ValueError("Follow-up idempotency keys may not contain secrets")
        fingerprint = self._request_fingerprint(
            conversation_id, kind, resolved_payload, principal_id=principal, schedule=schedule
        )
        receipt_job_id = "new:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        receipt_payload = {
            "kind": kind,
            "capability_id": resolved_capability,
            "request_fingerprint": fingerprint,
        }
        with self._db() as con:
            preexisting = con.execute(
                "SELECT * FROM followup_jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
        if preexisting is not None:
            self._assert_idempotent_match(
                preexisting, conversation_id, kind, fingerprint, principal
            )
            existing_job = self._row(preexisting)
            receipt = await self._begin_mutation_receipt(
                operation="create",
                job_id=receipt_job_id,
                principal_id=principal,
                conversation_id=conversation_id,
                request_id=key,
                payload=receipt_payload,
            )
            if receipt is not None and receipt.status is ReceiptStatus.STARTED:
                assert self.receipts is not None
                receipt = await self.receipts.complete(
                    receipt.action_id,
                    status=ReceiptStatus.VERIFIED,
                    provider_reference=str(existing_job["job_id"]),
                    result={
                        "job_id": existing_job["job_id"],
                        "state": existing_job["status"],
                        "next_run_at": existing_job["next_run_at"],
                    },
                    verification={"persisted": True, "principal_id": principal},
                )
            existing_job["action_receipt"] = receipt.as_dict() if receipt is not None else None
            return existing_job
        max_polls = (
            int(resolved_payload["max_polls"])
            if kind == "external_monitor" and resolved_payload.get("max_polls") is not None
            else None
        )
        expires_at = (
            str(resolved_payload["expires_at"])
            if kind == "external_monitor" and resolved_payload.get("expires_at") is not None
            else None
        )
        job_id, now = str(uuid.uuid4()), self._iso(self._now())
        receipt = await self._begin_mutation_receipt(
            operation="create",
            job_id=receipt_job_id,
            principal_id=principal,
            conversation_id=conversation_id,
            request_id=key,
            payload=receipt_payload,
        )
        try:
            with self._db() as con:
                con.execute(
                    """
                    INSERT INTO followup_jobs(
                      job_id,conversation_id,kind,payload_json,status,created_at,
                      next_run_at,idempotency_key,max_attempts,delivery_state,
                      request_fingerprint,poll_count,max_polls,expires_at,
                      principal_id,device_id,originating_endpoint,capability_id,
                      schedule_json,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        job_id,
                        conversation_id,
                        kind,
                        payload_json,
                        "pending",
                        now,
                        self._iso(due_at),
                        key,
                        maximum,
                        "pending",
                        fingerprint,
                        0,
                        max_polls,
                        expires_at,
                        principal,
                        str(device_id).strip() if device_id else None,
                        str(originating_endpoint).strip() if originating_endpoint else None,
                        resolved_capability,
                        schedule_json,
                        now,
                    ),
                )
                self._audit_sync(
                    con,
                    job_id=job_id,
                    principal_id=principal,
                    operation="create",
                    state="persisted",
                    evidence={"kind": kind, "next_run_at": self._iso(due_at)},
                )
        except sqlite3.IntegrityError:
            with self._db() as con:
                existing = con.execute(
                    "SELECT * FROM followup_jobs WHERE idempotency_key=?", (key,)
                ).fetchone()
            if existing is None:
                if receipt is not None and receipt.status is ReceiptStatus.STARTED:
                    assert self.receipts is not None
                    await self.receipts.complete(
                        receipt.action_id,
                        status=ReceiptStatus.FAILED,
                        error="Follow-up persistence failed",
                    )
                raise
            self._assert_idempotent_match(existing, conversation_id, kind, fingerprint, principal)
            raced = self._row(existing)
            if receipt is not None and receipt.status is ReceiptStatus.STARTED:
                assert self.receipts is not None
                receipt = await self.receipts.complete(
                    receipt.action_id,
                    status=ReceiptStatus.VERIFIED,
                    provider_reference=str(raced["job_id"]),
                    result={
                        "job_id": raced["job_id"],
                        "state": raced["status"],
                        "next_run_at": raced["next_run_at"],
                    },
                    verification={"persisted": True, "principal_id": principal},
                )
            raced["action_receipt"] = receipt.as_dict() if receipt is not None else None
            return raced
        except sqlite3.Error:
            if receipt is not None and receipt.status is ReceiptStatus.STARTED:
                assert self.receipts is not None
                await self.receipts.complete(
                    receipt.action_id,
                    status=ReceiptStatus.FAILED,
                    error="Follow-up persistence failed",
                )
            raise
        persisted = await self.get(job_id, principal_id=principal)
        if persisted is None:
            if receipt is not None:
                assert self.receipts is not None
                await self.receipts.complete(
                    receipt.action_id,
                    status=ReceiptStatus.FAILED,
                    error="Follow-up persistence verification failed",
                )
            raise RuntimeError("The follow-up was not durably persisted")
        if receipt is not None:
            assert self.receipts is not None
            completed_receipt = await self.receipts.complete(
                receipt.action_id,
                status=ReceiptStatus.VERIFIED,
                provider_reference=job_id,
                result={
                    "job_id": job_id,
                    "state": persisted["status"],
                    "next_run_at": persisted["next_run_at"],
                },
                verification={"persisted": True, "principal_id": principal},
            )
            persisted["action_receipt"] = completed_receipt.as_dict()
        else:
            persisted["action_receipt"] = None
        return persisted

    @staticmethod
    def _request_fingerprint(
        conversation_id: str,
        kind: str,
        payload: Mapping[str, Any],
        *,
        principal_id: str | None = None,
        schedule: Mapping[str, Any] | None = None,
    ) -> str:
        fingerprint_payload = dict(payload)
        if kind == "external_monitor":
            # The live baseline is captured before the durable store is called.
            # It may legitimately differ on an exact transport retry, while the
            # requested monitor definition remains the same.
            fingerprint_payload.pop("baseline", None)
        material = json.dumps(
            {
                "conversation_id": conversation_id,
                "kind": kind,
                "payload": fingerprint_payload,
                "principal_id": principal_id,
                "schedule": dict(schedule) if schedule is not None else None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _assert_idempotent_match(
        self,
        row: sqlite3.Row,
        conversation_id: str,
        kind: str,
        fingerprint: str,
        principal_id: str,
    ) -> None:
        existing_fingerprint = str(row["request_fingerprint"] or "")
        if not existing_fingerprint:
            existing_fingerprint = self._request_fingerprint(
                str(row["conversation_id"]),
                str(row["kind"]),
                json.loads(str(row["payload_json"])),
                principal_id=str(row["principal_id"] or ""),
                schedule=(json.loads(str(row["schedule_json"])) if row["schedule_json"] else None),
            )
        if (
            str(row["conversation_id"]) != conversation_id
            or str(row["kind"]) != kind
            or str(row["principal_id"] or "") != principal_id
            or existing_fingerprint != fingerprint
        ):
            raise ValueError("Follow-up idempotency key was already used for a different request")

    @staticmethod
    def _principal_from_conversation(conversation_id: str) -> str:
        if conversation_id.startswith("usr:"):
            parts = conversation_id.split(":", 2)
            if len(parts) == 3 and parts[1]:
                return parts[1][:64]
        return "aaron"

    async def _begin_mutation_receipt(
        self,
        *,
        operation: str,
        job_id: str,
        principal_id: str,
        conversation_id: str,
        request_id: str,
        payload: Mapping[str, Any],
    ):
        if self.receipts is None:
            return None
        claim = await self.receipts.begin(
            request_id=request_id,
            conversation_id=conversation_id,
            capability_id="personal.tasks.manage",
            provider_id="jarvis_core",
            target={"job_id": job_id, "principal_id": principal_id},
            requested_operation=operation,
            request_payload=payload,
            idempotency_key=f"personal-task:{operation}:{request_id}",
        )
        return claim.receipt

    async def _recover_mutation_receipt(
        self,
        *,
        operation: str,
        request_id: str | None,
        job: Mapping[str, Any],
        expected_state: str,
    ) -> None:
        """Complete only a previously-started local mutation after a retry."""

        if self.receipts is None or request_id is None:
            return
        receipt = await self.receipts.get_by_idempotency_key(
            f"personal-task:{operation}:{request_id}"
        )
        if receipt is None or receipt.status is not ReceiptStatus.STARTED:
            return
        await self.receipts.complete(
            receipt.action_id,
            status=ReceiptStatus.VERIFIED,
            provider_reference=str(job["job_id"]),
            result={"job_id": job["job_id"], "state": expected_state},
            verification={"persisted": True},
        )

    def _audit_sync(
        self,
        con: sqlite3.Connection,
        *,
        job_id: str,
        principal_id: str,
        operation: str,
        state: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        con.execute(
            """
            INSERT INTO followup_job_audit(
              job_id,principal_id,operation,state,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                job_id,
                principal_id,
                operation,
                state,
                json.dumps(dict(evidence or {}), separators=(",", ":"), sort_keys=True),
                self._iso(self._now()),
            ),
        )

    def _normalise_external_payload(
        self, conversation_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        provider = str(payload.get("provider") or payload.get("provider_id") or "").strip()
        capability_id = str(payload.get("capability_id") or "").strip()
        if not provider:
            raise ValueError("External monitor provider is required")
        if not capability_id:
            raise ValueError("External monitor capability_id is required")
        has_operation = "operation" in payload and self._has_monitor_value(payload.get("operation"))
        arguments = payload.get("arguments")
        if isinstance(arguments, Mapping) and arguments:
            has_operation = True
        has_query = "query" in payload and self._has_monitor_value(payload.get("query"))
        if not has_operation and not has_query:
            raise ValueError("External monitor operation or query is required")
        if "baseline" not in payload:
            raise ValueError("External monitor baseline is required")
        if "comparison" not in payload:
            raise ValueError("External monitor comparison is required")
        if (
            redact_secrets(payload["baseline"]) != payload["baseline"]
            or redact_secrets(payload["comparison"]) != payload["comparison"]
        ):
            raise ValueError("External monitor definitions may not contain credentials or secrets")
        self._validate_external_comparison(payload["baseline"], payload["comparison"])

        supplied_conversation = str(payload.get("conversation_id") or "").strip()
        if supplied_conversation and supplied_conversation != conversation_id:
            raise ValueError("External monitor payload conversation does not match its job")
        interval = payload.get("polling_interval_seconds", payload.get("interval_seconds"))
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise ValueError("External monitor polling_interval_seconds must be an integer")
        if not MIN_EXTERNAL_INTERVAL_SECONDS <= interval <= MAX_EXTERNAL_INTERVAL_SECONDS:
            raise ValueError(
                "External monitor polling interval must be between "
                f"{MIN_EXTERNAL_INTERVAL_SECONDS} and "
                f"{MAX_EXTERNAL_INTERVAL_SECONDS} seconds"
            )
        maximum = payload.get("max_attempts", self.max_attempts)
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise ValueError("External monitor max_attempts must be an integer")
        if not 1 <= maximum <= 10:
            raise ValueError("External monitor max_attempts must be between 1 and 10")
        if payload.get("poll_count") not in (None, 0):
            raise ValueError("External monitor poll_count is managed by Jarvis")
        payload.pop("poll_count", None)
        max_polls = payload.get("max_polls")
        if max_polls is not None:
            if isinstance(max_polls, bool) or not isinstance(max_polls, int):
                raise ValueError("External monitor max_polls must be an integer")
            if not 1 <= max_polls <= MAX_EXTERNAL_POLLS:
                raise ValueError(
                    f"External monitor max_polls must be between 1 and {MAX_EXTERNAL_POLLS}"
                )
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            parsed_expiry = self._parse_expiry(expires_at)
            if parsed_expiry <= self._now():
                raise ValueError("External monitor expires_at must be in the future")
            payload["expires_at"] = self._iso(parsed_expiry)
        notify_if_unchanged = payload.get("notify_if_unchanged", False)
        if not isinstance(notify_if_unchanged, bool):
            raise ValueError("External monitor notify_if_unchanged must be a boolean")
        if notify_if_unchanged and expires_at is None:
            raise ValueError("A no-change reminder requires an expiry deadline")
        payload["notify_if_unchanged"] = notify_if_unchanged
        # Legacy callers could supply arbitrary notification wording.  Monitor
        # delivery is generated from verified evidence, so that text is neither
        # persisted nor used.
        payload.pop("message", None)
        if redact_secrets(payload) != payload:
            raise ValueError("External monitor definitions may not contain credentials or secrets")

        payload.update(
            provider=provider,
            capability_id=capability_id,
            conversation_id=conversation_id,
            polling_interval_seconds=interval,
            max_attempts=maximum,
            max_polls=max_polls,
        )
        return payload, maximum

    @staticmethod
    def _parse_expiry(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            rendered = value.strip()
            if rendered.endswith("Z"):
                rendered = rendered[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(rendered)
            except ValueError as exc:
                raise ValueError(
                    "External monitor expires_at must be an ISO-8601 timestamp"
                ) from exc
        else:
            raise ValueError("External monitor expires_at must be an ISO-8601 timestamp")
        if parsed.tzinfo is None:
            raise ValueError("External monitor expires_at must include a timezone")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _has_monitor_value(value: Any) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    async def get(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["job_id=?"]
        values: list[Any] = [job_id]
        if kind is not None:
            if kind not in VALID_FOLLOWUP_KINDS:
                raise ValueError("Unsupported follow-up type")
            clauses.append("kind=?")
            values.append(kind)
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            values.append(conversation_id)
        if principal_id is not None:
            clauses.append("principal_id=?")
            values.append(str(principal_id))
        with self._db() as con:
            row = con.execute(
                "SELECT * FROM followup_jobs WHERE " + " AND ".join(clauses),
                values,
            ).fetchone()
        return self._row(row) if row else None

    async def get_by_idempotency_key(
        self,
        conversation_id: str,
        idempotency_key: str,
        *,
        kind: str = "external_monitor",
    ) -> dict[str, Any] | None:
        scoped_conversation = str(conversation_id).strip()
        if not scoped_conversation:
            raise ValueError("A conversation is required for follow-up lookup")
        if kind not in VALID_FOLLOWUP_KINDS:
            raise ValueError("Unsupported follow-up type")
        clauses = ["conversation_id=?", "idempotency_key=?", "kind=?"]
        values: list[Any] = [
            scoped_conversation,
            str(idempotency_key),
            kind,
        ]
        with self._db() as con:
            row = con.execute(
                "SELECT * FROM followup_jobs WHERE " + " AND ".join(clauses),
                values,
            ).fetchone()
        return self._row(row) if row else None

    async def status(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.get(
            job_id,
            kind=kind,
            conversation_id=conversation_id,
            principal_id=principal_id,
        )

    async def list(
        self,
        *,
        conversation_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        principal_id: str | None = None,
        limit: int = 100,
    ) -> List[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            values.append(conversation_id)
        if kind is not None:
            if kind not in VALID_FOLLOWUP_KINDS:
                raise ValueError("Unsupported follow-up type")
            clauses.append("kind=?")
            values.append(kind)
        if status is not None:
            if status not in VALID_FOLLOWUP_STATUSES:
                raise ValueError("Unsupported follow-up status")
            clauses.append("status=?")
            values.append(status)
        if principal_id is not None:
            clauses.append("principal_id=?")
            values.append(str(principal_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 500)))
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM followup_jobs" + where + " ORDER BY created_at DESC LIMIT ?", values
            ).fetchall()
        return [self._row(row) for row in rows]

    async def list_jobs(
        self,
        *,
        conversation_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        principal_id: str | None = None,
        limit: int = 100,
    ) -> List[dict[str, Any]]:
        return await self.list(
            conversation_id=conversation_id,
            kind=kind,
            status=status,
            principal_id=principal_id,
            limit=limit,
        )

    async def active_for_conversation(self, conversation_id: str) -> List[dict[str, Any]]:
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs WHERE conversation_id=?
                AND status IN ('pending','executing','delivery_pending','delivering','paused')
                ORDER BY created_at DESC
                """,
                (conversation_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    async def cancel(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
        principal_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Cancel work that has not atomically claimed its chat delivery."""

        async with self._operation_lock:
            return await self._cancel_locked(
                job_id,
                kind=kind,
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
            )

    async def _cancel_locked(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
        principal_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        filters = ["job_id=?"]
        values: list[Any] = [job_id]
        if kind is not None:
            if kind not in VALID_FOLLOWUP_KINDS:
                raise ValueError("Unsupported follow-up type")
            filters.append("kind=?")
            values.append(kind)
        if conversation_id is not None:
            filters.append("conversation_id=?")
            values.append(conversation_id)
        if principal_id is not None:
            filters.append("principal_id=?")
            values.append(str(principal_id))
        predicate = " AND ".join(filters)
        current = await self.get(
            job_id,
            kind=kind,
            conversation_id=conversation_id,
            principal_id=principal_id,
        )
        if current is None:
            return None
        if current["status"] == "cancelled":
            await self._recover_mutation_receipt(
                operation="cancel",
                request_id=request_id,
                job=current,
                expected_state="cancelled",
            )
            return current
        receipt = await self._begin_mutation_receipt(
            operation="cancel",
            job_id=job_id,
            principal_id=str(current["principal_id"]),
            conversation_id=str(current["conversation_id"]),
            request_id=str(request_id or uuid.uuid4()),
            payload={"operation": "cancel"},
        )
        with self._db() as con:
            changed = con.execute(
                f"""
                UPDATE followup_jobs SET status='cancelled',cancelled_at=?,
                delivery_state='cancelled',updated_at=? WHERE {predicate}
                AND status IN ('pending','executing','delivery_pending','paused')
                """,
                [self._iso(self._now()), self._iso(self._now()), *values],
            ).rowcount
            if changed:
                self._audit_sync(
                    con,
                    job_id=job_id,
                    principal_id=str(current["principal_id"]),
                    operation="cancel",
                    state="verified",
                    evidence={"previous_state": current["status"]},
                )
            row = con.execute("SELECT * FROM followup_jobs WHERE " + predicate, values).fetchone()
        updated = self._row(row) if row else None
        if receipt is not None and receipt.status is ReceiptStatus.STARTED:
            assert self.receipts is not None
            await self.receipts.complete(
                receipt.action_id,
                status=ReceiptStatus.VERIFIED if changed else ReceiptStatus.REJECTED,
                provider_reference=job_id,
                result={"job_id": job_id, "state": updated["status"] if updated else "missing"},
                verification={"persisted": bool(changed)},
                error=None if changed else "Job was not cancellable",
            )
        return updated

    async def cancel_for_conversation(self, conversation_id: str) -> int:
        """Cancel all runnable jobs before their conversation is removed."""

        async with self._operation_lock:
            with self._db() as con:
                rows = con.execute(
                    """
                    SELECT job_id FROM followup_jobs
                    WHERE conversation_id=?
                    AND status IN ('pending','executing','delivery_pending','paused')
                    """,
                    (conversation_id,),
                ).fetchall()
                if rows:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='cancelled',cancelled_at=?,
                        delivery_state='cancelled',result_json=?
                        WHERE conversation_id=?
                        AND status IN ('pending','executing','delivery_pending','paused')
                        """,
                        (
                            self._iso(self._now()),
                            json.dumps(
                                {
                                    "cancelled": True,
                                    "reason": "conversation_deleted",
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            conversation_id,
                        ),
                    )
            return len(rows)

    async def pause(
        self,
        job_id: str,
        *,
        principal_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Pause a principal-owned monitor or recurrence at a durable boundary."""

        return await self._change_state(
            job_id,
            principal_id=principal_id,
            operation="pause",
            from_states={"pending"},
            to_state="paused",
            request_id=request_id,
        )

    async def resume(
        self,
        job_id: str,
        *,
        principal_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Resume principal-owned work without replaying a past occurrence."""

        job = await self.get(job_id, principal_id=principal_id)
        if job is None:
            return job
        if job["status"] == "pending":
            await self._recover_mutation_receipt(
                operation="resume",
                request_id=request_id,
                job=job,
                expected_state="pending",
            )
            return job
        if job["status"] != "paused":
            return job
        next_run = self._now()
        if job["kind"] == "recurring":
            next_value = next_recurrence(job.get("schedule") or {}, after_utc=self._now())
            if next_value is None:
                raise ValueError("The recurring schedule is no longer valid")
            next_run = next_value
        return await self._change_state(
            job_id,
            principal_id=principal_id,
            operation="resume",
            from_states={"paused"},
            to_state="pending",
            request_id=request_id,
            next_run_at=next_run,
        )

    async def _change_state(
        self,
        job_id: str,
        *,
        principal_id: str,
        operation: str,
        from_states: set[str],
        to_state: str,
        request_id: str | None,
        next_run_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        async with self._operation_lock:
            current = await self.get(job_id, principal_id=principal_id)
            if current is None:
                return current
            if current["status"] == to_state:
                await self._recover_mutation_receipt(
                    operation=operation,
                    request_id=request_id,
                    job=current,
                    expected_state=to_state,
                )
                return current
            if current["status"] not in from_states:
                return current
            receipt = await self._begin_mutation_receipt(
                operation=operation,
                job_id=job_id,
                principal_id=principal_id,
                conversation_id=str(current["conversation_id"]),
                request_id=str(request_id or uuid.uuid4()),
                payload={"to": to_state},
            )
            now = self._iso(self._now())
            with self._db() as con:
                changed = con.execute(
                    """
                    UPDATE followup_jobs SET status=?,updated_at=?,paused_at=?,
                    next_run_at=COALESCE(?,next_run_at)
                    WHERE job_id=? AND principal_id=? AND status=?
                    """,
                    (
                        to_state,
                        now,
                        now if to_state == "paused" else None,
                        self._iso(next_run_at) if next_run_at else None,
                        job_id,
                        principal_id,
                        current["status"],
                    ),
                ).rowcount
                if changed:
                    self._audit_sync(
                        con,
                        job_id=job_id,
                        principal_id=principal_id,
                        operation=operation,
                        state="verified",
                        evidence={"from": current["status"], "to": to_state},
                    )
            updated = await self.get(job_id, principal_id=principal_id)
            if receipt is not None and receipt.status is ReceiptStatus.STARTED:
                assert self.receipts is not None
                await self.receipts.complete(
                    receipt.action_id,
                    status=ReceiptStatus.VERIFIED if changed else ReceiptStatus.REJECTED,
                    provider_reference=job_id,
                    result={"job_id": job_id, "state": updated["status"] if updated else "missing"},
                    verification={"persisted": bool(changed)},
                    error=None if changed else "Job state changed concurrently",
                )
            return updated

    async def reschedule(
        self,
        job_id: str,
        *,
        principal_id: str,
        due_at: datetime,
        timezone_name: str,
        schedule: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Move pending work atomically so its old occurrence cannot execute."""

        if due_at.tzinfo is None:
            raise ValueError("The new schedule must include a timezone")
        if due_at <= self._now():
            raise ValueError("The new schedule must be in the future")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("The new schedule timezone is invalid") from exc
        async with self._operation_lock:
            current = await self.get(job_id, principal_id=principal_id)
            if current is None or current["status"] not in {"pending", "paused"}:
                return current
            if current["kind"] == "recurring" and schedule is None:
                schedule = current.get("schedule")
            schedule_json = (
                json.dumps(dict(schedule), separators=(",", ":"), sort_keys=True)
                if schedule is not None
                else None
            )
            receipt = await self._begin_mutation_receipt(
                operation="reschedule",
                job_id=job_id,
                principal_id=principal_id,
                conversation_id=str(current["conversation_id"]),
                request_id=str(request_id or uuid.uuid4()),
                payload={"due_at": self._iso(due_at), "schedule": schedule},
            )
            payload = dict(current["payload"])
            payload["timezone"] = timezone_name
            with self._db() as con:
                changed = con.execute(
                    """
                    UPDATE followup_jobs SET next_run_at=?,payload_json=?,schedule_json=?,
                    status='pending',paused_at=NULL,updated_at=?,attempts=0
                    WHERE job_id=? AND principal_id=? AND status IN ('pending','paused')
                    """,
                    (
                        self._iso(due_at),
                        json.dumps(payload, separators=(",", ":"), sort_keys=True),
                        schedule_json,
                        self._iso(self._now()),
                        job_id,
                        principal_id,
                    ),
                ).rowcount
                if changed:
                    self._audit_sync(
                        con,
                        job_id=job_id,
                        principal_id=principal_id,
                        operation="reschedule",
                        state="verified",
                        evidence={"next_run_at": self._iso(due_at)},
                    )
            updated = await self.get(job_id, principal_id=principal_id)
            if receipt is not None and receipt.status is ReceiptStatus.STARTED:
                assert self.receipts is not None
                await self.receipts.complete(
                    receipt.action_id,
                    status=ReceiptStatus.VERIFIED if changed else ReceiptStatus.REJECTED,
                    provider_reference=job_id,
                    result={"job_id": job_id, "state": updated["status"] if updated else "missing"},
                    verification={"persisted": bool(changed)},
                    error=None if changed else "Job changed concurrently",
                )
            return updated

    async def recent_completions(
        self, *, principal_id: str, limit: int = 20
    ) -> List[dict[str, Any]]:
        values = (principal_id, max(1, min(int(limit), 100)))
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs WHERE principal_id=?
                AND status IN ('completed','failed','expired')
                ORDER BY COALESCE(delivered_at,updated_at,created_at) DESC LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row(row) for row in rows]

    async def audit(self, job_id: str, *, principal_id: str) -> List[dict[str, Any]]:
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_job_audit
                WHERE job_id=? AND principal_id=? ORDER BY audit_id
                """,
                (job_id, principal_id),
            ).fetchall()
        output: List[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            output.append(item)
        return output

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        data["result"] = json.loads(data.pop("result_json") or "{}")
        data["schedule"] = json.loads(data.pop("schedule_json") or "null")
        data["last_observed_state"] = json.loads(data.pop("last_observed_state_json") or "null")
        return data

    async def start(self) -> None:
        if self._task is not None and self._task.done():
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="jarvis-followups")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def _database_is_healthy(self) -> bool:
        """Probe the durable store without reading user or job data."""

        try:
            with self._db() as con:
                table = con.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='followup_jobs'
                    """
                ).fetchone()
                integrity = con.execute("PRAGMA quick_check(1)").fetchone()
            return bool(
                table is not None and integrity is not None and str(integrity[0]).lower() == "ok"
            )
        except Exception:
            return False

    async def health_snapshot(self) -> dict[str, Any]:
        """Return redacted worker and durable-store health derived at runtime."""

        task = self._task
        if task is None:
            worker_state = "stopped"
        elif not task.done():
            worker_state = "running"
        elif task.cancelled():
            worker_state = "stopped"
        else:
            worker_state = "failed"
        worker_running = worker_state == "running"
        database_healthy = await asyncio.to_thread(self._database_is_healthy)
        healthy = worker_running and database_healthy

        if healthy:
            reason = None
        elif not database_healthy and not worker_running:
            reason = "Follow-up worker is stopped and durable storage is unavailable."
        elif not database_healthy:
            reason = "Durable follow-up storage is unavailable."
        elif worker_state == "failed":
            reason = "Follow-up worker stopped unexpectedly."
        else:
            reason = "Follow-up worker is not running."
        counts: dict[str, int] = {}
        next_row: sqlite3.Row | None = None
        if database_healthy:
            with self._db() as con:
                counts = {
                    str(row["status"]): int(row["count"])
                    for row in con.execute(
                        "SELECT status,COUNT(*) AS count FROM followup_jobs GROUP BY status"
                    ).fetchall()
                }
                next_row = con.execute(
                    """
                    SELECT next_run_at,kind FROM followup_jobs
                    WHERE status='pending' ORDER BY next_run_at LIMIT 1
                    """
                ).fetchone()
        return {
            "healthy": healthy,
            "status": "healthy" if healthy else "degraded",
            "worker_running": worker_running,
            "worker_state": worker_state,
            "database_healthy": database_healthy,
            "worker": {
                "running": worker_running,
                "state": worker_state,
            },
            "database": {"healthy": database_healthy},
            "jobs": {
                "active": sum(
                    counts.get(state, 0)
                    for state in (
                        "pending",
                        "executing",
                        "delivery_pending",
                        "delivering",
                        "paused",
                    )
                ),
                "by_status": counts,
                "next_run_at": str(next_row["next_run_at"]) if next_row else None,
                "next_kind": str(next_row["kind"]) if next_row else None,
            },
            "reason": reason,
        }

    async def diagnostics(self, *, principal_id: str) -> dict[str, Any]:
        """Return one principal's redaction-safe task diagnostics."""

        with self._db() as con:
            counts = {
                str(row["status"]): int(row["count"])
                for row in con.execute(
                    """
                    SELECT status,COUNT(*) AS count FROM followup_jobs
                    WHERE principal_id=? GROUP BY status
                    """,
                    (principal_id,),
                ).fetchall()
            }
            row = con.execute(
                """
                SELECT * FROM followup_jobs WHERE principal_id=? AND status='pending'
                ORDER BY next_run_at LIMIT 1
                """,
                (principal_id,),
            ).fetchone()
        next_job = self._row(row) if row else None
        return {
            "principal_id": principal_id,
            "active": sum(
                counts.get(state, 0)
                for state in (
                    "pending",
                    "executing",
                    "delivery_pending",
                    "delivering",
                    "paused",
                )
            ),
            "states": counts,
            "next": (
                {
                    key: next_job.get(key)
                    for key in (
                        "job_id",
                        "kind",
                        "capability_id",
                        "status",
                        "next_run_at",
                        "last_evaluated_at",
                        "delivery_state",
                        "notification_state",
                    )
                }
                if next_job
                else None
            ),
        }

    async def handle_command(
        self,
        text: str,
        *,
        principal_id: str,
        conversation_id: str,
        timezone_name: str,
        request_id: str | None = None,
        device_id: str | None = None,
        originating_endpoint: str | None = None,
    ) -> FollowUpCommandResult:
        """Handle deterministic personal-task language through this durable store."""

        if len(text) > 5_000:
            return FollowUpCommandResult(handled=False)
        value = " ".join(text.strip().rstrip(".!?").split())
        lowered = value.casefold()

        if re.fullmatch(
            r"(?:what reminders do i have|what (?:tasks|reminders) (?:are )?scheduled|"
            r"show (?:me )?my (?:tasks|reminders)|what are you monitoring for me)",
            lowered,
        ):
            active = await self._principal_active_jobs(principal_id)
            if "monitor" in lowered:
                active = [job for job in active if self._is_monitor(job)]
            if not active:
                noun = "monitors" if "monitor" in lowered else "scheduled tasks"
                return FollowUpCommandResult(True, response=f"You have no active {noun}.")
            descriptions = [self._describe_job(job, timezone_name) for job in active[:8]]
            return FollowUpCommandResult(
                True,
                response="Your active personal tasks are: " + "; ".join(descriptions) + ".",
                intent="personal_task_list",
                details={"jobs": active},
            )

        if re.fullmatch(r"what have you got scheduled for tomorrow", lowered):
            try:
                zone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                return FollowUpCommandResult(
                    True,
                    False,
                    "I couldn’t verify your timezone, so I didn’t guess which tasks are tomorrow.",
                    "personal_task_list",
                )
            tomorrow = self._now().astimezone(zone).date() + timedelta(days=1)
            jobs = [
                job
                for job in await self._principal_active_jobs(principal_id)
                if datetime.fromisoformat(str(job["next_run_at"])).astimezone(zone).date()
                == tomorrow
            ]
            if not jobs:
                return FollowUpCommandResult(
                    True, response="You have nothing scheduled for tomorrow."
                )
            return FollowUpCommandResult(
                True,
                response="Tomorrow: "
                + "; ".join(self._describe_job(job, timezone_name) for job in jobs)
                + ".",
                intent="personal_task_list",
                details={"jobs": jobs},
            )

        management = re.match(
            r"^(cancel|pause|resume)\s+(?:my\s+)?(.+?)(?:\s+(?:reminder|monitor|task))?$",
            lowered,
        )
        if management:
            operation, reference = management.group(1), management.group(2).strip()
            candidates = await self._resolve_job_reference(
                reference,
                principal_id=principal_id,
                conversation_id=conversation_id,
                monitors_only=operation in {"pause", "resume"},
            )
            if len(candidates) != 1:
                response = (
                    "I couldn’t find that active task."
                    if not candidates
                    else "More than one task matches. Please name it more specifically."
                )
                return FollowUpCommandResult(True, False, response, "personal_task_manage")
            job = candidates[0]
            if operation == "cancel":
                updated = await self.cancel(
                    str(job["job_id"]),
                    principal_id=principal_id,
                    request_id=request_id,
                )
            elif operation == "pause":
                updated = await self.pause(
                    str(job["job_id"]),
                    principal_id=principal_id,
                    request_id=request_id,
                )
            else:
                updated = await self.resume(
                    str(job["job_id"]),
                    principal_id=principal_id,
                    request_id=request_id,
                )
            changed = (
                updated is not None
                and str(updated["status"])
                == {
                    "cancel": "cancelled",
                    "pause": "paused",
                    "resume": "pending",
                }[operation]
            )
            return FollowUpCommandResult(
                True,
                changed,
                (
                    f"{operation.title()}d task {str(job['job_id'])[:8]}."
                    if changed
                    else f"I couldn’t {operation} that task in its current state."
                ),
                "personal_task_manage",
                {"job": updated},
            )

        reschedule_match = re.match(
            r"^(?:move|change|reschedule)\s+(.+?)\s+(?:to|for)\s+(.+)$", lowered
        )
        if reschedule_match:
            reference, timing = reschedule_match.groups()
            reference = re.sub(r"^(?:that|the|my)\s+", "", reference).strip()
            reference = re.sub(r"\s+(?:reminder|task)$", "", reference).strip() or "that"
            candidates = await self._resolve_job_reference(
                reference,
                principal_id=principal_id,
                conversation_id=conversation_id,
            )
            if len(candidates) != 1:
                return FollowUpCommandResult(
                    True,
                    False,
                    "I need one unambiguous pending task before I can reschedule it.",
                    "personal_task_reschedule",
                )
            if re.fullmatch(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)", timing):
                timing = "at " + timing
            resolved = resolve_schedule(
                f"remind me {timing}", timezone_name=timezone_name, now_utc=self._now()
            )
            if resolved is None:
                return FollowUpCommandResult(
                    True,
                    False,
                    "I couldn’t resolve the new date and time, so the existing schedule is unchanged.",
                    "personal_task_reschedule",
                )
            updated = await self.reschedule(
                str(candidates[0]["job_id"]),
                principal_id=principal_id,
                due_at=resolved.due_utc,
                timezone_name=timezone_name,
                request_id=request_id,
            )
            return FollowUpCommandResult(
                True,
                updated is not None,
                (
                    f"Moved task {str(candidates[0]['job_id'])[:8]} to {resolved.description}."
                    if updated is not None
                    else "That task could not be rescheduled."
                ),
                "personal_task_reschedule",
                {"job": updated},
            )

        periodic_monitor = await self._try_create_periodic_monitor(
            value,
            principal_id=principal_id,
            conversation_id=conversation_id,
            request_id=request_id,
            device_id=device_id,
            originating_endpoint=originating_endpoint,
        )
        if periodic_monitor is not None:
            return periodic_monitor

        recurrence = resolve_recurrence(value, timezone_name=timezone_name, now_utc=self._now())
        if recurrence is not None:
            schedule, due_at, message = recurrence
            try:
                job = await self.create(
                    conversation_id=conversation_id,
                    kind="recurring",
                    payload={
                        "message": message,
                        "timezone": timezone_name,
                        "notify": True,
                    },
                    due_at=due_at,
                    idempotency_key=request_id,
                    principal_id=principal_id,
                    device_id=device_id,
                    originating_endpoint=originating_endpoint,
                    capability_id="personal.reminder",
                    schedule=schedule.as_dict(),
                )
            except (RuntimeError, sqlite3.Error):
                return self._persistence_failure("recurring task")
            return FollowUpCommandResult(
                True,
                response=(
                    f"Scheduled task {str(job['job_id'])[:8]} {schedule.description}; "
                    f"the next occurrence is {self._local_due(job, timezone_name)}."
                ),
                intent="personal_task_recurring_create",
                details={"job": job},
            )

        condition = await self._try_create_condition(
            value,
            principal_id=principal_id,
            conversation_id=conversation_id,
            request_id=request_id,
            device_id=device_id,
            originating_endpoint=originating_endpoint,
        )
        if condition is not None:
            return condition

        resolved = resolve_schedule(value, timezone_name=timezone_name, now_utc=self._now())
        if resolved is not None:
            try:
                job = await self.create(
                    conversation_id=conversation_id,
                    kind="scheduled",
                    payload={
                        "message": resolved.reminder_text,
                        "timezone": timezone_name,
                        "notify": True,
                    },
                    due_at=resolved.due_utc,
                    idempotency_key=request_id,
                    principal_id=principal_id,
                    device_id=device_id,
                    originating_endpoint=originating_endpoint,
                    capability_id="personal.reminder",
                )
            except (RuntimeError, sqlite3.Error):
                return self._persistence_failure("reminder")
            return FollowUpCommandResult(
                True,
                response=(f"Scheduled task {str(job['job_id'])[:8]} for {resolved.description}."),
                intent="personal_task_reminder_create",
                details={"job": job},
            )
        if lowered.startswith("remind me") or (
            lowered.startswith("every ") and " remind me " in lowered
        ):
            return FollowUpCommandResult(
                True,
                False,
                "I couldn’t resolve a complete future schedule, so no task was created.",
                "personal_task_invalid_schedule",
            )
        return FollowUpCommandResult(handled=False)

    @staticmethod
    def _persistence_failure(noun: str) -> FollowUpCommandResult:
        return FollowUpCommandResult(
            True,
            False,
            f"I could not durably save that {noun}, so it was not created.",
            "personal_task_persistence_failed",
        )

    async def _principal_active_jobs(self, principal_id: str) -> List[dict[str, Any]]:
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs WHERE principal_id=?
                AND status IN ('pending','executing','delivery_pending','delivering','paused')
                ORDER BY next_run_at,created_at
                """,
                (principal_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _is_monitor(job: Mapping[str, Any]) -> bool:
        return str(job.get("kind")) in {"condition", "periodic", "external_monitor"}

    async def _resolve_job_reference(
        self,
        reference: str,
        *,
        principal_id: str,
        conversation_id: str,
        monitors_only: bool = False,
    ) -> List[dict[str, Any]]:
        jobs = await self._principal_active_jobs(principal_id)
        if monitors_only:
            jobs = [job for job in jobs if self._is_monitor(job)]
        reference = reference.strip().casefold()
        if reference in {"that", "it", "last", "latest"}:
            return [job for job in jobs if str(job.get("conversation_id")) == conversation_id]
        identifier = re.fullmatch(r"(?:job\s+)?([0-9a-f]{4,36})", reference)
        if identifier:
            prefix = identifier.group(1)
            return [job for job in jobs if str(job["job_id"]).startswith(prefix)]
        terms = [term for term in re.findall(r"[a-z0-9_]+", reference) if len(term) > 2]
        return [
            job
            for job in jobs
            if all(
                term
                in " ".join(
                    [
                        str(job.get("kind") or ""),
                        str((job.get("payload") or {}).get("message") or ""),
                        str((job.get("payload") or {}).get("entity_id") or ""),
                    ]
                ).casefold()
                for term in terms
            )
        ]

    async def _try_create_condition(
        self,
        text: str,
        *,
        principal_id: str,
        conversation_id: str,
        request_id: str | None,
        device_id: str | None,
        originating_endpoint: str | None,
    ) -> FollowUpCommandResult | None:
        lowered = text.casefold()
        prefix = re.match(r"^(?:tell me|let me know)\s+(?:if|when)\s+(.+)$", lowered)
        if prefix is None:
            return None
        condition_text = prefix.group(1).strip()
        comparison, wanted = "equals", "on"
        if condition_text.endswith(" changes") or condition_text.endswith(" changes state"):
            query = re.sub(r"\s+changes(?: state)?$", "", condition_text).strip()
            comparison, wanted = "changed", ""
        elif condition_text.endswith(" comes back online"):
            query = condition_text[: -len(" comes back online")].strip()
            comparison, wanted = "not_equals", "unavailable"
        elif condition_text.endswith(" gets home"):
            query = condition_text[: -len(" gets home")].strip()
            wanted = "home"
        elif condition_text.endswith(" finishes"):
            query = condition_text[: -len(" finishes")].strip()
            wanted = "completed"
        elif condition_text.endswith(" detects a person"):
            query = condition_text[: -len(" detects a person")].strip()
            wanted = "on"
        else:
            state_match = re.match(
                r"^(.+?)\s+(?:is|becomes|reports)\s+([a-z0-9_-]+)$", condition_text
            )
            if state_match is None:
                return FollowUpCommandResult(
                    True,
                    False,
                    "I couldn’t resolve that condition to verified capability evidence, so I did not create a monitor.",
                    "personal_monitor_create",
                )
            query, wanted = state_match.groups()
        try:
            states = await self.states.readable_entity_states(refresh=True)
        except Exception:
            return FollowUpCommandResult(
                True,
                False,
                "Home Assistant is unavailable, so I did not create that monitor.",
                "personal_monitor_create",
            )
        matches = self._match_entities(query, states)
        if len(matches) != 1:
            return FollowUpCommandResult(
                True,
                False,
                (
                    "I couldn’t find a current Home Assistant entity for that condition."
                    if not matches
                    else "More than one Home Assistant entity matches; please name the exact device."
                ),
                "personal_monitor_create",
            )
        entity = matches[0]
        entity_id = str(entity.get("entity_id") or "")
        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "comparison": comparison,
            "baseline": str(entity.get("state") or "unknown"),
            "message": f"{entity.get('name') or entity_id} now satisfies your requested condition.",
            "notify": True,
        }
        if comparison != "changed":
            payload["state"] = wanted
        if condition_text.endswith(" finishes"):
            payload["states"] = ["completed", "finished"]
        try:
            job = await self.create(
                conversation_id=conversation_id,
                kind="condition",
                payload=payload,
                due_at=self._now(),
                idempotency_key=request_id,
                principal_id=principal_id,
                device_id=device_id,
                originating_endpoint=originating_endpoint,
                capability_id="home_assistant.read_state",
            )
        except (RuntimeError, sqlite3.Error):
            return self._persistence_failure("monitor")
        return FollowUpCommandResult(
            True,
            response=(
                f"Monitoring {entity.get('name') or entity_id} as task "
                f"{str(job['job_id'])[:8]}; I’ll report verified evidence here."
            ),
            intent="personal_monitor_create",
            details={"job": job},
        )

    async def _try_create_periodic_monitor(
        self,
        text: str,
        *,
        principal_id: str,
        conversation_id: str,
        request_id: str | None,
        device_id: str | None,
        originating_endpoint: str | None,
    ) -> FollowUpCommandResult | None:
        lowered = text.casefold()
        timed = re.match(
            r"^every\s+(\d{1,4})\s+(minutes?|hours?)\s+check\s+"
            r"(?:whether|if)\s+(.+?)(?:\s+has)?\s+changed$",
            lowered,
        )
        interval_seconds = 3600
        query: str | None = None
        if timed:
            amount = int(timed.group(1))
            interval_seconds = amount * (3600 if timed.group(2).startswith("hour") else 60)
            query = timed.group(3).strip()
        else:
            continuous = re.match(
                r"^keep checking\s+(.+?)(?:\s+and\s+(?:tell|let)\s+me\s+"
                r"when\s+it\s+changes)?$",
                lowered,
            )
            if continuous:
                query = continuous.group(1).strip()
        if query is None:
            return None
        if not 60 <= interval_seconds <= 30 * 86400:
            return FollowUpCommandResult(
                True,
                False,
                "That monitoring interval is outside the supported safe range, so no monitor was created.",
                "personal_monitor_create",
            )
        try:
            states = await self.states.readable_entity_states(refresh=True)
        except Exception:
            return FollowUpCommandResult(
                True,
                False,
                "Home Assistant is unavailable, so I did not create that monitor.",
                "personal_monitor_create",
            )
        matches = self._match_entities(query, states)
        if len(matches) != 1:
            if not matches:
                # A generic page/provider monitor belongs to the established
                # External Agent path, which performs its own availability,
                # read-only capability, baseline, and polling-policy checks.
                return None
            return FollowUpCommandResult(
                True,
                False,
                "More than one Home Assistant entity matches; please name the exact device.",
                "personal_monitor_create",
            )
        entity = matches[0]
        entity_id = str(entity.get("entity_id") or "")
        try:
            job = await self.create(
                conversation_id=conversation_id,
                kind="periodic",
                payload={
                    "entity_id": entity_id,
                    "comparison": "changed",
                    "baseline": str(entity.get("state") or "unknown"),
                    "interval_seconds": interval_seconds,
                    "message": f"{entity.get('name') or entity_id} changed.",
                    "notify": True,
                },
                due_at=self._now() + timedelta(seconds=interval_seconds),
                idempotency_key=request_id,
                principal_id=principal_id,
                device_id=device_id,
                originating_endpoint=originating_endpoint,
                capability_id="home_assistant.read_state",
            )
        except (RuntimeError, sqlite3.Error):
            return self._persistence_failure("monitor")
        return FollowUpCommandResult(
            True,
            response=(
                f"Monitoring {entity.get('name') or entity_id} every "
                f"{interval_seconds // 60} minutes as task {str(job['job_id'])[:8]}."
            ),
            intent="personal_monitor_create",
            details={"job": job},
        )

    @staticmethod
    def _match_entities(query: str, states: List[dict[str, Any]]) -> List[dict[str, Any]]:
        terms = [
            term
            for term in re.findall(r"[a-z0-9]+", query.casefold())
            if term not in {"a", "an", "the", "this", "that", "device", "camera"}
        ]
        normalised = " ".join(terms)
        if not normalised:
            return []
        ranked: List[tuple[int, dict[str, Any]]] = []
        for entity in states:
            entity_id = str(entity.get("entity_id") or "").casefold()
            name = str(entity.get("name") or "").casefold()
            searchable = " ".join(re.findall(r"[a-z0-9]+", f"{entity_id} {name}"))
            score = 0
            if query.casefold() == entity_id:
                score = 200
            elif normalised == " ".join(re.findall(r"[a-z0-9]+", name)):
                score = 180
            elif all(term in searchable for term in normalised.split()):
                score = 100 + len(normalised)
            if score:
                ranked.append((score, entity))
        if not ranked:
            return []
        highest = max(score for score, _ in ranked)
        return [entity for score, entity in ranked if score == highest]

    def _describe_job(self, job: Mapping[str, Any], timezone_name: str) -> str:
        payload = job.get("payload") or {}
        message = str(payload.get("message") or job.get("kind") or "task").rstrip(".")
        return (
            f"{str(job['job_id'])[:8]} ({job['status']}): {message}, "
            f"next {self._local_due(job, timezone_name)}"
        )

    @staticmethod
    def _local_due(job: Mapping[str, Any], timezone_name: str) -> str:
        try:
            zone = ZoneInfo(timezone_name)
            due = datetime.fromisoformat(str(job["next_run_at"])).astimezone(zone)
        except (ValueError, ZoneInfoNotFoundError):
            return str(job.get("next_run_at") or "unknown")
        return due.strftime("%A %d %B at %H:%M %Z")

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            await asyncio.sleep(self.poll_seconds)

    async def run_once(self) -> None:
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs
                WHERE status IN ('pending','delivery_pending') AND next_run_at<=?
                ORDER BY next_run_at LIMIT 20
                """,
                (self._iso(self._now()),),
            ).fetchall()
        for row in rows:
            await self._execute(self._row(row))

    async def _execute(self, job: dict[str, Any]) -> None:
        async with self._operation_lock:
            await self._execute_locked(job)

    async def _execute_locked(self, job: dict[str, Any]) -> None:
        if job["status"] == "delivery_pending":
            await self._deliver_pending(job)
            return
        with self._db() as con:
            claimed = con.execute(
                "UPDATE followup_jobs SET status='executing' WHERE job_id=? AND status='pending'",
                (job["job_id"],),
            ).rowcount
        if not claimed:
            return
        with self._db() as con:
            con.execute(
                "UPDATE followup_jobs SET last_evaluated_at=?,updated_at=? "
                "WHERE job_id=? AND status='executing'",
                (self._iso(self._now()), self._iso(self._now()), job["job_id"]),
            )
        if job["kind"] == "external_monitor":
            terminal_reason = self._external_monitor_terminal_reason(job)
            if terminal_reason is not None:
                if (
                    terminal_reason == "expires_at"
                    and job["payload"].get("notify_if_unchanged") is True
                ):
                    try:
                        done, message, result = await self._evaluate(job)
                    except Exception as exc:
                        await self._handle_evaluation_failure(job, exc)
                        return
                    if done:
                        queued = self._queue_delivery(
                            job["job_id"],
                            message,
                            result,
                            completion_status="completed",
                        )
                        if queued is not None:
                            await self._deliver_pending(queued)
                    else:
                        await self._expire_external_monitor(
                            job,
                            terminal_reason,
                            result=result,
                        )
                else:
                    await self._expire_external_monitor(job, terminal_reason)
                return
            with self._db() as con:
                incremented = con.execute(
                    """
                    UPDATE followup_jobs SET poll_count=poll_count+1
                    WHERE job_id=? AND status='executing'
                    """,
                    (job["job_id"],),
                ).rowcount
            if not incremented:
                return
            job = dict(job)
            job["poll_count"] = int(job.get("poll_count") or 0) + 1
        try:
            done, message, result = await self._evaluate(job)
        except Exception as exc:
            await self._handle_evaluation_failure(job, exc)
            return

        observed = result.get("state", result.get("value"))
        with self._db() as con:
            con.execute(
                """
                UPDATE followup_jobs SET last_observed_state_json=?,updated_at=?
                WHERE job_id=? AND status='executing'
                """,
                (
                    json.dumps(observed, separators=(",", ":"), sort_keys=True),
                    self._iso(self._now()),
                    job["job_id"],
                ),
            )

        if not done:
            if job["kind"] == "external_monitor":
                terminal_reason = self._external_monitor_terminal_reason(job)
                if terminal_reason is not None:
                    await self._expire_external_monitor(job, terminal_reason, result=result)
                    return
            with self._db() as con:
                con.execute(
                    """
                    UPDATE followup_jobs SET status='pending',next_run_at=?,
                    result_json=?,attempts=0,updated_at=?
                    WHERE job_id=? AND status='executing'
                    """,
                    (
                        self._iso(self._now() + timedelta(seconds=self._next_interval(job))),
                        json.dumps(result, separators=(",", ":"), sort_keys=True),
                        self._iso(self._now()),
                        job["job_id"],
                    ),
                )
            return
        queued = self._queue_delivery(job["job_id"], message, result, completion_status="completed")
        if queued is not None:
            await self._deliver_pending(queued)

    def _external_monitor_terminal_reason(self, job: Mapping[str, Any]) -> str | None:
        expires_at = job.get("expires_at")
        if expires_at:
            try:
                expired = self._parse_expiry(str(expires_at)) <= self._now()
            except ValueError:
                expired = True
            if expired:
                return "expires_at"
        max_polls = job.get("max_polls")
        if max_polls is not None and int(job.get("poll_count") or 0) >= int(max_polls):
            return "max_polls"
        return None

    async def _expire_external_monitor(
        self,
        job: Mapping[str, Any],
        reason: str,
        *,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        capability_id = str(job["payload"].get("capability_id") or "monitor")
        if reason == "expires_at" and job["payload"].get("notify_if_unchanged") is True:
            message = (
                f"I did not observe a verified change for {capability_id} before "
                "the requested deadline."
            )
        elif reason == "max_polls":
            message = (
                f"I stopped monitoring {capability_id} after its configured poll "
                "limit was reached without a verified change."
            )
        else:
            message = (
                f"I stopped monitoring {capability_id} because its configured "
                "expiry time was reached without a verified change."
            )
        terminal_result = dict(result or {})
        terminal_result.update(
            {
                "verified": bool(result is not None and result.get("verified") is True),
                "changed": False,
                "terminal_reason": reason,
                "poll_count": int(job.get("poll_count") or 0),
                "max_polls": job.get("max_polls"),
                "expires_at": job.get("expires_at"),
            }
        )
        queued = self._queue_delivery(
            str(job["job_id"]),
            message,
            terminal_result,
            completion_status="expired",
        )
        if queued is not None:
            await self._deliver_pending(queued)

    def _next_interval(self, job: Mapping[str, Any]) -> int:
        payload = job["payload"]
        if job["kind"] == "external_monitor":
            return int(payload["polling_interval_seconds"])
        if job["kind"] == "periodic":
            return max(10, min(int(payload.get("interval_seconds") or 60), 86400))
        return min(60, self.poll_seconds * 2)

    async def _handle_evaluation_failure(self, job: dict[str, Any], exc: Exception) -> None:
        attempts = int(job["attempts"]) + 1
        maximum = int(job.get("max_attempts") or self.max_attempts)
        result = {
            "error": "Follow-up evaluation failed",
            "error_type": type(exc).__name__,
            "verified": False,
        }
        if job["kind"] == "external_monitor":
            result.update(
                error="External monitor evaluation failed",
                provider=job["payload"].get("provider"),
                capability_id=job["payload"].get("capability_id"),
                changed=False,
            )
        if attempts < maximum:
            with self._db() as con:
                con.execute(
                    """
                    UPDATE followup_jobs SET status='pending',attempts=?,
                    next_run_at=?,result_json=? WHERE job_id=? AND status='executing'
                    """,
                    (
                        attempts,
                        self._iso(
                            self._now()
                            + timedelta(seconds=min(3600, 2**attempts * self.poll_seconds))
                        ),
                        json.dumps(result, separators=(",", ":"), sort_keys=True),
                        job["job_id"],
                    ),
                )
            return

        message = (
            "I stopped that external monitor after repeated evaluation failures. "
            "I did not verify a change."
            if job["kind"] == "external_monitor"
            else "I couldn't complete that follow-up because the required service was unavailable."
        )
        with self._db() as con:
            updated = con.execute(
                """
                UPDATE followup_jobs SET status='delivery_pending',attempts=?,
                result_json=?,delivery_message=?,completion_status='failed',
                delivery_state='pending',next_run_at=?
                WHERE job_id=? AND status='executing'
                """,
                (
                    attempts,
                    json.dumps(result, separators=(",", ":"), sort_keys=True),
                    message,
                    self._iso(self._now()),
                    job["job_id"],
                ),
            ).rowcount
        if updated:
            pending = await self.get(job["job_id"])
            if pending is not None:
                await self._deliver_pending(pending)

    def _queue_delivery(
        self,
        job_id: str,
        message: str,
        result: Mapping[str, Any],
        *,
        completion_status: str,
    ) -> dict[str, Any] | None:
        if completion_status not in {"completed", "failed", "expired"}:
            raise ValueError("Invalid follow-up completion status")
        message = str(message).strip()
        if not message:
            raise ValueError("Follow-up delivery message cannot be empty")
        result_json = json.dumps(dict(result), separators=(",", ":"), sort_keys=True)
        with self._db() as con:
            updated = con.execute(
                """
                UPDATE followup_jobs SET status='delivery_pending',result_json=?,
                delivery_message=?,completion_status=?,delivery_state='pending',
                next_run_at=? WHERE job_id=? AND status='executing'
                """,
                (
                    result_json,
                    message,
                    completion_status,
                    self._iso(self._now()),
                    job_id,
                ),
            ).rowcount
            row = (
                con.execute("SELECT * FROM followup_jobs WHERE job_id=?", (job_id,)).fetchone()
                if updated
                else None
            )
        return self._row(row) if row is not None else None

    async def _deliver_pending(self, job: dict[str, Any]) -> None:
        with self._db() as con:
            claimed = con.execute(
                "UPDATE followup_jobs SET status='delivering',"
                "delivery_state='delivering' WHERE job_id=? "
                "AND status='delivery_pending'",
                (job["job_id"],),
            ).rowcount
        if not claimed:
            return
        try:
            await self.conversations.add_assistant_message(
                str(job["conversation_id"]),
                str(job.get("delivery_message") or ""),
                delivery_key=(
                    f"followup:{job['job_id']}:occurrence:{job.get('occurrence_index', 0)}"
                    if job.get("kind") == "recurring"
                    else f"followup:{job['job_id']}:delivery"
                ),
            )
        except Exception as exc:
            attempts = int(job.get("delivery_attempts") or 0) + 1
            maximum = int(job.get("max_attempts") or self.max_attempts)
            result = dict(job.get("result") or {})
            result["delivery_error"] = "Conversation delivery failed"
            result["delivery_error_type"] = type(exc).__name__
            with self._db() as con:
                if attempts >= maximum:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='failed',
                        delivery_state='failed',delivery_attempts=?,result_json=?
                        WHERE job_id=? AND status='delivering'
                        """,
                        (
                            attempts,
                            json.dumps(result, separators=(",", ":"), sort_keys=True),
                            job["job_id"],
                        ),
                    )
                else:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='delivery_pending',
                        delivery_state='pending',delivery_attempts=?,result_json=?,
                        next_run_at=? WHERE job_id=? AND status='delivering'
                        """,
                        (
                            attempts,
                            json.dumps(result, separators=(",", ":"), sort_keys=True),
                            self._iso(
                                self._now()
                                + timedelta(seconds=min(3600, 2**attempts * self.poll_seconds))
                            ),
                            job["job_id"],
                        ),
                    )
            return

        notification_state = "not_requested"
        payload = job.get("payload") or {}
        if self.notifier is not None and payload.get("notify") is True:
            if str(job.get("notification_state") or "") == "attempting":
                notification_state = "outcome_unknown"
            else:
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET notification_state='attempting',updated_at=? "
                        "WHERE job_id=? AND status='delivering'",
                        (self._iso(self._now()), job["job_id"]),
                    )
                try:
                    notification = await self.notifier(
                        str(job.get("principal_id") or ""),
                        str(job.get("delivery_message") or ""),
                        "Jarvis reminder",
                    )
                    if bool(notification.get("success")) or bool(
                        notification.get("command_accepted")
                    ):
                        notification_state = "accepted_unverified"
                    elif bool(notification.get("command_sent")):
                        notification_state = "partially_accepted"
                    else:
                        notification_state = "failed"
                except Exception:
                    notification_state = "failed"

        completion = str(job.get("completion_status") or "failed")
        if completion not in {"completed", "failed", "expired"}:
            completion = "failed"
        with self._db() as con:
            now = self._now()
            if job.get("kind") == "recurring" and completion == "completed":
                next_run = next_recurrence(job.get("schedule") or {}, after_utc=now)
                if next_run is None:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='failed',delivered_at=?,
                        delivery_state='delivered',notification_state=?,updated_at=?
                        WHERE job_id=? AND status='delivering'
                        """,
                        (
                            self._iso(now),
                            notification_state,
                            self._iso(now),
                            job["job_id"],
                        ),
                    )
                else:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='pending',next_run_at=?,
                        delivered_at=?,delivery_state='pending',delivery_message=NULL,
                        completion_status=NULL,occurrence_index=occurrence_index+1,
                        notification_state=?,verified_at=?,updated_at=?,attempts=0
                        WHERE job_id=? AND status='delivering'
                        """,
                        (
                            self._iso(next_run),
                            self._iso(now),
                            notification_state,
                            self._iso(now),
                            self._iso(now),
                            job["job_id"],
                        ),
                    )
            else:
                con.execute(
                    """
                    UPDATE followup_jobs SET status=?,delivered_at=?,
                    delivery_state='delivered',notification_state=?,verified_at=?,updated_at=?
                    WHERE job_id=? AND status='delivering'
                    """,
                    (
                        completion,
                        self._iso(now),
                        notification_state,
                        self._iso(now) if completion == "completed" else None,
                        self._iso(now),
                        job["job_id"],
                    ),
                )
            self._audit_sync(
                con,
                job_id=str(job["job_id"]),
                principal_id=str(job.get("principal_id") or "aaron"),
                operation="deliver",
                state=completion,
                evidence={
                    "conversation_delivery": "delivered",
                    "notification_state": notification_state,
                    "verified": completion == "completed",
                    "occurrence_index": int(job.get("occurrence_index") or 0),
                },
            )

    async def _evaluate(self, job: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        payload, kind = job["payload"], job["kind"]
        if kind == "external_monitor":
            return await self._evaluate_external_monitor(job)
        if kind in {"time", "scheduled", "recurring"}:
            return (
                True,
                str(payload.get("message") or "Your requested follow-up is due."),
                {"verified": True},
            )
        if kind == "completion" and payload.get("source_type") == "followup_job":
            source = await self.get(str(payload.get("source_job_id") or ""))
            if source is None:
                raise RuntimeError("The referenced job no longer exists")
            status = str(source.get("status") or "unknown")
            if status == "failed":
                return (
                    True,
                    "The job I was watching failed, so it did not complete successfully.",
                    {
                        "source_job_id": source["job_id"],
                        "state": status,
                        "verified": True,
                    },
                )
            if status in {"cancelled", "expired"}:
                return (
                    True,
                    "The job I was watching ended without completing successfully.",
                    {
                        "source_job_id": source["job_id"],
                        "state": status,
                        "verified": True,
                    },
                )
            return (
                status == "completed",
                str(payload.get("message") or "The job I was watching has finished."),
                {
                    "source_job_id": source["job_id"],
                    "state": status,
                    "verified": status == "completed",
                },
            )

        entity_id = str(payload.get("entity_id") or "")
        states = {
            str(item.get("entity_id")): item
            for item in await self.states.readable_entity_states(refresh=True)
        }
        entity = states.get(entity_id)
        if entity is None:
            raise RuntimeError("The monitored Home Assistant entity is no longer available")
        state = str(entity.get("state") or "unknown")
        if kind in {"condition", "completion"}:
            if payload.get("comparison") == "changed":
                changed = state != str(payload.get("baseline") or "")
                return (
                    changed,
                    str(
                        payload.get("message")
                        or f"{entity.get('name') or entity_id} changed to {state}."
                    ),
                    {"entity_id": entity_id, "state": state, "verified": changed},
                )
            wanted = str(payload.get("state") or "on")
            wanted_states = {
                str(item) for item in payload.get("states") or [wanted] if str(item).strip()
            }
            if payload.get("comparison") == "not_equals":
                verified = state != wanted
                return (
                    verified,
                    str(
                        payload.get("message")
                        or f"{entity.get('name') or entity_id} is now {state}."
                    ),
                    {
                        "entity_id": entity_id,
                        "state": state,
                        "comparison": "not_equals",
                        "target": wanted,
                        "verified": verified,
                    },
                )
            matches = state in wanted_states
            return (
                matches,
                str(payload.get("message") or f"{entity.get('name') or entity_id} is now {state}."),
                {
                    "entity_id": entity_id,
                    "state": state,
                    "target_states": sorted(wanted_states),
                    "verified": matches,
                },
            )
        baseline = str(payload.get("baseline") or "")
        return (
            state != baseline,
            str(payload.get("message") or f"{entity.get('name') or entity_id} changed to {state}."),
            {
                "entity_id": entity_id,
                "state": state,
                "verified": state != baseline,
            },
        )

    async def _evaluate_external_monitor(
        self, job: Mapping[str, Any]
    ) -> tuple[bool, str, dict[str, Any]]:
        evaluator = self.external_evaluator
        if evaluator is None:
            raise RuntimeError("External monitor evaluator is not configured")
        monitor = dict(job["payload"])
        monitor.update(
            job_id=job["job_id"],
            attempt=int(job.get("attempts") or 0) + 1,
        )
        callback: Callable[[Mapping[str, Any]], Any]
        if hasattr(evaluator, "evaluate_external_monitor"):
            callback = evaluator.evaluate_external_monitor  # type: ignore[union-attr]
        elif hasattr(evaluator, "evaluate"):
            callback = evaluator.evaluate  # type: ignore[union-attr]
        elif callable(evaluator):
            callback = evaluator
        else:
            raise RuntimeError("External monitor evaluator is invalid")

        evaluation = callback(monitor)
        if inspect.isawaitable(evaluation):
            evaluation = await evaluation
        if isinstance(evaluation, ExternalMonitorEvaluation):
            data = asdict(evaluation)
        elif isinstance(evaluation, Mapping):
            data = dict(evaluation)
        else:
            raise RuntimeError("External monitor returned a malformed evaluation")
        if data.get("verified") is not True:
            raise RuntimeError("External monitor result was not verified")

        if data.get("changed") is not None:
            raise RuntimeError(
                "External monitor evaluators must return observations, not decisions"
            )
        value_present = any(key in data for key in ("value", "current", "observation"))
        value = data.get("value", data.get("current", data.get("observation")))
        if not value_present:
            raise RuntimeError("External monitor must return an observed value")
        if redact_secrets(value) != value:
            raise RuntimeError("External monitor observation contained credential material")
        changed = self._compare_external_values(
            job["payload"]["baseline"],
            value,
            job["payload"]["comparison"],
        )

        observed_at = data.get("observed_at")
        if observed_at is None:
            evaluated_at = self._iso(self._now())
        else:
            try:
                evaluated_at = self._iso(self._parse_expiry(observed_at))
            except ValueError as exc:
                raise RuntimeError(
                    "External monitor returned a malformed observation timestamp"
                ) from exc

        result: dict[str, Any] = {
            "provider": job["payload"]["provider"],
            "capability_id": job["payload"]["capability_id"],
            "verified": True,
            "changed": changed,
            "comparison_operator": self._comparison_operator(job["payload"]["comparison"]),
            "baseline": job["payload"]["baseline"],
            "poll_count": int(job.get("poll_count") or 0),
            "evaluated_at": evaluated_at,
        }
        if value_present:
            result["value"] = value
        if data.get("provider_reference") is not None:
            result["provider_reference"] = redact_text(data["provider_reference"], max_length=500)
        try:
            json.dumps(result, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("External monitor returned a non-serializable result") from exc

        # Evaluators supply observations, never user-facing claims.  The worker
        # binds its message to the values that its own comparison verified.
        message = self._external_monitor_change_message(
            job["payload"]["baseline"],
            value,
            job["payload"]["comparison"],
        )
        return changed, message, result

    @classmethod
    def _comparison_operator(cls, comparison: Any) -> str:
        if isinstance(comparison, str):
            operator = comparison.strip().lower()
        elif isinstance(comparison, Mapping):
            if (
                comparison.get("operator") is not None
                and comparison.get("type") is not None
                and str(comparison["operator"]).strip().lower()
                != str(comparison["type"]).strip().lower()
            ):
                raise ValueError("External monitor comparison operators conflict")
            operator = (
                str(comparison.get("operator") or comparison.get("type") or "").strip().lower()
            )
        else:
            raise ValueError("External monitor comparison is malformed")
        if operator not in VALID_EXTERNAL_COMPARISONS:
            raise ValueError(f"Unsupported external monitor comparison: {operator or 'missing'}")
        return operator

    @staticmethod
    def _is_number(value: Any) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            or isinstance(value, float)
            and math.isfinite(value)
        )

    @classmethod
    def _comparison_target(cls, comparison: Any) -> Any:
        if not isinstance(comparison, Mapping):
            raise ValueError("External monitor comparison requires an explicit target")
        target_keys = [key for key in ("target", "expected", "value") if key in comparison]
        if len(target_keys) != 1:
            raise ValueError("External monitor comparison requires exactly one explicit target")
        return comparison[target_keys[0]]

    @classmethod
    def _validate_external_comparison(cls, baseline: Any, comparison: Any) -> None:
        operator = cls._comparison_operator(comparison)
        if isinstance(comparison, Mapping):
            allowed = {"operator", "type", "target", "expected", "value"}
            if any(key not in allowed for key in comparison):
                raise ValueError("External monitor comparison has unsupported fields")
            target_keys = {key for key in ("target", "expected", "value") if key in comparison}
        else:
            target_keys = set()

        if operator in EXPLICIT_TARGET_COMPARISONS:
            target = cls._comparison_target(comparison)
            if operator in {"less_than", "greater_than"} and not (
                cls._is_number(baseline) and cls._is_number(target)
            ):
                raise ValueError("Ordered external monitor comparisons require numeric values")
            try:
                already_satisfied = cls._compare_external_values(baseline, baseline, comparison)
            except RuntimeError as exc:
                raise ValueError(
                    "External monitor baseline is incompatible with its comparison"
                ) from exc
            if already_satisfied:
                raise ValueError("External monitor baseline already satisfies its comparison")
            return

        if target_keys:
            raise ValueError(f"External monitor comparison '{operator}' does not accept a target")
        if operator in {"decreased", "increased"} and not cls._is_number(baseline):
            raise ValueError("Ordered external monitors require a numeric baseline")
        if operator == "truthy" and bool(baseline):
            raise ValueError("External monitor baseline already satisfies its comparison")

    @staticmethod
    def _render_monitor_value(value: Any) -> str:
        rendered = json.dumps(
            redact_secrets(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        return redact_text(rendered, max_length=240)

    @classmethod
    def _external_monitor_change_message(cls, baseline: Any, current: Any, comparison: Any) -> str:
        operator = cls._comparison_operator(comparison)
        if operator == "changed" and any(
            isinstance(value, Mapping) and str(value.get("kind") or "") == "content_fingerprint"
            for value in (baseline, current)
        ):
            return "The monitored page or live result changed."

        numeric_evidence = cls._is_number(baseline) and cls._is_number(current)
        if operator in EXPLICIT_TARGET_COMPARISONS:
            target_value = cls._comparison_target(comparison)
            numeric_evidence = numeric_evidence and cls._is_number(target_value)
        if not numeric_evidence:
            neutral_messages = {
                "changed": "The monitored external value changed.",
                "equals": ("The monitored external value reached the configured target."),
                "not_equals": (
                    "The monitored external value no longer matches the configured target."
                ),
                "contains": ("The monitored external value now contains the configured target."),
                "truthy": "The monitored external condition became true.",
            }
            if operator in neutral_messages:
                return neutral_messages[operator]

        before = cls._render_monitor_value(baseline)
        after = cls._render_monitor_value(current)
        if operator == "changed":
            return f"The monitored value changed from {before} to {after}."
        if operator == "decreased":
            return f"The monitored value decreased from {before} to {after}."
        if operator == "increased":
            return f"The monitored value increased from {before} to {after}."
        if operator == "truthy":
            return f"The monitored value became truthy, changing from {before} to {after}."

        target = cls._render_monitor_value(cls._comparison_target(comparison))
        if operator == "equals":
            return f"The monitored value reached {after}, matching target {target}."
        if operator == "not_equals":
            return (
                f"The monitored value changed from {before} to {after}, which no "
                f"longer matches target {target}."
            )
        if operator == "less_than":
            return f"The monitored value is now {after}, below target {target} (baseline {before})."
        if operator == "greater_than":
            return f"The monitored value is now {after}, above target {target} (baseline {before})."
        if operator == "contains":
            return f"The monitored value now contains target {target}: {after}."
        raise RuntimeError("External monitor comparison could not be described")

    @classmethod
    def _compare_external_values(cls, baseline: Any, current: Any, comparison: Any) -> bool:
        operator = cls._comparison_operator(comparison)
        if operator == "changed":
            return current != baseline
        if operator in EXPLICIT_TARGET_COMPARISONS:
            expected = cls._comparison_target(comparison)
        else:
            expected = baseline
        if operator == "equals":
            return current == expected
        if operator == "not_equals":
            return current != expected
        if operator in {"decreased", "less_than"}:
            if not (cls._is_number(current) and cls._is_number(expected)):
                raise RuntimeError("External monitor values cannot be ordered")
            return current < expected
        if operator in {"increased", "greater_than"}:
            if not (cls._is_number(current) and cls._is_number(expected)):
                raise RuntimeError("External monitor values cannot be ordered")
            return current > expected
        if operator == "contains":
            try:
                return expected in current
            except TypeError as exc:
                raise RuntimeError("External monitor value does not support containment") from exc
        if operator == "truthy":
            return bool(current)
        raise RuntimeError("External monitor comparison could not be evaluated")
