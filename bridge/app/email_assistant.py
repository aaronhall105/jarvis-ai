"""Durable Gmail policies built on the connector receipt boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.connectors import CapabilityRequest, ConnectorRegistry, ExecutionStatus
from app.connectors.credentials import redact_text


logger = logging.getLogger("jarvis-email-assistant")


class EmailAssistantPolicyEngine:
    """Persist and execute principal-scoped mailbox policies safely."""

    def __init__(
        self,
        database_path: str | Path,
        registry: ConnectorRegistry,
        *,
        poll_seconds: int = 60,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.poll_seconds = max(10, min(int(poll_seconds), 3600))
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
                """
            )
            # A process may stop between item persistence and provider execution.
            # Reconciliation always reads Gmail state before deciding what follows.
            connection.execute(
                "UPDATE email_policy_items SET status='outcome_unknown',"
                "error='Core restarted while the policy item was executing',updated_at=? "
                "WHERE status='executing'",
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
                    "AND kind='inbox_retention' AND status IN ('active','paused') "
                    "ORDER BY created_at LIMIT 1",
                    (principal,),
                ).fetchone()
                if enabled is not None:
                    existing = self._row(enabled)
                    if (
                        existing["conversation_id"] == conversation
                        and existing["retention_days"] == days
                        and existing["interval_seconds"] == interval
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
                    "interval_seconds,idempotency_key,created_at,updated_at,next_run_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        policy_id,
                        principal,
                        conversation,
                        "inbox_retention",
                        "active",
                        days,
                        interval,
                        key,
                        self._iso(now),
                        self._iso(now),
                        self._iso(now),
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
                        "operation": "gmail.trash",
                        "permanent_delete": False,
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

    async def run_due(self, *, now: datetime | None = None) -> Sequence[dict[str, Any]]:
        current = (now or self._now()).astimezone(timezone.utc)
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM email_policies WHERE status='active' AND next_run_at<=? "
                "ORDER BY next_run_at LIMIT 20",
                (self._iso(current),),
            ).fetchall()
        results = []
        for row in rows:
            results.append(await self.run_policy(str(row["policy_id"]), now=current))
        return results

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

    @staticmethod
    def _eligibility_key(policy: Mapping[str, Any], message_id: str, cutoff: datetime) -> str:
        material = (
            f"{policy['policy_id']}:{message_id}:{policy['retention_days']}:"
            f"{cutoff.date().isoformat()}:inbox-not-sent-draft-trash"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

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
            if "TRASH" in labels:
                evidence = json.loads(str(row["evidence_json"] or "{}"))
                evidence["reconciliation"] = {
                    "trash_label_present": True,
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
                    "trash_label_present": False,
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
                    error="Prior write outcome was unknown; fresh readback did not show Trash",
                )
                reconciled += 1
        return reconciled

    async def run_policy(
        self,
        policy_id: str,
        *,
        now: datetime | None = None,
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
            if policy["status"] != "active":
                return {"policy_id": policy_id, "status": policy["status"], "ran": False}

            current = (now or self._now()).astimezone(timezone.utc)
            cutoff = current - timedelta(days=int(policy["retention_days"]))
            reconciled = await self._reconcile_unknown_items(policy)
            search = await self.registry.execute(
                CapabilityRequest(
                    capability_id="gmail.search",
                    payload={
                        # Gmail accepts Unix seconds for before:. Query through the
                        # inclusive cutoff second, then enforce the exact timestamp
                        # and live labels again in _eligible before every write.
                        "query": f"in:inbox before:{int(cutoff.timestamp()) + 1}",
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
                }

            candidate_ids = [
                str(item) for item in search.data.get("message_ids") or () if str(item).strip()
            ]
            counts = {"trashed": 0, "skipped": 0, "failed": 0, "outcome_unknown": 0}
            halted_reason: str | None = None
            for message_id in dict.fromkeys(candidate_ids):
                message = await self._read_message(policy=policy, message_id=message_id)
                if message is None:
                    counts["failed"] += 1
                    continue
                eligible, reason = self._eligible(message, cutoff)
                if not eligible:
                    counts["skipped"] += 1
                    continue
                with self._db() as connection:
                    latest_policy = connection.execute(
                        "SELECT status,retention_days FROM email_policies WHERE policy_id=?",
                        (policy_id,),
                    ).fetchone()
                if latest_policy is None:
                    halted_reason = "policy_removed"
                    break
                if str(latest_policy["status"]) != "active":
                    halted_reason = f"policy_{latest_policy['status']}"
                    break
                if int(latest_policy["retention_days"]) != int(policy["retention_days"]):
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
                    "permanent_delete": False,
                }
                self._record_item(
                    policy_id=policy_id,
                    message_id=message_id,
                    eligibility_key=eligibility_key,
                    status="executing",
                    attempts=attempts,
                    evidence=evidence,
                )
                action_key = f"email-retention:{eligibility_key}:attempt:{attempts}"
                action = await self.registry.execute(
                    CapabilityRequest(
                        capability_id="gmail.trash",
                        payload={"message_id": message_id},
                        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, action_key)),
                        conversation_id=policy["conversation_id"],
                        principal_id=policy["principal_id"],
                        operation="retention_inbox_to_trash",
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
                if action.status is ExecutionStatus.VERIFIED:
                    item_status = "verified"
                    counts["trashed"] += 1
                elif action.status is ExecutionStatus.OUTCOME_UNKNOWN:
                    item_status = "outcome_unknown"
                    counts["outcome_unknown"] += 1
                    observed = await self._read_message(policy=policy, message_id=message_id)
                    if observed is not None and "TRASH" in {
                        str(item) for item in observed.get("label_ids") or ()
                    }:
                        item_status = "verified_after_unknown"
                        counts["outcome_unknown"] -= 1
                        counts["trashed"] += 1
                        evidence["reconciliation"] = {
                            "trash_label_present": True,
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
            with self._db() as connection:
                connection.execute(
                    "UPDATE email_policies SET last_run_at=?,last_error=?,next_run_at=?,updated_at=? "
                    "WHERE policy_id=?",
                    (
                        self._iso(current),
                        last_error,
                        self._iso(next_run),
                        self._iso(current),
                        policy_id,
                    ),
                )
                self._audit(
                    connection,
                    policy,
                    "run",
                    run_state,
                    {
                        **counts,
                        "candidates": len(candidate_ids),
                        "reconciled": reconciled,
                        "cutoff": self._iso(cutoff),
                    },
                )
            return {
                "policy_id": policy_id,
                "status": run_state,
                "ran": True,
                "candidates": len(candidate_ids),
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
