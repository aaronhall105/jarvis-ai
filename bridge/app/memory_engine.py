import asyncio
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryError(RuntimeError):
    """Raised when a memory operation cannot be completed."""


class MemoryEngine:
    VALID_CATEGORIES = {
        "personal",
        "preference",
        "home",
        "project",
        "general",
    }

    def __init__(
        self,
        database_path: str = "/app/data/jarvis_memory.db",
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._lock = asyncio.Lock()
        self._initialise_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'memories'
                """
            ).fetchone()

            if existing is None:
                self._create_table(connection)
                connection.commit()
                return

            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(memories)"
                ).fetchall()
            }

            if "owner_key" not in columns:
                # Rebuild the table so the previous UNIQUE(category, subject)
                # constraint becomes user-scoped. Existing memories belong to
                # Aaron because Jarvis was single-user before this migration.
                connection.execute(
                    "ALTER TABLE memories RENAME TO memories_legacy"
                )
                self._create_table(connection)
                connection.execute(
                    """
                    INSERT INTO memories (
                        id,
                        owner_key,
                        category,
                        subject,
                        content,
                        search_text,
                        created_at,
                        updated_at
                    )
                    SELECT
                        id,
                        'aaron',
                        category,
                        subject,
                        content,
                        search_text,
                        created_at,
                        updated_at
                    FROM memories_legacy
                    """
                )
                connection.execute("DROP TABLE memories_legacy")
            else:
                self._create_indexes(connection)

            connection.commit()

    @staticmethod
    def _create_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_key TEXT NOT NULL,
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                search_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_key, category, subject)
            )
            """
        )
        MemoryEngine._create_indexes(connection)

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_memories_owner_search
            ON memories(owner_key, search_text)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_memories_owner_updated
            ON memories(owner_key, updated_at DESC)
            """
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        value = value.strip()
        value = re.sub(r"\s+", " ", value)
        return value

    @staticmethod
    def _normalise(value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"[^a-z0-9\s'-]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value

    @classmethod
    def _owner(cls, owner_key: str | None) -> str:
        value = cls._normalise(owner_key or "aaron")
        return value.replace(" ", "_") or "aaron"

    @staticmethod
    def _row_to_dict(
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "owner_key": row["owner_key"],
            "category": row["category"],
            "subject": row["subject"],
            "content": row["content"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def save(
        self,
        category: str,
        subject: str,
        content: str,
        owner_key: str = "aaron",
    ) -> dict[str, Any]:
        owner_key = self._owner(owner_key)
        category = self._normalise(category)
        subject = self._clean_text(subject)
        content = self._clean_text(content)

        if category not in self.VALID_CATEGORIES:
            raise MemoryError(
                f"Unsupported memory category: {category}"
            )

        if not subject:
            raise MemoryError(
                "Memory subject cannot be empty."
            )

        if not content:
            raise MemoryError(
                "Memory content cannot be empty."
            )

        now = datetime.now(timezone.utc).isoformat()

        search_text = self._normalise(
            f"{category} {subject} {content}"
        )

        async with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO memories (
                        owner_key,
                        category,
                        subject,
                        content,
                        search_text,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_key, category, subject)
                    DO UPDATE SET
                        content = excluded.content,
                        search_text = excluded.search_text,
                        updated_at = excluded.updated_at
                    """,
                    (
                        owner_key,
                        category,
                        subject,
                        content,
                        search_text,
                        now,
                        now,
                    ),
                )

                connection.commit()

                row = connection.execute(
                    """
                    SELECT *
                    FROM memories
                    WHERE owner_key = ?
                      AND category = ?
                      AND subject = ?
                    """,
                    (
                        owner_key,
                        category,
                        subject,
                    ),
                ).fetchone()

        if row is None:
            raise MemoryError(
                "The memory could not be saved."
            )

        return self._row_to_dict(row)

    async def list_memories(
        self,
        limit: int = 100,
        owner_key: str = "aaron",
    ) -> list[dict[str, Any]]:
        owner_key = self._owner(owner_key)
        limit = max(1, min(limit, 500))

        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM memories
                    WHERE owner_key = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (owner_key, limit),
                ).fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    async def search(
        self,
        query: str,
        limit: int = 8,
        owner_key: str = "aaron",
    ) -> list[dict[str, Any]]:
        owner_key = self._owner(owner_key)
        query = self._normalise(query)
        limit = max(1, min(limit, 20))

        if not query:
            return []

        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "about",
            "do",
            "does",
            "for",
            "i",
            "in",
            "is",
            "it",
            "me",
            "my",
            "of",
            "on",
            "please",
            "remember",
            "tell",
            "that",
            "the",
            "to",
            "what",
            "who",
            "you",
        }

        terms = [
            term
            for term in query.split()
            if len(term) >= 3 and term not in stop_words
        ]

        if not terms:
            terms = query.split()

        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM memories
                    WHERE owner_key = ?
                    ORDER BY updated_at DESC
                    LIMIT 500
                    """,
                    (owner_key,),
                ).fetchall()

        ranked: list[
            tuple[int, dict[str, Any]]
        ] = []

        for row in rows:
            search_text = row["search_text"]
            score = sum(
                1
                for term in terms
                if term in search_text
            )

            if score:
                ranked.append(
                    (
                        score,
                        self._row_to_dict(row),
                    )
                )

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1]["updated_at"],
            ),
            reverse=True,
        )

        return [
            memory
            for _, memory in ranked[:limit]
        ]

    async def delete(
        self,
        category: str,
        subject: str,
        owner_key: str = "aaron",
    ) -> bool:
        owner_key = self._owner(owner_key)
        category = self._normalise(category)
        subject = self._clean_text(subject)

        async with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE owner_key = ?
                      AND category = ?
                      AND subject = ?
                    """,
                    (
                        owner_key,
                        category,
                        subject,
                    ),
                )

                connection.commit()

        return cursor.rowcount > 0

    async def delete_by_id(
        self,
        memory_id: int,
        owner_key: str = "aaron",
    ) -> bool:
        owner_key = self._owner(owner_key)
        async with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE id = ?
                      AND owner_key = ?
                    """,
                    (memory_id, owner_key),
                )

                connection.commit()

        return cursor.rowcount > 0

    async def count(self, owner_key: str = "aaron") -> int:
        owner_key = self._owner(owner_key)
        async with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM memories
                    WHERE owner_key = ?
                    """,
                    (owner_key,),
                ).fetchone()

        return int(row["total"]) if row else 0

    async def context_for(
        self,
        query: str,
        limit: int = 6,
        owner_key: str = "aaron",
    ) -> str:
        memories = await self.search(
            query=query,
            limit=limit,
            owner_key=owner_key,
        )

        if not memories:
            return ""

        lines = [
            "Relevant saved memories:",
        ]

        for memory in memories:
            lines.append(
                "- "
                f'[{memory["category"]}] '
                f'{memory["subject"]}: '
                f'{memory["content"]}'
            )

        return "\n".join(lines)
