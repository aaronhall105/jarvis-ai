import asyncio
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_ROLES = {
    "user",
    "assistant",
    "system",
    "tool",
}


@dataclass
class Conversation:
    conversation_id: str
    title: str | None
    summary: str | None
    source: str
    created_at: str
    updated_at: str


@dataclass
class ConversationMessage:
    message_id: int
    conversation_id: str
    role: str
    content: str
    created_at: str


class ConversationEngine:
    """
    Persistent multi-turn conversation storage for Jarvis Core.

    Stores:
    - Conversations
    - User and assistant messages
    - Conversation titles
    - Conversation summaries
    - Source information such as web or Home Assistant
    """

    def __init__(
        self,
        database_path: str,
        default_history_limit: int = 20,
    ) -> None:
        self.database_path = Path(database_path)
        self.default_history_limit = max(1, default_history_limit)

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialise_database()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA journal_mode = WAL"
        )
        connection.execute(
            "PRAGMA synchronous = NORMAL"
        )

        return connection

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    summary TEXT,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    idempotency_key TEXT,
                    FOREIGN KEY (conversation_id)
                        REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS
                    idx_messages_conversation_id
                ON messages (
                    conversation_id,
                    message_id
                );

                CREATE INDEX IF NOT EXISTS
                    idx_conversations_updated_at
                ON conversations (
                    updated_at DESC
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE messages ADD COLUMN idempotency_key TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_idempotency
                ON messages(conversation_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )

    def _create_conversation_sync(
        self,
        source: str,
        title: str | None,
        conversation_id: str | None,
    ) -> Conversation:
        resolved_id = (
            conversation_id.strip()
            if conversation_id
            and conversation_id.strip()
            else str(uuid.uuid4())
        )

        resolved_source = (
            source.strip()
            if source.strip()
            else "unknown"
        )

        now = self._utc_now()

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT
                    conversation_id,
                    title,
                    summary,
                    source,
                    created_at,
                    updated_at
                FROM conversations
                WHERE conversation_id = ?
                """,
                (resolved_id,),
            ).fetchone()

            if existing is not None:
                return Conversation(**dict(existing))

            connection.execute(
                """
                INSERT INTO conversations (
                    conversation_id,
                    title,
                    summary,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, NULL, ?, ?, ?)
                """,
                (
                    resolved_id,
                    title,
                    resolved_source,
                    now,
                    now,
                ),
            )

        return Conversation(
            conversation_id=resolved_id,
            title=title,
            summary=None,
            source=resolved_source,
            created_at=now,
            updated_at=now,
        )

    async def create_conversation(
        self,
        source: str = "unknown",
        title: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        conversation = await asyncio.to_thread(
            self._create_conversation_sync,
            source,
            title,
            conversation_id,
        )

        return asdict(conversation)

    def _get_conversation_sync(
        self,
        conversation_id: str,
    ) -> Conversation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    conversation_id,
                    title,
                    summary,
                    source,
                    created_at,
                    updated_at
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()

        if row is None:
            return None

        return Conversation(**dict(row))

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        conversation = await asyncio.to_thread(
            self._get_conversation_sync,
            conversation_id,
        )

        if conversation is None:
            return None

        return asdict(conversation)

    async def ensure_conversation(
        self,
        conversation_id: str | None = None,
        source: str = "unknown",
        title: str | None = None,
    ) -> dict[str, Any]:
        if conversation_id:
            existing = await self.get_conversation(
                conversation_id
            )

            if existing is not None:
                return existing

        return await self.create_conversation(
            source=source,
            title=title,
            conversation_id=conversation_id,
        )

    def _add_message_sync(
        self,
        conversation_id: str,
        role: str,
        content: str,
        idempotency_key: str | None = None,
    ) -> ConversationMessage:
        resolved_role = role.strip().lower()
        resolved_content = content.strip()
        resolved_idempotency_key = (
            idempotency_key.strip()
            if idempotency_key is not None and idempotency_key.strip()
            else None
        )

        if resolved_role not in VALID_ROLES:
            raise ValueError(
                f"Unsupported conversation role: {role}"
            )

        if not resolved_content:
            raise ValueError(
                "Conversation message content cannot be empty."
            )

        now = self._utc_now()

        with self._connect() as connection:
            conversation = connection.execute(
                """
                SELECT conversation_id
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()

            if conversation is None:
                raise ValueError(
                    "Conversation does not exist: "
                    f"{conversation_id}"
                )

            try:
                cursor = connection.execute(
                    """
                    INSERT INTO messages (
                        conversation_id, role, content, created_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        resolved_role,
                        resolved_content,
                        now,
                        resolved_idempotency_key,
                    ),
                )
                message_id = int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                if resolved_idempotency_key is None:
                    raise
                existing = connection.execute(
                    """
                    SELECT message_id, role, content, created_at
                    FROM messages
                    WHERE conversation_id=? AND idempotency_key=?
                    """,
                    (conversation_id, resolved_idempotency_key),
                ).fetchone()
                if existing is None:
                    raise
                return ConversationMessage(
                    message_id=int(existing["message_id"]),
                    conversation_id=conversation_id,
                    role=str(existing["role"]),
                    content=str(existing["content"]),
                    created_at=str(existing["created_at"]),
                )

            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    now,
                    conversation_id,
                ),
            )

        return ConversationMessage(
            message_id=message_id,
            conversation_id=conversation_id,
            role=resolved_role,
            content=resolved_content,
            created_at=now,
        )

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
    ) -> dict[str, Any]:
        message = await asyncio.to_thread(
            self._add_message_sync,
            conversation_id,
            role,
            content,
            None,
        )

        return asdict(message)

    async def add_user_message(
        self,
        conversation_id: str,
        content: str,
    ) -> dict[str, Any]:
        return await self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=content,
        )

    async def add_user_message_once(
        self,
        conversation_id: str,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Append one request message exactly once across transport retries."""
        resolved_key = idempotency_key.strip()
        if not resolved_key:
            raise ValueError("An idempotency key is required for one-time delivery.")
        message = await asyncio.to_thread(
            self._add_message_sync,
            conversation_id,
            "user",
            content,
            resolved_key,
        )
        return asdict(message)

    async def add_assistant_message(
        self,
        conversation_id: str,
        content: str,
    ) -> dict[str, Any]:
        return await self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
        )

    async def add_assistant_message_once(
        self,
        conversation_id: str,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Append one background result exactly once across worker retries."""
        resolved_key = idempotency_key.strip()
        if not resolved_key:
            raise ValueError("An idempotency key is required for one-time delivery.")
        message = await asyncio.to_thread(
            self._add_message_sync,
            conversation_id,
            "assistant",
            content,
            resolved_key,
        )
        return asdict(message)

    def _get_messages_sync(
        self,
        conversation_id: str,
        limit: int,
    ) -> list[ConversationMessage]:
        safe_limit = max(1, min(limit, 200))

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    message_id,
                    conversation_id,
                    role,
                    content,
                    created_at
                FROM (
                    SELECT
                        message_id,
                        conversation_id,
                        role,
                        content,
                        created_at
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY message_id DESC
                    LIMIT ?
                )
                ORDER BY message_id ASC
                """,
                (
                    conversation_id,
                    safe_limit,
                ),
            ).fetchall()

        return [
            ConversationMessage(**dict(row))
            for row in rows
        ]

    async def get_messages(
        self,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        resolved_limit = (
            limit
            if limit is not None
            else self.default_history_limit
        )

        messages = await asyncio.to_thread(
            self._get_messages_sync,
            conversation_id,
            resolved_limit,
        )

        return [
            asdict(message)
            for message in messages
        ]

    async def get_ai_history(
        self,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        messages = await self.get_messages(
            conversation_id=conversation_id,
            limit=limit,
        )

        return [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in messages
            if message["role"] in {
                "user",
                "assistant",
                "system",
            }
        ]

    def _list_conversations_sync(
        self,
        limit: int,
        conversation_id_prefix: str | None = None,
    ) -> list[Conversation]:
        safe_limit = max(1, min(limit, 100))

        with self._connect() as connection:
            if conversation_id_prefix is None:
                rows = connection.execute(
                    """
                    SELECT
                        conversation_id,
                        title,
                        summary,
                        source,
                        created_at,
                        updated_at
                    FROM conversations
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                        conversation_id,
                        title,
                        summary,
                        source,
                        created_at,
                        updated_at
                    FROM conversations
                    WHERE substr(conversation_id, 1, ?) = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (
                        len(conversation_id_prefix),
                        conversation_id_prefix,
                        safe_limit,
                    ),
                ).fetchall()

        return [
            Conversation(**dict(row))
            for row in rows
        ]

    async def list_conversations(
        self,
        limit: int = 50,
        conversation_id_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        conversations = await asyncio.to_thread(
            self._list_conversations_sync,
            limit,
            conversation_id_prefix,
        )

        return [
            asdict(conversation)
            for conversation in conversations
        ]

    def _rename_conversation_sync(
        self,
        conversation_id: str,
        title: str,
    ) -> bool:
        resolved_title = title.strip()

        if not resolved_title:
            raise ValueError(
                "Conversation title cannot be empty."
            )

        now = self._utc_now()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET
                    title = ?,
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    resolved_title,
                    now,
                    conversation_id,
                ),
            )

        return cursor.rowcount > 0

    async def rename_conversation(
        self,
        conversation_id: str,
        title: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._rename_conversation_sync,
            conversation_id,
            title,
        )

    def _update_summary_sync(
        self,
        conversation_id: str,
        summary: str | None,
    ) -> bool:
        resolved_summary = (
            summary.strip()
            if summary
            and summary.strip()
            else None
        )

        now = self._utc_now()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET
                    summary = ?,
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    resolved_summary,
                    now,
                    conversation_id,
                ),
            )

        return cursor.rowcount > 0

    async def update_summary(
        self,
        conversation_id: str,
        summary: str | None,
    ) -> bool:
        return await asyncio.to_thread(
            self._update_summary_sync,
            conversation_id,
            summary,
        )

    def _delete_conversation_sync(
        self,
        conversation_id: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            )

        return cursor.rowcount > 0

    async def delete_conversation(
        self,
        conversation_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._delete_conversation_sync,
            conversation_id,
        )

    def _message_count_sync(
        self,
        conversation_id: str,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM messages
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()

        return int(row["total"])

    async def message_count(
        self,
        conversation_id: str,
    ) -> int:
        return await asyncio.to_thread(
            self._message_count_sync,
            conversation_id,
        )
