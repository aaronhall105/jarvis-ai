"""Durable, redacted action receipts for external connector writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.connectors.credentials import SecretValue, redact_secrets, redact_text


class ReceiptStatus(str, Enum):
    STARTED = "started"
    VERIFIED = "verified"
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"

    @property
    def terminal(self) -> bool:
        return self is not ReceiptStatus.STARTED


TERMINAL_RECEIPT_STATUSES = frozenset(status for status in ReceiptStatus if status.terminal)


class IdempotencyConflict(RuntimeError):
    """An idempotency key was reused for a materially different action."""


class ReceiptStateError(RuntimeError):
    """An invalid receipt state transition was requested."""


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    action_id: str
    request_id: str
    conversation_id: str | None
    capability_id: str
    provider_id: str
    target: Any
    requested_operation: str
    status: ReceiptStatus
    request_digest: str
    idempotency_digest: str
    provider_reference: str | None
    result: Mapping[str, Any]
    verification: Mapping[str, Any]
    error: str | None
    started_at: str
    completed_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "capability_id": self.capability_id,
            "provider_id": self.provider_id,
            "target": self.target,
            "requested_operation": self.requested_operation,
            "status": self.status.value,
            "request_digest": self.request_digest,
            "idempotency_digest": self.idempotency_digest,
            "provider_reference": self.provider_reference,
            "result": dict(self.result),
            "verification": dict(self.verification),
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class ReceiptClaim:
    receipt: ActionReceipt
    claimed: bool


def _json_default(value: Any) -> Any:
    if isinstance(value, SecretValue):
        return value.reveal()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def action_request_digest(
    *,
    capability_id: str,
    request_payload: Mapping[str, Any],
    target: Any,
    operation: str,
) -> str:
    """Bind idempotency to the exact operation without retaining its payload."""

    encoded = _canonical_json(
        {
            "capability_id": capability_id,
            "request": request_payload,
            "target": target,
            "operation": operation,
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ActionReceiptStore:
    """SQLite/WAL receipt journal.

    Every public database operation is offloaded from the event loop.  ``begin``
    commits its STARTED row before returning, so the registry can prove that an
    audit record exists before invoking a write connector.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

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

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS connector_action_receipts (
                  action_id TEXT PRIMARY KEY,
                  request_id TEXT NOT NULL,
                  conversation_id TEXT,
                  capability_id TEXT NOT NULL,
                  provider_id TEXT NOT NULL,
                  target_json TEXT NOT NULL,
                  requested_operation TEXT NOT NULL,
                  status TEXT NOT NULL,
                  request_digest TEXT NOT NULL,
                  idempotency_digest TEXT NOT NULL UNIQUE,
                  provider_reference TEXT,
                  result_json TEXT NOT NULL DEFAULT '{}',
                  verification_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT,
                  started_at TEXT NOT NULL,
                  completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_connector_receipts_recent
                  ON connector_action_receipts(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_connector_receipts_conversation
                  ON connector_action_receipts(conversation_id, started_at DESC);
                """
            )

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if not self._initialized:
                await asyncio.to_thread(self._initialize_sync)
                self._initialized = True

    @staticmethod
    def _row(row: sqlite3.Row) -> ActionReceipt:
        return ActionReceipt(
            action_id=str(row["action_id"]),
            request_id=str(row["request_id"]),
            conversation_id=row["conversation_id"],
            capability_id=str(row["capability_id"]),
            provider_id=str(row["provider_id"]),
            target=json.loads(row["target_json"]),
            requested_operation=str(row["requested_operation"]),
            status=ReceiptStatus(row["status"]),
            request_digest=str(row["request_digest"]),
            idempotency_digest=str(row["idempotency_digest"]),
            provider_reference=row["provider_reference"],
            result=json.loads(row["result_json"] or "{}"),
            verification=json.loads(row["verification_json"] or "{}"),
            error=row["error"],
            started_at=str(row["started_at"]),
            completed_at=row["completed_at"],
        )

    def _begin_sync(
        self,
        *,
        action_id: str,
        request_id: str,
        conversation_id: str | None,
        capability_id: str,
        provider_id: str,
        target_json: str,
        operation: str,
        request_digest: str,
        idempotency_digest: str,
        started_at: str,
    ) -> ReceiptClaim:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO connector_action_receipts (
                      action_id, request_id, conversation_id, capability_id,
                      provider_id, target_json, requested_operation, status,
                      request_digest, idempotency_digest, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id,
                        request_id,
                        conversation_id,
                        capability_id,
                        provider_id,
                        target_json,
                        operation,
                        ReceiptStatus.STARTED.value,
                        request_digest,
                        idempotency_digest,
                        started_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM connector_action_receipts WHERE action_id=?",
                    (action_id,),
                ).fetchone()
                if row is None:  # pragma: no cover - protected by the transaction
                    raise RuntimeError("Action receipt insert did not persist")
                return ReceiptClaim(self._row(row), True)
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM connector_action_receipts WHERE idempotency_digest=?",
                    (idempotency_digest,),
                ).fetchone()
                if row is None:  # pragma: no cover - an unrelated integrity failure
                    raise
                receipt = self._row(row)
                if receipt.request_digest != request_digest:
                    raise IdempotencyConflict(
                        "Idempotency key is already bound to a different connector action"
                    )
                return ReceiptClaim(receipt, False)

    async def begin(
        self,
        *,
        request_id: str,
        conversation_id: str | None,
        capability_id: str,
        provider_id: str,
        target: Any,
        requested_operation: str,
        request_payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> ReceiptClaim:
        if not str(idempotency_key or ""):
            raise ValueError("idempotency_key must not be empty")
        await self.initialize()
        operation = str(requested_operation or capability_id)
        digest = action_request_digest(
            capability_id=capability_id,
            request_payload=request_payload,
            target=target,
            operation=operation,
        )
        safe_target = redact_secrets(target)
        target_json = _canonical_json(safe_target)
        return await asyncio.to_thread(
            self._begin_sync,
            action_id=str(uuid.uuid4()),
            request_id=str(request_id),
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            capability_id=str(capability_id),
            provider_id=str(provider_id),
            target_json=target_json,
            operation=operation,
            request_digest=digest,
            idempotency_digest=_idempotency_digest(str(idempotency_key)),
            started_at=self._now(),
        )

    def _complete_sync(
        self,
        action_id: str,
        status: ReceiptStatus,
        provider_reference: str | None,
        result_json: str,
        verification_json: str,
        error: str | None,
        completed_at: str,
    ) -> ActionReceipt:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE connector_action_receipts
                SET status=?, provider_reference=?, result_json=?,
                    verification_json=?, error=?, completed_at=?
                WHERE action_id=? AND status=?
                """,
                (
                    status.value,
                    provider_reference,
                    result_json,
                    verification_json,
                    error,
                    completed_at,
                    action_id,
                    ReceiptStatus.STARTED.value,
                ),
            ).rowcount
            row = connection.execute(
                "SELECT * FROM connector_action_receipts WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown action receipt: {action_id}")
            receipt = self._row(row)
            if not changed and receipt.status is not status:
                raise ReceiptStateError(
                    f"Action receipt is already terminal with status {receipt.status.value}"
                )
            return receipt

    async def complete(
        self,
        action_id: str,
        *,
        status: ReceiptStatus,
        provider_reference: str | None = None,
        result: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> ActionReceipt:
        if status not in TERMINAL_RECEIPT_STATUSES:
            raise ValueError("Action receipt completion requires a terminal status")
        await self.initialize()
        safe_result = redact_secrets(result or {})
        safe_verification = redact_secrets(verification or {})
        safe_reference = (
            redact_text(provider_reference, max_length=1_000) if provider_reference else None
        )
        safe_error = redact_text(error, max_length=2_000) if error else None
        return await asyncio.to_thread(
            self._complete_sync,
            str(action_id),
            status,
            safe_reference,
            _canonical_json(safe_result),
            _canonical_json(safe_verification),
            safe_error,
            self._now(),
        )

    def _get_sync(self, action_id: str) -> ActionReceipt | None:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM connector_action_receipts WHERE action_id=?",
                (action_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    async def get(self, action_id: str) -> ActionReceipt | None:
        await self.initialize()
        return await asyncio.to_thread(self._get_sync, str(action_id))

    def _get_by_idempotency_sync(self, digest: str) -> ActionReceipt | None:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM connector_action_receipts WHERE idempotency_digest=?",
                (digest,),
            ).fetchone()
        return self._row(row) if row is not None else None

    async def get_by_idempotency_key(self, key: str) -> ActionReceipt | None:
        await self.initialize()
        return await asyncio.to_thread(self._get_by_idempotency_sync, _idempotency_digest(key))

    def _list_recent_sync(
        self,
        limit: int,
        conversation_id: str | None,
    ) -> list[ActionReceipt]:
        with self._db() as connection:
            if conversation_id is None:
                rows = connection.execute(
                    "SELECT * FROM connector_action_receipts ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM connector_action_receipts
                    WHERE conversation_id=? ORDER BY started_at DESC LIMIT ?
                    """,
                    (conversation_id, limit),
                ).fetchall()
        return [self._row(row) for row in rows]

    async def list_recent(
        self,
        *,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[ActionReceipt]:
        await self.initialize()
        bounded = max(1, min(int(limit), 500))
        return await asyncio.to_thread(self._list_recent_sync, bounded, conversation_id)

    def _recover_stale_sync(self, cutoff: str, completed_at: str) -> int:
        with self._db() as connection:
            return connection.execute(
                """
                UPDATE connector_action_receipts
                SET status=?, error=?, completed_at=?
                WHERE status=? AND started_at<?
                """,
                (
                    ReceiptStatus.OUTCOME_UNKNOWN.value,
                    "Process ended before the provider outcome was durably recorded",
                    completed_at,
                    ReceiptStatus.STARTED.value,
                    cutoff,
                ),
            ).rowcount

    async def recover_stale(self, *, older_than_seconds: int = 300) -> int:
        """Conservatively close crash-orphaned writes without retrying them."""

        await self.initialize()
        age = max(1, int(older_than_seconds))
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=age)).isoformat()
        return await asyncio.to_thread(self._recover_stale_sync, cutoff, now.isoformat())
