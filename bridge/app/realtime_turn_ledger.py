from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TurnRecord:
    client_kind: str
    device_id: str
    conversation_id: str
    client_turn_id: int
    command_sha256: str
    status: str
    response: dict[str, Any] | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class TurnClaim:
    disposition: str
    record: TurnRecord

    @property
    def is_new(self) -> bool:
        return self.disposition == "new"

    @property
    def is_duplicate(self) -> bool:
        return self.disposition == "duplicate"

    @property
    def is_conflict(self) -> bool:
        return self.disposition == "conflict"


class RealtimeTurnLedger:
    """
    Durable admission ledger for mobile realtime turns.

    Important semantics:

    * A client turn is uniquely identified by:
          client_kind
          device_id
          conversation_id
          client_turn_id

    * The first command claiming a key wins.

    * Repeating the same key with the same command is a duplicate
      and MUST NOT start a second side-effecting brain turn.

    * Repeating the same key with a different command is a protocol
      conflict and MUST NOT execute either as a replacement.

    The ledger is deliberately independent from one WebSocket
    connection so ownership survives LAN/Tailscale/mobile handover.
    """

    VALID_STATUSES = {
        "accepted",
        "completed",
        "cancelled",
        "interrupted",
    }

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._db.execute(
            "PRAGMA busy_timeout = 5000"
        )
        self._db.execute(
            "PRAGMA journal_mode = WAL"
        )
        self._db.execute(
            "PRAGMA synchronous = NORMAL"
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS realtime_turn_ledger (
                client_kind TEXT NOT NULL,
                device_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                client_turn_id INTEGER NOT NULL,
                command_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (
                    client_kind,
                    device_id,
                    conversation_id,
                    client_turn_id
                )
            )
            """
        )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_realtime_turn_ledger_updated
            ON realtime_turn_ledger(updated_at)
            """
        )

    @staticmethod
    def normalise_command(
        command: Any,
    ) -> str:
        return " ".join(
            str(command or "").split()
        ).strip()

    @classmethod
    def command_digest(
        cls,
        command: Any,
    ) -> str:
        cleaned = cls.normalise_command(
            command
        )
        return hashlib.sha256(
            cleaned.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _clean_identity(
        value: Any,
        fallback: str,
        limit: int = 200,
    ) -> str:
        cleaned = str(
            value or ""
        ).strip()
        if not cleaned:
            cleaned = fallback
        return cleaned[:limit]

    def claim(
        self,
        *,
        client_kind: str,
        device_id: str,
        conversation_id: str,
        client_turn_id: int,
        command: str,
    ) -> TurnClaim:
        turn_id = int(client_turn_id)

        if turn_id <= 0:
            raise ValueError(
                "client_turn_id must be positive"
            )

        cleaned_command = (
            self.normalise_command(command)
        )

        if not cleaned_command:
            raise ValueError(
                "command must not be empty"
            )

        key = (
            self._clean_identity(
                client_kind,
                "mobile",
                40,
            ),
            self._clean_identity(
                device_id,
                "unknown-device",
            ),
            self._clean_identity(
                conversation_id,
                "unknown-conversation",
            ),
            turn_id,
        )

        digest = self.command_digest(
            cleaned_command
        )
        now = float(self._clock())

        with self._lock:
            cursor = self._db.cursor()

            try:
                cursor.execute(
                    "BEGIN IMMEDIATE"
                )

                row = cursor.execute(
                    """
                    SELECT
                        client_kind,
                        device_id,
                        conversation_id,
                        client_turn_id,
                        command_sha256,
                        status,
                        response_json,
                        created_at,
                        updated_at
                    FROM realtime_turn_ledger
                    WHERE client_kind = ?
                      AND device_id = ?
                      AND conversation_id = ?
                      AND client_turn_id = ?
                    """,
                    key,
                ).fetchone()

                if row is None:
                    cursor.execute(
                        """
                        INSERT INTO realtime_turn_ledger (
                            client_kind,
                            device_id,
                            conversation_id,
                            client_turn_id,
                            command_sha256,
                            status,
                            response_json,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            *key,
                            digest,
                            "accepted",
                            now,
                            now,
                        ),
                    )

                    cursor.execute(
                        "COMMIT"
                    )

                    return TurnClaim(
                        "new",
                        TurnRecord(
                            client_kind=key[0],
                            device_id=key[1],
                            conversation_id=key[2],
                            client_turn_id=turn_id,
                            command_sha256=digest,
                            status="accepted",
                            response=None,
                            created_at=now,
                            updated_at=now,
                        ),
                    )

                cursor.execute(
                    "COMMIT"
                )

                existing = self._row_to_record(
                    row
                )

                disposition = (
                    "duplicate"
                    if existing.command_sha256
                    == digest
                    else "conflict"
                )

                return TurnClaim(
                    disposition,
                    existing,
                )

            except Exception:
                try:
                    cursor.execute(
                        "ROLLBACK"
                    )
                except sqlite3.Error:
                    pass
                raise
            finally:
                cursor.close()

    def lookup(
        self,
        *,
        client_kind: str,
        device_id: str,
        conversation_id: str,
        client_turn_id: int,
    ) -> TurnRecord | None:
        key = (
            self._clean_identity(
                client_kind,
                "mobile",
                40,
            ),
            self._clean_identity(
                device_id,
                "unknown-device",
            ),
            self._clean_identity(
                conversation_id,
                "unknown-conversation",
            ),
            int(client_turn_id),
        )

        with self._lock:
            row = self._db.execute(
                """
                SELECT
                    client_kind,
                    device_id,
                    conversation_id,
                    client_turn_id,
                    command_sha256,
                    status,
                    response_json,
                    created_at,
                    updated_at
                FROM realtime_turn_ledger
                WHERE client_kind = ?
                  AND device_id = ?
                  AND conversation_id = ?
                  AND client_turn_id = ?
                """,
                key,
            ).fetchone()

        if row is None:
            return None

        return self._row_to_record(
            row
        )

    def mark_completed(
        self,
        *,
        client_kind: str,
        device_id: str,
        conversation_id: str,
        client_turn_id: int,
        response: dict[str, Any] | None = None,
    ) -> TurnRecord:
        return self._mark(
            client_kind=client_kind,
            device_id=device_id,
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            status="completed",
            response=response,
        )

    def mark_cancelled(
        self,
        *,
        client_kind: str,
        device_id: str,
        conversation_id: str,
        client_turn_id: int,
    ) -> TurnRecord:
        return self._mark(
            client_kind=client_kind,
            device_id=device_id,
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            status="cancelled",
        )

    def mark_interrupted(
        self,
        *,
        client_kind: str,
        device_id: str,
        conversation_id: str,
        client_turn_id: int,
    ) -> TurnRecord:
        return self._mark(
            client_kind=client_kind,
            device_id=device_id,
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            status="interrupted",
        )

    def _mark(
        self,
        *,
        client_kind: str,
        device_id: str,
        conversation_id: str,
        client_turn_id: int,
        status: str,
        response: dict[str, Any] | None = None,
    ) -> TurnRecord:
        if status not in self.VALID_STATUSES:
            raise ValueError(
                f"invalid turn status: {status}"
            )

        key = (
            self._clean_identity(
                client_kind,
                "mobile",
                40,
            ),
            self._clean_identity(
                device_id,
                "unknown-device",
            ),
            self._clean_identity(
                conversation_id,
                "unknown-conversation",
            ),
            int(client_turn_id),
        )

        now = float(self._clock())

        response_json = (
            json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if response is not None
            else None
        )

        with self._lock:
            cursor = self._db.execute(
                """
                UPDATE realtime_turn_ledger
                SET
                    status = ?,
                    response_json = ?,
                    updated_at = ?
                WHERE client_kind = ?
                  AND device_id = ?
                  AND conversation_id = ?
                  AND client_turn_id = ?
                """,
                (
                    status,
                    response_json,
                    now,
                    *key,
                ),
            )

            if cursor.rowcount != 1:
                raise KeyError(
                    "realtime turn does not exist"
                )

        record = self.lookup(
            client_kind=key[0],
            device_id=key[1],
            conversation_id=key[2],
            client_turn_id=key[3],
        )

        if record is None:
            raise RuntimeError(
                "updated realtime turn disappeared"
            )

        return record

    def prune(
        self,
        *,
        max_age_seconds: float,
    ) -> int:
        age = max(
            0.0,
            float(max_age_seconds),
        )
        cutoff = (
            float(self._clock()) - age
        )

        with self._lock:
            cursor = self._db.execute(
                """
                DELETE FROM realtime_turn_ledger
                WHERE updated_at < ?
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row_to_record(
        row: tuple[Any, ...],
    ) -> TurnRecord:
        response: dict[str, Any] | None = None

        if row[6]:
            parsed = json.loads(
                str(row[6])
            )
            if isinstance(parsed, dict):
                response = parsed

        return TurnRecord(
            client_kind=str(row[0]),
            device_id=str(row[1]),
            conversation_id=str(row[2]),
            client_turn_id=int(row[3]),
            command_sha256=str(row[4]),
            status=str(row[5]),
            response=response,
            created_at=float(row[7]),
            updated_at=float(row[8]),
        )
