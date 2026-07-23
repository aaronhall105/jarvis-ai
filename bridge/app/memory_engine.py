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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, subject)
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memories_search_text
                ON memories(search_text)
                """
            )

            connection.commit()

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

    @staticmethod
    def _row_to_dict(
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
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
    ) -> dict[str, Any]:
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
                        category,
                        subject,
                        content,
                        search_text,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(category, subject)
                    DO UPDATE SET
                        content = excluded.content,
                        search_text = excluded.search_text,
                        updated_at = excluded.updated_at
                    """,
                    (
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
                    WHERE category = ?
                      AND subject = ?
                    """,
                    (
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
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))

        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM memories
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    async def search(
        self,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
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
                    ORDER BY updated_at DESC
                    LIMIT 500
                    """
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
    ) -> bool:
        category = self._normalise(category)
        subject = self._clean_text(subject)

        async with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE category = ?
                      AND subject = ?
                    """,
                    (
                        category,
                        subject,
                    ),
                )

                connection.commit()

        return cursor.rowcount > 0

    async def delete_by_id(
        self,
        memory_id: int,
    ) -> bool:
        async with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE id = ?
                    """,
                    (memory_id,),
                )

                connection.commit()

        return cursor.rowcount > 0

    async def count(self) -> int:
        async with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM memories
                    """
                ).fetchone()

        return int(row["total"]) if row else 0

    async def context_for(
        self,
        query: str,
        limit: int = 6,
    ) -> str:
        memories = await self.search(
            query=query,
            limit=limit,
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
