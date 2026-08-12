from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.user_context import UserContext

logger = logging.getLogger("jarvis-core.improvement")


_CORRECTION_PATTERN = re.compile(
    r"\b(?:that(?:'s| is| was)? wrong|you got (?:that|it) wrong|wrong device|"
    r"not what i meant|i meant|no[, ]+i meant|that didn['’]?t work|"
    r"it didn['’]? work|why did you|you should have|that was the wrong|"
    r"you misunderstood|you didn['’]?t understand|record that as a mistake|"
    r"learn from that)\b",
    re.I,
)

_POSITIVE_PATTERN = re.compile(
    r"^\s*(?:it works|works|that works|fixed|perfect|brilliant|spot on|"
    r"that['’]?s right|yes that['’]?s right)(?:[.! ]|$)",
    re.I,
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:api[_ -]?key|token|password|secret|pin)\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(https?://[^\s:@/]+:)[^@\s/]+@"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)

_PREPARE_LAST_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:prepare|build|make|create)\s+(?:a\s+)?(?:safe\s+)?"
    r"(?:fix|improvement|patch)\s+(?:for\s+)?(?:the\s+)?last\s+mistake\s*[.!?]*\s*$",
    re.I,
)
_PREPARE_ID_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:prepare|build|make|create)\s+(?:a\s+)?(?:safe\s+)?"
    r"(?:fix|improvement|patch)\s+(?:for\s+)?(?:failure|mistake|issue)\s+#?(\d+)\s*[.!?]*\s*$",
    re.I,
)
_APPROVE_PATTERN = re.compile(
    r"^\s*approve\s+improvement\s+#?(\d+)\s+(?:code\s+)?([0-9]{6})\s*[.!?]*\s*$",
    re.I,
)
_DEPLOY_PATTERN = re.compile(
    r"^\s*deploy\s+improvement\s+#?(\d+)\s+(?:code\s+)?([0-9]{6})\s*[.!?]*\s*$",
    re.I,
)
_REJECT_PATTERN = re.compile(
    r"^\s*(?:reject|discard|cancel)\s+improvement\s+#?(\d+)\s*[.!?]*\s*$",
    re.I,
)
_ROLLBACK_PATTERN = re.compile(
    r"^\s*(?:roll\s*back|rollback|undo)\s+improvement\s+#?(\d+)\s+"
    r"(?:code\s+)?([0-9]{6})\s*[.!?]*\s*$",
    re.I,
)
_STATUS_PATTERN = re.compile(
    r"^\s*(?:show|tell me|what(?:'s| is)|give me)?\s*(?:the\s+)?"
    r"(?:self[- ]?improvement|improvement)\s+(?:status|summary|queue|report)\s*[.!?]*\s*$",
    re.I,
)
_FAILURES_PATTERN = re.compile(
    r"^\s*(?:show|list|what are|tell me)\s+(?:the\s+)?(?:pending\s+)?"
    r"(?:mistakes|failures|issues)\s*(?:you(?:'ve| have)?\s+recorded)?\s*[.!?]*\s*$",
    re.I,
)
_CANDIDATES_PATTERN = re.compile(
    r"^\s*(?:show|list|what are|tell me)\s+(?:the\s+)?(?:pending\s+)?"
    r"(?:improvements|candidates|fixes|patches)\s*[.!?]*\s*$",
    re.I,
)
_STOP_PATTERN = re.compile(
    r"^\s*(?:emergency\s+stop|disable|stop|pause)\s+(?:jarvis\s+)?"
    r"self[- ]?improvement\s*[.!?]*\s*$",
    re.I,
)
_RESUME_PATTERN = re.compile(
    r"^\s*(?:enable|resume|start)\s+(?:jarvis\s+)?self[- ]?improvement\s*[.!?]*\s*$",
    re.I,
)
_RECORD_PATTERN = re.compile(
    r"^\s*(?:record|save|log)\s+(?:that|this)\s+as\s+(?:a\s+)?mistake\s*[.!?]*\s*$",
    re.I,
)


@dataclass(slots=True)
class ImprovementCommandResult:
    handled: bool
    success: bool = True
    response: str = ""
    intent: str = "self_improvement"
    details: dict[str, Any] | None = None


class SelfImprovementEngine:
    """Persistent, supervised self-improvement coordination for Jarvis.

    The live Jarvis process records evidence and accepts authenticated approval
    commands. A separate unprivileged host worker generates and tests candidate
    patches. The live process never edits its own source tree directly.
    """

    def __init__(
        self,
        database_path: str,
        *,
        enabled: bool = True,
        auto_prepare: bool = True,
        repeat_threshold: int = 2,
        latency_failure_ms: int = 7000,
        core_version: str = "unknown",
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_enabled = bool(enabled)
        self.auto_prepare = bool(auto_prepare)
        self.repeat_threshold = max(1, int(repeat_threshold))
        self.latency_failure_ms = max(1000, int(latency_failure_ms))
        self.core_version = core_version
        self.disabled_file = self.database_path.parent / "self_improvement.disabled"
        self._initialise_database()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _ensure_candidate_transaction_columns(
        connection: sqlite3.Connection,
    ) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(improvement_candidates)"
            ).fetchall()
        }

        columns = {
            "approval_code_expires_at": "TEXT",
            "deploy_ticket_hash": "TEXT",
            "deploy_ticket_salt": "TEXT",
            "deploy_ticket_expires_at": "TEXT",
            "deploy_ticket_consumed_at": "TEXT",
            "base_commit": "TEXT",
            "candidate_commit": "TEXT",
            "validated_patch_sha256": "TEXT",
            "deploy_lease_id": "TEXT",
            "deploy_lease_started_at": "TEXT",
            "deploy_lease_expires_at": "TEXT",
            "deploy_phase": "TEXT",
        }

        for name, data_type in columns.items():
            if name in existing:
                continue

            connection.execute(
                "ALTER TABLE improvement_candidates "
                f"ADD COLUMN {name} {data_type}"
            )

    @staticmethod
    def _utc_after(
        seconds: int,
    ) -> str:
        return (
            datetime.now(timezone.utc)
            + timedelta(seconds=seconds)
        ).isoformat()

    @staticmethod
    def _timestamp_expired(
        value: str | None,
        *,
        missing_is_expired: bool,
    ) -> bool:
        raw = str(
            value or ""
        ).strip()

        if not raw:
            return missing_is_expired

        try:
            parsed = datetime.fromisoformat(
                raw
            )
        except ValueError:
            return True

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return (
            parsed.astimezone(timezone.utc)
            <= datetime.now(timezone.utc)
        )

    @staticmethod
    def _ticket_digest(
        candidate_id: int,
        salt: str,
        code: str,
    ) -> str:
        return hashlib.sha256(
            (
                f"{candidate_id}:"
                f"{salt}:"
                f"{code}"
            ).encode(
                "utf-8"
            )
        ).hexdigest()

    def _initialise_database(self) -> None:
        now = self._utc_now()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS improvement_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS improvement_interactions (
                    interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    user_key TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    interpreted_text TEXT,
                    intent TEXT,
                    success INTEGER NOT NULL,
                    response TEXT NOT NULL,
                    tool_calls_json TEXT NOT NULL DEFAULT '[]',
                    understanding_json TEXT NOT NULL DEFAULT '{}',
                    timings_json TEXT NOT NULL DEFAULT '{}',
                    tone_json TEXT NOT NULL DEFAULT '{}',
                    core_version TEXT NOT NULL,
                    failure_like INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_improvement_interactions_conversation
                ON improvement_interactions (conversation_id, interaction_id DESC);

                CREATE TABLE IF NOT EXISTS improvement_failures (
                    failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    user_key TEXT NOT NULL,
                    source_interaction_id INTEGER,
                    correction_interaction_id INTEGER,
                    signature TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 1,
                    explicit INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'recorded',
                    FOREIGN KEY (source_interaction_id)
                        REFERENCES improvement_interactions(interaction_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_improvement_failures_signature
                ON improvement_failures (signature);

                CREATE INDEX IF NOT EXISTS idx_improvement_failures_status
                ON improvement_failures (status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS improvement_candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    failure_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    model TEXT,
                    branch_name TEXT,
                    workspace_path TEXT,
                    summary TEXT,
                    root_cause TEXT,
                    risk TEXT,
                    patch_path TEXT,
                    changed_files_json TEXT NOT NULL DEFAULT '[]',
                    diff_stats_json TEXT NOT NULL DEFAULT '{}',
                    test_results_json TEXT NOT NULL DEFAULT '{}',
                    security_results_json TEXT NOT NULL DEFAULT '{}',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    approval_code TEXT,
                    approved_at TEXT,
                    deploy_requested_at TEXT,
                    deployed_at TEXT,
                    rollback_requested_at TEXT,
                    rolled_back_at TEXT,
                    rollback_ref TEXT,
                    pr_url TEXT,
                    error TEXT,
                    FOREIGN KEY (failure_id)
                        REFERENCES improvement_failures(failure_id)
                );

                CREATE INDEX IF NOT EXISTS idx_improvement_candidates_status
                ON improvement_candidates (status, updated_at ASC);

                CREATE TABLE IF NOT EXISTS improvement_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    failure_id INTEGER,
                    candidate_id INTEGER,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            self._ensure_candidate_transaction_columns(
                connection
            )

            connection.execute(
                """
                INSERT OR IGNORE INTO improvement_settings (key, value, updated_at)
                VALUES ('enabled', ?, ?)
                """,
                ("true" if self.default_enabled else "false", now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO improvement_settings (key, value, updated_at)
                VALUES ('worker_heartbeat', '', ?)
                """,
                (now,),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))

    @staticmethod
    def _load_json(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def redact_text(text: str) -> str:
        redacted = str(text or "")
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                redacted = pattern.sub(r"\1[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        return redacted[:5000]

    @staticmethod
    def _normalise_signature_text(text: str) -> str:
        lowered = text.casefold()
        lowered = re.sub(r"\b\d+\b", "#", lowered)
        lowered = re.sub(r"[^a-z0-9#]+", " ", lowered)
        return re.sub(r"\s+", " ", lowered).strip()[:240]

    def _signature(
        self,
        *,
        category: str,
        source: dict[str, Any],
        correction: str | None,
    ) -> str:
        tool_names = []
        for call in source.get("tool_calls", []):
            if isinstance(call, dict):
                name = str(call.get("tool") or call.get("name") or "")
                if name:
                    tool_names.append(name)
        raw = "|".join(
            [
                category,
                str(source.get("intent") or ""),
                self._normalise_signature_text(str(source.get("raw_text") or "")),
                ",".join(sorted(set(tool_names))),
                self._normalise_signature_text(correction or ""),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _category_for_interaction(source: dict[str, Any], correction: str | None = None) -> str:
        text = f"{source.get('raw_text', '')} {correction or ''}".casefold()
        intent = str(source.get("intent") or "").casefold()
        calls = source.get("tool_calls", [])
        tool_names = " ".join(
            str(call.get("tool") or call.get("name") or "")
            for call in calls
            if isinstance(call, dict)
        ).casefold()

        if "notification" in text or "notification" in intent or "notify" in tool_names:
            return "notification"
        if any(word in text for word in ("wrong device", "light", "switch", "tv", "speaker")):
            return "device_resolution"
        if any(word in text for word in ("remember", "context", "forgot", "pronoun")):
            return "dialogue_context"
        if any(word in text for word in ("slow", "pause", "latency", "faster")):
            return "performance"
        if any(word in text for word in ("automation", "script", "admin mode")):
            return "admin_mode"
        if any(word in text for word in ("angry", "frustrated", "tone", "lol", "happy")):
            return "tone"
        if "awareness" in intent or any(word in text for word in ("what changed", "while i was out")):
            return "house_awareness"
        if "stream" in intent or "stream" in text:
            return "streaming"
        return "general"

    @staticmethod
    def _severity_for(source: dict[str, Any], explicit: bool) -> str:
        calls = source.get("tool_calls", [])
        for call in calls:
            if not isinstance(call, dict):
                continue
            result = call.get("result")
            if isinstance(result, dict):
                entity_id = str(result.get("entity_id") or call.get("entity_id") or "")
                if entity_id.startswith(("lock.", "alarm_control_panel.", "cover.")):
                    return "critical"
            tool = str(call.get("tool") or "")
            if tool in {"propose_admin_change", "apply_admin_change"}:
                return "high"
        if explicit:
            return "medium"
        return "low"

    def _setting_sync(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM improvement_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    async def setting(self, key: str, default: str = "") -> str:
        return await asyncio.to_thread(self._setting_sync, key, default)

    def _set_setting_sync(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO improvement_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, self._utc_now()),
            )

    async def set_setting(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._set_setting_sync, key, value)

    async def enabled(self) -> bool:
        if self.disabled_file.exists():
            return False
        value = (await self.setting("enabled", "true")).casefold()
        return value not in {"0", "false", "no", "off", "disabled"}

    async def set_enabled(self, enabled: bool, actor: str) -> None:
        await self.set_setting("enabled", "true" if enabled else "false")
        if enabled:
            try:
                self.disabled_file.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove self-improvement emergency-stop file")
        else:
            try:
                self.disabled_file.write_text(
                    f"disabled by {actor} at {self._utc_now()}\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.warning("Could not write self-improvement emergency-stop file")
        await self.audit(
            "self_improvement_enabled" if enabled else "self_improvement_disabled",
            actor=actor,
            details={"enabled": enabled},
        )

    def _insert_interaction_sync(
        self,
        *,
        conversation_id: str,
        user_key: str,
        raw_text: str,
        interpreted_text: str | None,
        intent: str,
        success: bool,
        response: str,
        tool_calls: list[dict[str, Any]],
        understanding: dict[str, Any],
        timings: dict[str, Any],
        tone: dict[str, Any],
        failure_like: bool,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO improvement_interactions (
                    created_at, conversation_id, user_key, raw_text,
                    interpreted_text, intent, success, response,
                    tool_calls_json, understanding_json, timings_json,
                    tone_json, core_version, failure_like
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    conversation_id,
                    user_key,
                    self.redact_text(raw_text),
                    self.redact_text(interpreted_text or "") or None,
                    intent,
                    1 if success else 0,
                    self.redact_text(response),
                    self._json(tool_calls),
                    self._json(understanding),
                    self._json(timings),
                    self._json(tone),
                    self.core_version,
                    1 if failure_like else 0,
                ),
            )
            return int(cursor.lastrowid)

    async def observe_interaction(
        self,
        *,
        conversation_id: str,
        actor: UserContext,
        raw_text: str,
        result: dict[str, Any],
    ) -> int:
        calls = result.get("calls") if isinstance(result.get("calls"), list) else []
        understanding = (
            result.get("understanding")
            if isinstance(result.get("understanding"), dict)
            else {}
        )
        timings = result.get("timings") if isinstance(result.get("timings"), dict) else {}
        tone = result.get("tone") if isinstance(result.get("tone"), dict) else {}
        success = bool(result.get("success"))
        intent = str(result.get("intent") or "unknown")
        response = str(result.get("response") or "")

        failed_tool = any(
            isinstance(call, dict)
            and isinstance(call.get("result"), dict)
            and call["result"].get("success") is False
            for call in calls
        )
        latency = int(timings.get("jarvis_request_total_ms") or 0)
        benign_false_intents = {
            "admin_confirm_missing",
            "control_follow_up_ambiguous",
            "dialogue_cancel",
            "future_action",
            "clarification",
        }
        failure_like = (
            failed_tool
            or (not success and intent not in benign_false_intents)
            or latency >= self.latency_failure_ms
        )

        interaction_id = await asyncio.to_thread(
            self._insert_interaction_sync,
            conversation_id=conversation_id,
            user_key=actor.user_key,
            raw_text=raw_text,
            interpreted_text=str(understanding.get("interpreted_text") or "") or None,
            intent=intent,
            success=success,
            response=response,
            tool_calls=calls,
            understanding=understanding,
            timings=timings,
            tone=tone,
            failure_like=failure_like,
        )

        if failed_tool or (not success and intent not in benign_false_intents):
            source = await self.get_interaction(interaction_id)
            if source:
                await self.record_failure(
                    source=source,
                    correction=None,
                    explicit=False,
                    summary=(
                        f"Jarvis request failed: {self.redact_text(raw_text)[:180]}"
                    ),
                )
        elif latency >= self.latency_failure_ms:
            source = await self.get_interaction(interaction_id)
            if source:
                await self.record_failure(
                    source=source,
                    correction=f"Jarvis request took {latency} ms.",
                    explicit=False,
                    summary=f"Slow Jarvis response ({latency} ms).",
                )

        if _POSITIVE_PATTERN.search(raw_text):
            await self._mark_recent_failure_confirmed_resolved(conversation_id)

        return interaction_id

    def _interaction_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["success"] = bool(item.get("success"))
        item["failure_like"] = bool(item.get("failure_like"))
        item["tool_calls"] = self._load_json(item.pop("tool_calls_json", "[]"), [])
        item["understanding"] = self._load_json(item.pop("understanding_json", "{}"), {})
        item["timings"] = self._load_json(item.pop("timings_json", "{}"), {})
        item["tone"] = self._load_json(item.pop("tone_json", "{}"), {})
        return item

    def _get_interaction_sync(self, interaction_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM improvement_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        return self._interaction_from_row(row) if row else None

    async def get_interaction(self, interaction_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_interaction_sync, interaction_id)

    def _last_interaction_sync(
        self,
        conversation_id: str,
        *,
        before_id: int | None = None,
    ) -> dict[str, Any] | None:
        query = (
            "SELECT * FROM improvement_interactions WHERE conversation_id = ? "
            + ("AND interaction_id < ? " if before_id else "")
            + "ORDER BY interaction_id DESC LIMIT 1"
        )
        params: tuple[Any, ...] = (
            (conversation_id, before_id) if before_id else (conversation_id,)
        )
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._interaction_from_row(row) if row else None

    async def last_interaction(self, conversation_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._last_interaction_sync, conversation_id)

    async def capture_feedback_before_request(
        self,
        *,
        conversation_id: str,
        actor: UserContext,
        raw_text: str,
    ) -> int | None:
        if not _CORRECTION_PATTERN.search(raw_text):
            return None
        source = await self.last_interaction(conversation_id)
        if source is None:
            return None
        return await self.record_failure(
            source=source,
            correction=raw_text,
            explicit=True,
            summary=(
                "User correction after Jarvis response: "
                f"{self.redact_text(raw_text)[:220]}"
            ),
        )

    def _upsert_failure_sync(
        self,
        *,
        source: dict[str, Any],
        correction: str | None,
        explicit: bool,
        summary: str,
    ) -> tuple[int, int, str]:
        category = self._category_for_interaction(source, correction)
        severity = self._severity_for(source, explicit)
        signature = self._signature(
            category=category,
            source=source,
            correction=correction,
        )
        now = self._utc_now()
        evidence = {
            "source": {
                "interaction_id": source.get("interaction_id"),
                "created_at": source.get("created_at"),
                "raw_text": source.get("raw_text"),
                "interpreted_text": source.get("interpreted_text"),
                "intent": source.get("intent"),
                "success": source.get("success"),
                "response": source.get("response"),
                "tool_calls": source.get("tool_calls", []),
                "understanding": source.get("understanding", {}),
                "timings": source.get("timings", {}),
                "core_version": source.get("core_version"),
            },
            "correction": self.redact_text(correction or "") or None,
        }
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failure_id, occurrences, status FROM improvement_failures WHERE signature = ?",
                (signature,),
            ).fetchone()
            if row:
                failure_id = int(row["failure_id"])
                occurrences = int(row["occurrences"]) + 1
                existing_status = str(row["status"])
                next_status = existing_status
                if existing_status in {"resolved", "ignored"}:
                    next_status = "recorded"
                connection.execute(
                    """
                    UPDATE improvement_failures SET
                        updated_at = ?, last_seen_at = ?, occurrences = ?,
                        explicit = MAX(explicit, ?), severity = ?, summary = ?,
                        evidence_json = ?, status = ?
                    WHERE failure_id = ?
                    """,
                    (
                        now,
                        now,
                        occurrences,
                        1 if explicit else 0,
                        severity,
                        summary,
                        self._json(evidence),
                        next_status,
                        failure_id,
                    ),
                )
                return failure_id, occurrences, next_status

            cursor = connection.execute(
                """
                INSERT INTO improvement_failures (
                    created_at, updated_at, last_seen_at, conversation_id,
                    user_key, source_interaction_id, correction_interaction_id,
                    signature, category, severity, summary, evidence_json,
                    occurrences, explicit, status
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 1, ?, 'recorded')
                """,
                (
                    now,
                    now,
                    now,
                    str(source.get("conversation_id") or ""),
                    str(source.get("user_key") or "unknown"),
                    source.get("interaction_id"),
                    signature,
                    category,
                    severity,
                    summary,
                    self._json(evidence),
                    1 if explicit else 0,
                ),
            )
            return int(cursor.lastrowid), 1, "recorded"

    async def record_failure(
        self,
        *,
        source: dict[str, Any],
        correction: str | None,
        explicit: bool,
        summary: str,
    ) -> int:
        failure_id, occurrences, status = await asyncio.to_thread(
            self._upsert_failure_sync,
            source=source,
            correction=correction,
            explicit=explicit,
            summary=summary,
        )
        await self.audit(
            "failure_recorded",
            actor=str(source.get("user_key") or "system"),
            failure_id=failure_id,
            details={
                "occurrences": occurrences,
                "explicit": explicit,
                "status": status,
            },
        )
        if await self.enabled() and self.auto_prepare and (
            explicit or occurrences >= self.repeat_threshold
        ):
            await self.queue_failure(failure_id, actor="automatic")
        return failure_id

    def _queue_failure_sync(self, failure_id: int) -> tuple[bool, int | None, str]:
        now = self._utc_now()
        with self._connect() as connection:
            failure = connection.execute(
                "SELECT status FROM improvement_failures WHERE failure_id = ?",
                (failure_id,),
            ).fetchone()
            if not failure:
                return False, None, "Failure not found."
            existing = connection.execute(
                """
                SELECT candidate_id, status FROM improvement_candidates
                WHERE failure_id = ?
                  AND status NOT IN ('rejected', 'failed', 'rolled_back')
                ORDER BY candidate_id DESC LIMIT 1
                """,
                (failure_id,),
            ).fetchone()
            if existing:
                return True, int(existing["candidate_id"]), str(existing["status"])
            cursor = connection.execute(
                """
                INSERT INTO improvement_candidates (
                    failure_id, created_at, updated_at, status
                ) VALUES (?, ?, ?, 'queued')
                """,
                (failure_id, now, now),
            )
            candidate_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE improvement_failures SET status = 'queued', updated_at = ? WHERE failure_id = ?",
                (now, failure_id),
            )
            return True, candidate_id, "queued"

    async def queue_failure(self, failure_id: int, actor: str) -> tuple[bool, int | None, str]:
        if not await self.enabled():
            return False, None, "Self-improvement is disabled."
        result = await asyncio.to_thread(self._queue_failure_sync, failure_id)
        if result[0]:
            await self.audit(
                "candidate_queued",
                actor=actor,
                failure_id=failure_id,
                candidate_id=result[1],
                details={"status": result[2]},
            )
        return result

    def _failure_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["explicit"] = bool(item.get("explicit"))
        item["evidence"] = self._load_json(item.pop("evidence_json", "{}"), {})
        return item

    def _candidate_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in (
            "changed_files_json",
            "diff_stats_json",
            "test_results_json",
            "security_results_json",
            "usage_json",
        ):
            output_key = key.removesuffix("_json")
            item[output_key] = self._load_json(item.pop(key, "{}"), [] if "files" in key else {})
        return item

    def _list_failures_sync(self, limit: int, status: str | None) -> list[dict[str, Any]]:
        query = "SELECT * FROM improvement_failures"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._failure_from_row(row) for row in rows]

    async def list_failures(self, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_failures_sync, limit, status)

    def _get_failure_sync(self, failure_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM improvement_failures WHERE failure_id = ?",
                (failure_id,),
            ).fetchone()
        return self._failure_from_row(row) if row else None

    async def get_failure(self, failure_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_failure_sync, failure_id)

    def _list_candidates_sync(self, limit: int, status: str | None) -> list[dict[str, Any]]:
        query = "SELECT * FROM improvement_candidates"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    async def list_candidates(self, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_candidates_sync, limit, status)

    def _get_candidate_sync(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM improvement_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return self._candidate_from_row(row) if row else None

    async def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_candidate_sync, candidate_id)

    def _last_failure_sync(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM improvement_failures
                WHERE status NOT IN ('ignored')
                ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()
        return self._failure_from_row(row) if row else None

    async def last_failure(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._last_failure_sync)

    def _set_candidate_status_sync(
        self,
        candidate_id: int,
        status: str,
        *,
        approved: bool = False,
        deploy_requested: bool = False,
        rollback_requested: bool = False,
        error: str | None = None,
    ) -> bool:
        now = self._utc_now()
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, now]
        if approved:
            fields.append("approved_at = ?")
            values.append(now)
        if deploy_requested:
            fields.append("deploy_requested_at = ?")
            values.append(now)
        if rollback_requested:
            fields.append("rollback_requested_at = ?")
            values.append(now)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        values.append(candidate_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE improvement_candidates SET {', '.join(fields)} WHERE candidate_id = ?",
                tuple(values),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _code_matches(
        candidate: dict[str, Any],
        supplied: str,
    ) -> bool:
        expected = str(
            candidate.get(
                "approval_code"
            )
            or ""
        )

        return bool(
            expected
            and secrets.compare_digest(
                expected,
                supplied,
            )
        )

    def _approve_candidate_sync(
        self,
        candidate_id: int,
        code: str,
    ) -> tuple[
        bool,
        str,
        str | None,
        str | None,
    ]:
        now = self._utc_now()

        with self._connect() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT *
                FROM improvement_candidates
                WHERE candidate_id = ?
                """,
                (
                    candidate_id,
                ),
            ).fetchone()

            if row is None:
                return (
                    False,
                    "missing",
                    None,
                    None,
                )

            candidate = dict(
                row
            )

            if str(
                candidate.get(
                    "status"
                )
            ) not in {
                "awaiting_approval",
                "candidate_ready",
            }:
                return (
                    False,
                    "invalid_state",
                    None,
                    None,
                )

            if not self._code_matches(
                candidate,
                code,
            ):
                return (
                    False,
                    "bad_code",
                    None,
                    None,
                )

            if self._timestamp_expired(
                candidate.get(
                    "approval_code_expires_at"
                ),
                missing_is_expired=False,
            ):
                return (
                    False,
                    "review_code_expired",
                    None,
                    None,
                )

            deploy_code = (
                f"{secrets.randbelow(900000) + 100000:06d}"
            )

            salt = secrets.token_hex(
                16
            )

            digest = self._ticket_digest(
                candidate_id,
                salt,
                deploy_code,
            )

            expires_at = self._utc_after(
                15 * 60
            )

            cursor = connection.execute(
                """
                UPDATE improvement_candidates
                SET
                    status = 'approved',
                    updated_at = ?,
                    approved_at = ?,
                    approval_code = NULL,
                    approval_code_expires_at = NULL,
                    deploy_ticket_hash = ?,
                    deploy_ticket_salt = ?,
                    deploy_ticket_expires_at = ?,
                    deploy_ticket_consumed_at = NULL,
                    deploy_lease_id = NULL,
                    deploy_lease_started_at = NULL,
                    deploy_lease_expires_at = NULL,
                    deploy_phase = 'approved'
                WHERE candidate_id = ?
                  AND status IN (
                      'awaiting_approval',
                      'candidate_ready'
                  )
                  AND approval_code = ?
                """,
                (
                    now,
                    now,
                    digest,
                    salt,
                    expires_at,
                    candidate_id,
                    code,
                ),
            )

            if cursor.rowcount != 1:
                return (
                    False,
                    "race",
                    None,
                    None,
                )

            return (
                True,
                "approved",
                deploy_code,
                expires_at,
            )

    def _request_deploy_sync(
        self,
        candidate_id: int,
        code: str,
    ) -> tuple[
        bool,
        str,
    ]:
        now = self._utc_now()

        with self._connect() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT *
                FROM improvement_candidates
                WHERE candidate_id = ?
                """,
                (
                    candidate_id,
                ),
            ).fetchone()

            if row is None:
                return (
                    False,
                    "missing",
                )

            candidate = dict(
                row
            )

            if str(
                candidate.get(
                    "status"
                )
            ) != "approved":
                return (
                    False,
                    "invalid_state",
                )

            if candidate.get(
                "deploy_ticket_consumed_at"
            ):
                return (
                    False,
                    "ticket_used",
                )

            if self._timestamp_expired(
                candidate.get(
                    "deploy_ticket_expires_at"
                ),
                missing_is_expired=True,
            ):
                return (
                    False,
                    "ticket_expired",
                )

            salt = str(
                candidate.get(
                    "deploy_ticket_salt"
                )
                or ""
            )

            expected = str(
                candidate.get(
                    "deploy_ticket_hash"
                )
                or ""
            )

            if not salt or not expected:
                return (
                    False,
                    "ticket_missing",
                )

            supplied = self._ticket_digest(
                candidate_id,
                salt,
                code,
            )

            if not secrets.compare_digest(
                expected,
                supplied,
            ):
                return (
                    False,
                    "bad_code",
                )

            if not (
                str(
                    candidate.get(
                        "base_commit"
                    )
                    or ""
                ).strip()
                and str(
                    candidate.get(
                        "candidate_commit"
                    )
                    or ""
                ).strip()
                and str(
                    candidate.get(
                        "validated_patch_sha256"
                    )
                    or ""
                ).strip()
            ):
                return (
                    False,
                    "binding_missing",
                )

            cursor = connection.execute(
                """
                UPDATE improvement_candidates
                SET
                    status = 'deploy_requested',
                    updated_at = ?,
                    deploy_requested_at = ?,
                    deploy_ticket_consumed_at = ?,
                    deploy_ticket_hash = NULL,
                    deploy_ticket_salt = NULL,
                    deploy_lease_id = NULL,
                    deploy_lease_started_at = NULL,
                    deploy_lease_expires_at = NULL,
                    deploy_phase = 'requested'
                WHERE candidate_id = ?
                  AND status = 'approved'
                  AND deploy_ticket_consumed_at IS NULL
                """,
                (
                    now,
                    now,
                    now,
                    candidate_id,
                ),
            )

            if cursor.rowcount != 1:
                return (
                    False,
                    "race",
                )

            return (
                True,
                "deploy_requested",
            )

    async def approve_candidate(
        self,
        candidate_id: int,
        code: str,
        actor: str,
    ) -> ImprovementCommandResult:
        (
            success,
            reason,
            deploy_code,
            expires_at,
        ) = await asyncio.to_thread(
            self._approve_candidate_sync,
            candidate_id,
            code,
        )

        if not success:
            messages = {
                "missing": (
                    f"I can’t find improvement {candidate_id}."
                ),
                "invalid_state": (
                    f"Improvement {candidate_id} cannot "
                    "be approved in its current state."
                ),
                "bad_code": (
                    "That approval code is incorrect."
                ),
                "review_code_expired": (
                    "That approval code has expired. "
                    "The candidate must be reviewed again."
                ),
                "race": (
                    "The candidate changed while approval "
                    "was being recorded. Nothing was deployed."
                ),
            }

            return ImprovementCommandResult(
                True,
                False,
                messages.get(
                    reason,
                    "Approval failed safely.",
                ),
                f"improvement_approve_{reason}",
            )

        await self.audit(
            "candidate_approved",
            actor=actor,
            candidate_id=candidate_id,
            details={
                "deploy_ticket_expires_at": (
                    expires_at
                ),
            },
        )

        return ImprovementCommandResult(
            True,
            True,
            (
                f"Improvement {candidate_id} is approved. "
                f"Deployment code {deploy_code} is valid "
                "for 15 minutes. It has not been deployed."
            ),
            "improvement_approved",
            {
                "candidate_id": candidate_id,
                "deploy_code": deploy_code,
                "deploy_ticket_expires_at": expires_at,
            },
        )

    async def request_deploy(
        self,
        candidate_id: int,
        code: str,
        actor: str,
    ) -> ImprovementCommandResult:
        (
            success,
            reason,
        ) = await asyncio.to_thread(
            self._request_deploy_sync,
            candidate_id,
            code,
        )

        if not success:
            messages = {
                "missing": (
                    f"I can’t find improvement {candidate_id}."
                ),
                "invalid_state": (
                    f"Improvement {candidate_id} must be "
                    "approved before deployment can be requested."
                ),
                "ticket_used": (
                    "That deployment ticket has already been used."
                ),
                "ticket_expired": (
                    "That deployment ticket has expired. "
                    "Approve the candidate again after review."
                ),
                "ticket_missing": (
                    "This candidate has no valid deployment ticket."
                ),
                "bad_code": (
                    "That deployment code is incorrect."
                ),
                "binding_missing": (
                    "The candidate is not bound to an exact "
                    "validated commit, so deployment was refused."
                ),
                "race": (
                    "The candidate changed while the deployment "
                    "request was being recorded."
                ),
            }

            return ImprovementCommandResult(
                True,
                False,
                messages.get(
                    reason,
                    "Deployment request failed safely.",
                ),
                f"improvement_deploy_{reason}",
            )

        await self.audit(
            "candidate_deploy_requested",
            actor=actor,
            candidate_id=candidate_id,
            details={
                "ticket_consumed": True,
            },
        )

        return ImprovementCommandResult(
            True,
            True,
            (
                f"Deployment requested for improvement "
                f"{candidate_id}. The one-time deployment "
                "ticket has been consumed."
            ),
            "improvement_deploy_requested",
        )

    async def reject_candidate(self, candidate_id: int, actor: str) -> ImprovementCommandResult:
        candidate = await self.get_candidate(candidate_id)
        if not candidate:
            return ImprovementCommandResult(True, False, f"I can’t find improvement {candidate_id}.", "improvement_reject_missing")
        if str(candidate.get("status")) in {"deployed", "deploying", "rolled_back"}:
            return ImprovementCommandResult(
                True,
                False,
                f"Improvement {candidate_id} is {candidate.get('status')} and cannot simply be rejected.",
                "improvement_reject_invalid_state",
            )
        await asyncio.to_thread(self._set_candidate_status_sync, candidate_id, "rejected")
        await self.audit("candidate_rejected", actor=actor, candidate_id=candidate_id)
        return ImprovementCommandResult(True, True, f"Improvement {candidate_id} has been rejected.", "improvement_rejected")

    async def request_rollback(self, candidate_id: int, code: str, actor: str) -> ImprovementCommandResult:
        candidate = await self.get_candidate(candidate_id)
        if not candidate:
            return ImprovementCommandResult(True, False, f"I can’t find improvement {candidate_id}.", "improvement_rollback_missing")
        if str(candidate.get("status")) != "deployed":
            return ImprovementCommandResult(
                True,
                False,
                f"Improvement {candidate_id} is not currently deployed.",
                "improvement_rollback_invalid_state",
            )
        if not self._code_matches(candidate, code):
            return ImprovementCommandResult(True, False, "That rollback code is incorrect.", "improvement_rollback_bad_code")
        await asyncio.to_thread(
            self._set_candidate_status_sync,
            candidate_id,
            "rollback_requested",
            rollback_requested=True,
        )
        await self.audit("candidate_rollback_requested", actor=actor, candidate_id=candidate_id)
        return ImprovementCommandResult(
            True,
            True,
            f"Rollback requested for improvement {candidate_id}.",
            "improvement_rollback_requested",
        )

    async def status(self) -> dict[str, Any]:
        enabled = await self.enabled()
        failures = await self.list_failures(limit=200)
        candidates = await self.list_candidates(limit=200)
        heartbeat = await self.setting("worker_heartbeat", "")
        return {
            "enabled": enabled,
            "auto_prepare": self.auto_prepare,
            "repeat_threshold": self.repeat_threshold,
            "failure_count": len(failures),
            "open_failure_count": sum(
                1 for item in failures if item.get("status") not in {"resolved", "ignored"}
            ),
            "candidate_count": len(candidates),
            "queued_candidates": sum(1 for item in candidates if item.get("status") == "queued"),
            "awaiting_approval": sum(
                1 for item in candidates if item.get("status") in {"candidate_ready", "awaiting_approval"}
            ),
            "deploy_requested": sum(1 for item in candidates if item.get("status") == "deploy_requested"),
            "deployed": sum(1 for item in candidates if item.get("status") == "deployed"),
            "worker_heartbeat": heartbeat or None,
            "database_path": str(self.database_path),
        }

    def _audit_log_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT audit_id, created_at, event_type, actor, failure_id,
                       candidate_id, details_json
                FROM improvement_audit
                ORDER BY audit_id DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = self._load_json(item.pop("details_json", "{}"), {})
            items.append(item)
        return items

    async def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._audit_log_sync, limit)

    async def audit(
        self,
        event_type: str,
        *,
        actor: str,
        failure_id: int | None = None,
        candidate_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        def _write() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO improvement_audit (
                        created_at, event_type, actor, failure_id,
                        candidate_id, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._utc_now(),
                        event_type,
                        actor,
                        failure_id,
                        candidate_id,
                        self._json(details or {}),
                    ),
                )
        await asyncio.to_thread(_write)

    def _mark_recent_failure_confirmed_resolved_sync(self, conversation_id: str) -> None:
        now = self._utc_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT failure_id FROM improvement_failures
                WHERE conversation_id = ? AND status = 'deployed'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE improvement_failures SET status = 'resolved', updated_at = ? WHERE failure_id = ?",
                    (now, int(row["failure_id"])),
                )

    async def _mark_recent_failure_confirmed_resolved(self, conversation_id: str) -> None:
        await asyncio.to_thread(self._mark_recent_failure_confirmed_resolved_sync, conversation_id)

    async def handle_command(
        self,
        *,
        text: str,
        actor: UserContext,
        conversation_id: str,
    ) -> ImprovementCommandResult:
        user_text = str(text or "").strip()
        if not user_text:
            return ImprovementCommandResult(False)

        is_improvement_command = any(
            pattern.fullmatch(user_text)
            for pattern in (
                _PREPARE_LAST_PATTERN,
                _PREPARE_ID_PATTERN,
                _APPROVE_PATTERN,
                _DEPLOY_PATTERN,
                _REJECT_PATTERN,
                _ROLLBACK_PATTERN,
                _STATUS_PATTERN,
                _FAILURES_PATTERN,
                _CANDIDATES_PATTERN,
                _STOP_PATTERN,
                _RESUME_PATTERN,
                _RECORD_PATTERN,
            )
        )
        if not is_improvement_command:
            return ImprovementCommandResult(False)

        if not actor.can_admin:
            return ImprovementCommandResult(
                True,
                False,
                "Self-improvement controls are available only to Aaron’s authenticated administrator account.",
                "self_improvement_forbidden",
            )

        actor_name = actor.display_name

        if _STOP_PATTERN.fullmatch(user_text):
            await self.set_enabled(False, actor_name)
            return ImprovementCommandResult(
                True,
                True,
                "Self-improvement is stopped. Jarvis will keep working normally, but no fixes will be generated or deployed.",
                "self_improvement_stopped",
            )

        if _RESUME_PATTERN.fullmatch(user_text):
            await self.set_enabled(True, actor_name)
            return ImprovementCommandResult(
                True,
                True,
                "Self-improvement is enabled again. Code deployment still requires approval.",
                "self_improvement_resumed",
            )

        if _STATUS_PATTERN.fullmatch(user_text):
            status = await self.status()
            state = "enabled" if status["enabled"] else "disabled"
            response = (
                f"Self-improvement is {state}. There are {status['open_failure_count']} open mistakes, "
                f"{status['queued_candidates']} queued candidates and {status['awaiting_approval']} awaiting approval."
            )
            return ImprovementCommandResult(True, True, response, "self_improvement_status", status)

        if _FAILURES_PATTERN.fullmatch(user_text):
            failures = await self.list_failures(limit=5)
            if not failures:
                return ImprovementCommandResult(True, True, "I haven’t recorded any improvement failures yet.", "self_improvement_failures")
            parts = [
                f"{item['failure_id']}: {item['summary']} ({item['status']})"
                for item in failures
            ]
            return ImprovementCommandResult(
                True,
                True,
                "Recent mistakes are " + "; ".join(parts) + ".",
                "self_improvement_failures",
                {"failures": failures},
            )

        if _CANDIDATES_PATTERN.fullmatch(user_text):
            candidates = await self.list_candidates(limit=5)
            if not candidates:
                return ImprovementCommandResult(True, True, "There are no improvement candidates yet.", "self_improvement_candidates")
            parts = []
            for item in candidates:
                summary = str(item.get("summary") or f"failure {item.get('failure_id')}")
                parts.append(f"{item['candidate_id']}: {summary} ({item['status']})")
            return ImprovementCommandResult(
                True,
                True,
                "Recent improvements are " + "; ".join(parts) + ".",
                "self_improvement_candidates",
                {"candidates": candidates},
            )

        if _RECORD_PATTERN.fullmatch(user_text):
            source = await self.last_interaction(conversation_id)
            if not source:
                return ImprovementCommandResult(True, False, "There isn’t a previous Jarvis response to record.", "self_improvement_record_missing")
            failure_id = await self.record_failure(
                source=source,
                correction=user_text,
                explicit=True,
                summary=f"Aaron explicitly marked interaction {source['interaction_id']} as wrong.",
            )
            return ImprovementCommandResult(
                True,
                True,
                f"Recorded as mistake {failure_id}. I’ll prepare a fix safely without changing the live system.",
                "self_improvement_recorded",
                {"failure_id": failure_id},
            )

        if _PREPARE_LAST_PATTERN.fullmatch(user_text):
            failure = await self.last_failure()
            if not failure:
                return ImprovementCommandResult(True, False, "There isn’t a recorded mistake to prepare a fix for.", "self_improvement_prepare_missing")
            ok, candidate_id, status = await self.queue_failure(int(failure["failure_id"]), actor_name)
            if not ok:
                return ImprovementCommandResult(True, False, status, "self_improvement_prepare_failed")
            return ImprovementCommandResult(
                True,
                True,
                f"Improvement {candidate_id} is queued for isolated testing. The live Jarvis has not been changed.",
                "self_improvement_queued",
                {"failure_id": failure["failure_id"], "candidate_id": candidate_id, "status": status},
            )

        match = _PREPARE_ID_PATTERN.fullmatch(user_text)
        if match:
            failure_id = int(match.group(1))
            ok, candidate_id, status = await self.queue_failure(failure_id, actor_name)
            if not ok:
                return ImprovementCommandResult(True, False, status, "self_improvement_prepare_failed")
            return ImprovementCommandResult(
                True,
                True,
                f"Improvement {candidate_id} is queued for isolated testing. The live Jarvis has not been changed.",
                "self_improvement_queued",
                {"failure_id": failure_id, "candidate_id": candidate_id, "status": status},
            )

        match = _APPROVE_PATTERN.fullmatch(user_text)
        if match:
            return await self.approve_candidate(int(match.group(1)), match.group(2), actor_name)

        match = _DEPLOY_PATTERN.fullmatch(user_text)
        if match:
            return await self.request_deploy(int(match.group(1)), match.group(2), actor_name)

        match = _REJECT_PATTERN.fullmatch(user_text)
        if match:
            return await self.reject_candidate(int(match.group(1)), actor_name)

        match = _ROLLBACK_PATTERN.fullmatch(user_text)
        if match:
            return await self.request_rollback(int(match.group(1)), match.group(2), actor_name)

        return ImprovementCommandResult(False)
