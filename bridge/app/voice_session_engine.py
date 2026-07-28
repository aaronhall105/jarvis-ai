from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class VoiceSessionRecord:
    session_id: str
    conversation_id: str
    user_key: str
    satellite_id: str | None
    device_id: str | None
    endpoint_kind: str
    status: str
    turn_count: int
    started_at: str
    last_activity_at: str
    expires_at: str
    last_intent: str | None
    close_reason: str | None
    interrupt_count: int
    last_interrupted_at: str | None
    last_interrupt_reason: str | None
    last_interrupt_media_player: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class VoiceSessionEngine:
    """Persistent lifecycle and interruption registry for voice sessions."""

    VERSION = "17.0.3"

    def __init__(
        self,
        database_path: str = "/app/data/jarvis_voice_sessions.db",
        *,
        idle_timeout_seconds: int = 45,
        max_session_seconds: int = 300,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.idle_timeout_seconds = max(10, min(int(idle_timeout_seconds), 300))
        self.max_session_seconds = max(60, min(int(max_session_seconds), 1800))
        self._lock = asyncio.Lock()
        self._last_error: str | None = None
        self._initialise_database()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _columns(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(voice_sessions)").fetchall()
        }

    def _initialise_database(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_sessions (
                    session_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_key TEXT NOT NULL,
                    satellite_id TEXT,
                    device_id TEXT,
                    endpoint_kind TEXT NOT NULL DEFAULT 'unknown',
                    status TEXT NOT NULL,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_intent TEXT,
                    close_reason TEXT,
                    interrupt_count INTEGER NOT NULL DEFAULT 0,
                    last_interrupted_at TEXT,
                    last_interrupt_reason TEXT,
                    last_interrupt_media_player TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_voice_sessions_status_activity
                ON voice_sessions (status, last_activity_at DESC);

                CREATE INDEX IF NOT EXISTS idx_voice_sessions_endpoint
                ON voice_sessions (satellite_id, device_id, user_key);
                """
            )
            columns = self._columns(connection)
            migrations = {
                "endpoint_kind": (
                    "ALTER TABLE voice_sessions "
                    "ADD COLUMN endpoint_kind TEXT NOT NULL DEFAULT 'unknown'"
                ),
                "interrupt_count": (
                    "ALTER TABLE voice_sessions "
                    "ADD COLUMN interrupt_count INTEGER NOT NULL DEFAULT 0"
                ),
                "last_interrupted_at": (
                    "ALTER TABLE voice_sessions ADD COLUMN last_interrupted_at TEXT"
                ),
                "last_interrupt_reason": (
                    "ALTER TABLE voice_sessions ADD COLUMN last_interrupt_reason TEXT"
                ),
                "last_interrupt_media_player": (
                    "ALTER TABLE voice_sessions "
                    "ADD COLUMN last_interrupt_media_player TEXT"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(statement)

    def _expire_stale_sync(self, now: datetime) -> int:
        now_text = self._iso(now)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE voice_sessions
                SET status = 'expired', close_reason = COALESCE(close_reason, 'timeout')
                WHERE status = 'active' AND expires_at <= ?
                """,
                (now_text,),
            )
            return int(cursor.rowcount)

    async def expire_stale(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._expire_stale_sync, self._utc_now())

    def _touch_sync(
        self,
        *,
        session_id: str,
        conversation_id: str,
        user_key: str,
        satellite_id: str | None,
        device_id: str | None,
        endpoint_kind: str | None,
        turn_index: int | None,
        intent: str | None,
        now: datetime,
    ) -> VoiceSessionRecord:
        clean_session = str(session_id or "").strip()
        clean_conversation = str(conversation_id or "").strip()
        clean_user = str(user_key or "unknown").strip() or "unknown"
        clean_endpoint_kind = str(endpoint_kind or "unknown").strip().casefold() or "unknown"
        if clean_endpoint_kind not in {"satellite", "mobile_app", "unknown"}:
            clean_endpoint_kind = "unknown"
        if not clean_session:
            raise ValueError("voice session ID is required")
        if not clean_conversation:
            raise ValueError("conversation ID is required")

        self._expire_stale_sync(now)
        now_text = self._iso(now)
        idle_expiry = now + timedelta(seconds=self.idle_timeout_seconds)

        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM voice_sessions WHERE session_id = ?",
                (clean_session,),
            ).fetchone()

            if existing is None:
                started_at = now
                hard_expiry = started_at + timedelta(seconds=self.max_session_seconds)
                expires_at = min(idle_expiry, hard_expiry)
                turn_count = max(1, int(turn_index or 1))
                connection.execute(
                    """
                    INSERT INTO voice_sessions (
                        session_id, conversation_id, user_key, satellite_id,
                        device_id, endpoint_kind, status, turn_count, started_at,
                        last_activity_at, expires_at, last_intent, close_reason,
                        interrupt_count, last_interrupted_at,
                        last_interrupt_reason, last_interrupt_media_player
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL,
                              0, NULL, NULL, NULL)
                    """,
                    (
                        clean_session,
                        clean_conversation,
                        clean_user,
                        str(satellite_id or "").strip() or None,
                        str(device_id or "").strip() or None,
                        clean_endpoint_kind,
                        turn_count,
                        now_text,
                        now_text,
                        self._iso(expires_at),
                        str(intent or "").strip() or None,
                    ),
                )
            else:
                started_at = self._parse(str(existing["started_at"])) or now
                hard_expiry = started_at + timedelta(seconds=self.max_session_seconds)
                expires_at = min(idle_expiry, hard_expiry)
                current_turn = int(existing["turn_count"] or 0)
                turn_count = max(current_turn + 1, int(turn_index or 0))
                connection.execute(
                    """
                    UPDATE voice_sessions
                    SET conversation_id = ?, user_key = ?, satellite_id = ?,
                        device_id = ?, endpoint_kind = ?, status = 'active', turn_count = ?,
                        last_activity_at = ?, expires_at = ?, last_intent = ?,
                        close_reason = NULL
                    WHERE session_id = ?
                    """,
                    (
                        clean_conversation,
                        clean_user,
                        str(satellite_id or "").strip() or None,
                        str(device_id or "").strip() or None,
                        clean_endpoint_kind,
                        turn_count,
                        now_text,
                        self._iso(expires_at),
                        str(intent or "").strip() or None,
                        clean_session,
                    ),
                )

            row = connection.execute(
                "SELECT * FROM voice_sessions WHERE session_id = ?",
                (clean_session,),
            ).fetchone()

        if row is None:
            raise RuntimeError("voice session could not be stored")
        return VoiceSessionRecord(**dict(row))

    async def touch(
        self,
        *,
        session_id: str,
        conversation_id: str,
        user_key: str,
        satellite_id: str | None = None,
        device_id: str | None = None,
        endpoint_kind: str | None = None,
        turn_index: int | None = None,
        intent: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            try:
                record = await asyncio.to_thread(
                    self._touch_sync,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    user_key=user_key,
                    satellite_id=satellite_id,
                    device_id=device_id,
                    endpoint_kind=endpoint_kind,
                    turn_index=turn_index,
                    intent=intent,
                    now=self._utc_now(),
                )
                self._last_error = None
                return record.as_dict()
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def _record_interrupt_sync(
        self,
        session_id: str,
        reason: str,
        media_player_entity_id: str | None,
        now: datetime,
    ) -> VoiceSessionRecord | None:
        clean_session = str(session_id or "").strip()
        if not clean_session:
            return None
        clean_reason = str(reason or "new_voice_request").strip()[:100] or "new_voice_request"
        clean_player = str(media_player_entity_id or "").strip()[:255] or None
        now_text = self._iso(now)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE voice_sessions
                SET interrupt_count = interrupt_count + 1,
                    last_interrupted_at = ?,
                    last_interrupt_reason = ?,
                    last_interrupt_media_player = ?,
                    last_activity_at = ?
                WHERE session_id = ? AND status = 'active'
                """,
                (now_text, clean_reason, clean_player, now_text, clean_session),
            )
            if not cursor.rowcount:
                return None
            row = connection.execute(
                "SELECT * FROM voice_sessions WHERE session_id = ?",
                (clean_session,),
            ).fetchone()
        return VoiceSessionRecord(**dict(row)) if row is not None else None

    async def record_interrupt(
        self,
        session_id: str,
        *,
        reason: str = "new_voice_request",
        media_player_entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._lock:
            try:
                record = await asyncio.to_thread(
                    self._record_interrupt_sync,
                    session_id,
                    reason,
                    media_player_entity_id,
                    self._utc_now(),
                )
                self._last_error = None
                return record.as_dict() if record is not None else None
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def _close_sync(self, session_id: str, reason: str, now: datetime) -> bool:
        clean_session = str(session_id or "").strip()
        if not clean_session:
            return False
        clean_reason = str(reason or "closed").strip()[:100] or "closed"
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE voice_sessions
                SET status = 'closed', close_reason = ?, last_activity_at = ?,
                    expires_at = ?
                WHERE session_id = ? AND status = 'active'
                """,
                (clean_reason, self._iso(now), self._iso(now), clean_session),
            )
            return bool(cursor.rowcount)

    async def close(self, session_id: str, reason: str = "closed") -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._close_sync,
                session_id,
                reason,
                self._utc_now(),
            )

    def _list_sync(self, active_only: bool, limit: int) -> list[VoiceSessionRecord]:
        safe_limit = max(1, min(int(limit), 200))
        query = "SELECT * FROM voice_sessions"
        if active_only:
            query += " WHERE status = 'active'"
        query += " ORDER BY last_activity_at DESC LIMIT ?"
        with self._connection() as connection:
            rows = connection.execute(query, (safe_limit,)).fetchall()
        return [VoiceSessionRecord(**dict(row)) for row in rows]

    async def list_sessions(
        self,
        *,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self.expire_stale()
        records = await asyncio.to_thread(self._list_sync, active_only, limit)
        return [record.as_dict() for record in records]

    def _counts_sync(self) -> tuple[dict[str, int], dict[str, int], int, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM voice_sessions GROUP BY status"
            ).fetchall()
            endpoint_rows = connection.execute(
                "SELECT endpoint_kind, COUNT(*) AS total FROM voice_sessions GROUP BY endpoint_kind"
            ).fetchall()
            totals = connection.execute(
                """
                SELECT
                    COALESCE(SUM(interrupt_count), 0) AS total_interrupts,
                    COALESCE(SUM(CASE WHEN interrupt_count > 0 THEN 1 ELSE 0 END), 0)
                        AS interrupted_sessions
                FROM voice_sessions
                """
            ).fetchone()
        counts = {str(row["status"]): int(row["total"]) for row in rows}
        endpoint_counts = {str(row["endpoint_kind"]): int(row["total"]) for row in endpoint_rows}
        return (
            counts,
            endpoint_counts,
            int(totals["total_interrupts"] if totals is not None else 0),
            int(totals["interrupted_sessions"] if totals is not None else 0),
        )

    async def status(self) -> dict[str, Any]:
        await self.expire_stale()
        counts, endpoint_counts, total_interrupts, interrupted_sessions = await asyncio.to_thread(
            self._counts_sync
        )
        return {
            "version": self.VERSION,
            "enabled": True,
            "database": str(self.database_path),
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "max_session_seconds": self.max_session_seconds,
            "session_counts": counts,
            "active_count": int(counts.get("active", 0)),
            "endpoint_counts": endpoint_counts,
            "total_interrupts": total_interrupts,
            "interrupted_sessions": interrupted_sessions,
            "last_error": self._last_error,
        }
