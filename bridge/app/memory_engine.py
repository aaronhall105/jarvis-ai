import asyncio
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class MemoryError(RuntimeError):
    """Raised when a memory operation cannot be completed safely."""


class MemoryEngine:
    """Persistent, subject-aware memory with explicit visibility boundaries.

    Backwards compatibility:
    - ``owner_key`` remains the authenticated requester/creator argument.
    - Existing callers that do not pass ``subject_key`` or ``visibility`` still work.
    - Existing v1/v2 databases are migrated in place on startup.
    """

    SCHEMA_VERSION = 3
    VALID_CATEGORIES = {
        "personal",
        "preference",
        "home",
        "project",
        "general",
    }
    VALID_VISIBILITIES = {
        "private",
        "subject_and_owner",
        "household",
    }
    VALID_SENSITIVITIES = {
        "normal",
        "sensitive",
    }
    HOUSEHOLD_USERS = {"aaron", "amber"}
    HOUSEHOLD_SUBJECT = "household"

    _SENSITIVE_PATTERN = re.compile(
        r"\b(?:health|medical|condition|diagnos(?:is|ed)|allerg(?:y|ic)|"
        r"intoleran(?:ce|t)|medication|medicine|prescription|disability|"
        r"mental health|pregnan(?:cy|t)|blood type|nhs|doctor|hospital|"
        r"password|passcode|pin|token|bank|card number|cvv)\b",
        re.I,
    )
    _PRIVATE_HINT_PATTERN = re.compile(
        r"\b(?:private|secret|surprise|present|gift|hidden|hide|proposal|"
        r"password|passcode|pin|token|bank|card|cvv)\b",
        re.I,
    )
    _PROFILE_FACT_PATTERN = re.compile(
        r"\b(?:favourite|favorite|likes?|dislikes?|prefers?|preference|"
        r"diet|dietary|vegetarian|vegan|allerg|intoleran|health|medical|"
        r"condition|medication|birthday|date of birth|job|workplace|"
        r"shoe size|clothes size|phone number|email address)\b",
        re.I,
    )

    # Retrieval concepts bridge natural questions and stored factual wording.
    # For example, "Do I have any health conditions?" must retrieve a memory
    # stored as "Amber is lactose intolerant" without weakening access control.
    _QUERY_CONCEPT_PATTERNS = {
        "health_profile": re.compile(
            r"\b(?:health|medical|condition|diagnos(?:is|ed)|allerg(?:y|ies|ic)|"
            r"intoleran(?:ce|t)|medication|medicine|prescription|disability|"
            r"dietary requirements?|food restrictions?|what (?:can|cannot|can't) "
            r"(?:i|aaron|amber) (?:eat|have))\b",
            re.I,
        ),
        "dietary_profile": re.compile(
            r"\b(?:diet|dietary|food|eat|allerg(?:y|ies|ic)|intoleran(?:ce|t)|"
            r"vegetarian|vegan|gluten|lactose|dairy|coeliac|celiac)\b",
            re.I,
        ),
        "birthday_profile": re.compile(
            r"\b(?:birthday|date of birth|born|age|how old)\b",
            re.I,
        ),
        "work_profile": re.compile(
            r"\b(?:job|work|workplace|employer|occupation|works? at|works? as)\b",
            re.I,
        ),
        "contact_profile": re.compile(
            r"\b(?:phone number|mobile number|email address|contact details?)\b",
            re.I,
        ),
        "preference_profile": re.compile(
            r"\b(?:favourite|favorite|likes?|dislikes?|prefers?|preference)\b",
            re.I,
        ),
    }

    _MEMORY_CONCEPT_PATTERNS = {
        "health_profile": re.compile(
            r"\b(?:health|medical|condition|diagnos(?:is|ed)|allerg(?:y|ies|ic)|"
            r"intoleran(?:ce|t)|medication|medicine|prescription|disability|"
            r"mental health|pregnan(?:cy|t)|blood type|asthma|diabetes|epilepsy|"
            r"coeliac|celiac|lactose|gluten|dairy)\b",
            re.I,
        ),
        "dietary_profile": re.compile(
            r"\b(?:diet|dietary|food|eat|allerg(?:y|ies|ic)|intoleran(?:ce|t)|"
            r"vegetarian|vegan|gluten|lactose|dairy|coeliac|celiac)\b",
            re.I,
        ),
        "birthday_profile": re.compile(
            r"\b(?:birthday|date of birth|born|age|years? old)\b",
            re.I,
        ),
        "work_profile": re.compile(
            r"\b(?:job|work|workplace|employer|occupation|works? at|works? as)\b",
            re.I,
        ),
        "contact_profile": re.compile(
            r"\b(?:phone number|mobile number|email address|contact details?)\b",
            re.I,
        ),
        "preference_profile": re.compile(
            r"\b(?:favourite|favorite|likes?|dislikes?|prefers?|preference)\b",
            re.I,
        ),
    }

    def __init__(
        self,
        database_path: str = "/app/data/jarvis_memory.db",
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialise_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    @staticmethod
    def _normalise(value: str) -> str:
        value = str(value or "").casefold().strip()
        value = re.sub(r"[^a-z0-9\s'-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _owner(cls, owner_key: str | None) -> str:
        value = cls._normalise(owner_key or "aaron")
        return value.replace(" ", "_") or "aaron"

    @classmethod
    def _subject_key(cls, subject_key: str | None) -> str:
        value = cls._normalise(subject_key or "")
        return value.replace(" ", "_")

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None

    @classmethod
    def _create_table(cls, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_key TEXT NOT NULL,
                subject_key TEXT NOT NULL,
                visibility TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                search_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                UNIQUE(owner_key, category, subject)
            )
            """
        )
        cls._create_indexes(connection)

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_owner_search
            ON memories(owner_key, search_text);

            CREATE INDEX IF NOT EXISTS idx_memories_subject_search
            ON memories(subject_key, visibility, search_text);

            CREATE INDEX IF NOT EXISTS idx_memories_visibility_updated
            ON memories(visibility, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_memories_subject_lookup
            ON memories(subject_key, category, subject);
            """
        )

    @staticmethod
    def _create_meta(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _meta_get(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value FROM memory_meta WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    @staticmethod
    def _meta_set(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            """
            INSERT INTO memory_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _rebuild_single_user_table(self, connection: sqlite3.Connection) -> None:
        """Migrate the original schema that had no owner_key."""
        connection.execute("ALTER TABLE memories RENAME TO memories_legacy")
        self._create_table(connection)
        rows = connection.execute("SELECT * FROM memories_legacy").fetchall()
        for row in rows:
            owner = "aaron"
            subject = str(row["subject"])
            content = str(row["content"])
            category = str(row["category"])
            subject_key = self._infer_subject_key(subject, content, owner)
            sensitivity = self._infer_sensitivity(category, subject, content)
            visibility = self._inferred_visibility(
                owner, subject_key, category, subject, content, sensitivity
            )
            if sensitivity == "sensitive" and visibility == "household":
                visibility = "subject_and_owner" if subject_key in self.HOUSEHOLD_USERS else "private"
            connection.execute(
                """
                INSERT INTO memories (
                    id, owner_key, subject_key, visibility, sensitivity,
                    category, subject, content, search_text,
                    created_at, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], owner, subject_key, visibility, sensitivity,
                    category, subject, content, row["search_text"],
                    row["created_at"], row["updated_at"], owner,
                ),
            )
        connection.execute("DROP TABLE memories_legacy")

    def _add_v3_columns(self, connection: sqlite3.Connection) -> None:
        columns = self._table_columns(connection, "memories")
        additions = {
            "subject_key": "TEXT NOT NULL DEFAULT ''",
            "visibility": "TEXT NOT NULL DEFAULT 'private'",
            "sensitivity": "TEXT NOT NULL DEFAULT 'normal'",
            "updated_by": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE memories ADD COLUMN {name} {definition}"
                )

    def _migrate_v2_rows(self, connection: sqlite3.Connection) -> int:
        rows = connection.execute("SELECT * FROM memories").fetchall()
        migrated = 0
        for row in rows:
            owner = self._owner(str(row["owner_key"] or "aaron"))
            subject = str(row["subject"] or "")
            content = str(row["content"] or "")
            category = str(row["category"] or "general")
            existing_subject = self._subject_key(str(row["subject_key"] or ""))
            subject_key = existing_subject or self._infer_subject_key(
                subject, content, owner
            )
            sensitivity_value = str(row["sensitivity"] or "").strip().lower()
            sensitivity = (
                sensitivity_value
                if existing_subject
                and sensitivity_value in self.VALID_SENSITIVITIES
                else self._infer_sensitivity(category, subject, content)
            )
            visibility_value = str(row["visibility"] or "").strip().lower()
            # v2 rows gained the SQL default "private" during ALTER. Re-evaluate
            # them once so memories clearly about Amber become visible to Amber.
            visibility = self._inferred_visibility(
                owner, subject_key, category, subject, content, sensitivity
            )
            if visibility_value in self.VALID_VISIBILITIES and existing_subject:
                visibility = visibility_value
            if sensitivity == "sensitive" and visibility == "household":
                visibility = (
                    "subject_and_owner"
                    if subject_key in self.HOUSEHOLD_USERS
                    else "private"
                )
            updated_by = self._owner(str(row["updated_by"] or owner))
            search_text = self._build_search_text(
                category, subject, content, owner, subject_key, visibility
            )
            connection.execute(
                """
                UPDATE memories
                SET owner_key = ?, subject_key = ?, visibility = ?,
                    sensitivity = ?, updated_by = ?, search_text = ?
                WHERE id = ?
                """,
                (
                    owner, subject_key, visibility, sensitivity,
                    updated_by, search_text, row["id"],
                ),
            )
            migrated += 1
        return migrated

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            if not self._table_exists(connection, "memories"):
                self._create_table(connection)
            else:
                columns = self._table_columns(connection, "memories")
                if "owner_key" not in columns:
                    self._rebuild_single_user_table(connection)
                else:
                    self._add_v3_columns(connection)
                    self._create_indexes(connection)

            self._create_meta(connection)
            raw_version = self._meta_get(connection, "schema_version")
            try:
                version = int(raw_version or "0")
            except ValueError:
                version = 0
            if version < self.SCHEMA_VERSION:
                migrated = self._migrate_v2_rows(connection)
                self._meta_set(
                    connection, "last_migrated_rows", str(migrated)
                )
                self._meta_set(
                    connection, "last_migrated_at", self._utc_now()
                )
                self._meta_set(
                    connection, "schema_version", str(self.SCHEMA_VERSION)
                )
            connection.commit()

    @classmethod
    def _infer_subject_key(
        cls,
        subject: str,
        content: str,
        owner_key: str,
    ) -> str:
        subject_text = cls._normalise(subject)
        content_text = cls._normalise(content)

        # The stable subject label is authoritative. Content is used only when it
        # starts with an explicit person's name or possessive.
        for person in sorted(cls.HOUSEHOLD_USERS):
            if re.search(
                rf"^(?:{re.escape(person)}(?:'s)?)(?:\s|$)",
                subject_text,
            ):
                return person
            if re.search(
                rf"^(?:{re.escape(person)}(?:'s)?)(?:\s+(?:is|has|does|cannot|can't)|\s)",
                content_text,
            ):
                return person

        if re.search(
            r"\b(?:household|our home|our flat|the flat|everyone|family)\b",
            subject_text,
        ):
            return cls.HOUSEHOLD_SUBJECT
        return cls._owner(owner_key)

    @classmethod
    def _infer_sensitivity(
        cls,
        category: str,
        subject: str,
        content: str,
    ) -> str:
        combined = f"{category} {subject} {content}"
        return "sensitive" if cls._SENSITIVE_PATTERN.search(combined) else "normal"

    @classmethod
    def _inferred_visibility(
        cls,
        owner_key: str,
        subject_key: str,
        category: str,
        subject: str,
        content: str,
        sensitivity: str,
    ) -> str:
        combined = f"{category} {subject} {content}"
        if subject_key == cls.HOUSEHOLD_SUBJECT:
            return "household" if sensitivity == "normal" else "private"
        if subject_key not in cls.HOUSEHOLD_USERS or subject_key == owner_key:
            return "private"
        # Old schemas had no visibility field. Be conservative: a clearly named
        # person does not automatically make surprise plans or secrets visible.
        if cls._PRIVATE_HINT_PATTERN.search(combined):
            return "private"
        if sensitivity == "sensitive":
            return "subject_and_owner"
        if category == "preference" or cls._PROFILE_FACT_PATTERN.search(combined):
            return "subject_and_owner"
        return "private"

    @classmethod
    def _build_search_text(
        cls,
        category: str,
        subject: str,
        content: str,
        owner_key: str,
        subject_key: str,
        visibility: str,
    ) -> str:
        return cls._normalise(
            f"{category} {subject} {content} {owner_key} {subject_key} {visibility}"
        )

    @classmethod
    def _validate_visibility(
        cls,
        owner_key: str,
        subject_key: str,
        visibility: str,
        sensitivity: str,
    ) -> str:
        if visibility not in cls.VALID_VISIBILITIES:
            raise MemoryError(f"Unsupported memory visibility: {visibility}")
        if sensitivity not in cls.VALID_SENSITIVITIES:
            raise MemoryError(f"Unsupported memory sensitivity: {sensitivity}")
        if visibility == "subject_and_owner":
            if subject_key not in cls.HOUSEHOLD_USERS:
                raise MemoryError(
                    "Subject-and-owner memories require Aaron or Amber as the subject."
                )
        if visibility == "household":
            if sensitivity == "sensitive":
                raise MemoryError(
                    "Sensitive personal information cannot be shared household-wide."
                )
            if subject_key != cls.HOUSEHOLD_SUBJECT:
                raise MemoryError(
                    "Household memories must use subject_key 'household'."
                )
        return visibility

    @classmethod
    def _can_view(cls, row: sqlite3.Row, requester_key: str) -> bool:
        requester = cls._owner(requester_key)
        owner = cls._owner(str(row["owner_key"]))
        subject_key = cls._subject_key(str(row["subject_key"]))
        visibility = str(row["visibility"])
        if requester == owner:
            return True
        if (
            visibility == "subject_and_owner"
            and requester == subject_key
            and requester in cls.HOUSEHOLD_USERS
        ):
            return True
        return (
            visibility == "household"
            and requester in cls.HOUSEHOLD_USERS
        )

    @classmethod
    def _can_edit(cls, row: sqlite3.Row, requester_key: str) -> bool:
        requester = cls._owner(requester_key)
        owner = cls._owner(str(row["owner_key"]))
        subject_key = cls._subject_key(str(row["subject_key"]))
        visibility = str(row["visibility"])
        return requester == owner or (
            visibility == "subject_and_owner"
            and requester == subject_key
            and requester in cls.HOUSEHOLD_USERS
        )

    @classmethod
    def _row_to_dict(
        cls,
        row: sqlite3.Row,
        requester_key: str | None = None,
    ) -> dict[str, Any]:
        requester = cls._owner(requester_key or str(row["owner_key"]))
        return {
            "id": row["id"],
            "owner_key": row["owner_key"],
            "subject_key": row["subject_key"],
            "visibility": row["visibility"],
            "sensitivity": row["sensitivity"],
            "category": row["category"],
            "subject": row["subject"],
            "content": row["content"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
            "requester_can_edit": cls._can_edit(row, requester),
        }

    def _find_existing_for_save(
        self,
        connection: sqlite3.Connection,
        *,
        owner_key: str,
        subject_key: str,
        visibility: str,
        category: str,
        subject: str,
    ) -> sqlite3.Row | None:
        if visibility == "private":
            return connection.execute(
                """
                SELECT * FROM memories
                WHERE owner_key = ? AND category = ?
                  AND lower(subject) = lower(?)
                ORDER BY updated_at DESC LIMIT 1
                """,
                (owner_key, category, subject),
            ).fetchone()
        if visibility == "subject_and_owner":
            return connection.execute(
                """
                SELECT * FROM memories
                WHERE subject_key = ? AND category = ?
                  AND lower(subject) = lower(?)
                  AND visibility = 'subject_and_owner'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (subject_key, category, subject),
            ).fetchone()
        return connection.execute(
            """
            SELECT * FROM memories
            WHERE subject_key = 'household' AND category = ?
              AND lower(subject) = lower(?)
              AND visibility = 'household'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (category, subject),
        ).fetchone()

    async def save(
        self,
        category: str,
        subject: str,
        content: str,
        owner_key: str = "aaron",
        subject_key: str | None = None,
        visibility: str | None = None,
        sensitivity: str | None = None,
    ) -> dict[str, Any]:
        owner = self._owner(owner_key)
        category = self._normalise(category)
        subject = self._clean_text(subject)
        content = self._clean_text(content)
        if category not in self.VALID_CATEGORIES:
            raise MemoryError(f"Unsupported memory category: {category}")
        if not subject:
            raise MemoryError("Memory subject cannot be empty.")
        if not content:
            raise MemoryError("Memory content cannot be empty.")
        if len(subject) > 150:
            raise MemoryError("Memory subject is too long.")
        if len(content) > 2000:
            raise MemoryError("Memory content is too long.")

        resolved_subject = self._subject_key(subject_key)
        if not resolved_subject:
            resolved_subject = self._infer_subject_key(subject, content, owner)
        resolved_sensitivity = str(sensitivity or "").strip().lower()
        if resolved_sensitivity not in self.VALID_SENSITIVITIES:
            resolved_sensitivity = self._infer_sensitivity(
                category, subject, content
            )
        resolved_visibility = str(visibility or "").strip().lower()
        if not resolved_visibility:
            resolved_visibility = self._inferred_visibility(
                owner,
                resolved_subject,
                category,
                subject,
                content,
                resolved_sensitivity,
            )
        resolved_visibility = self._validate_visibility(
            owner,
            resolved_subject,
            resolved_visibility,
            resolved_sensitivity,
        )

        now = self._utc_now()
        search_text = self._build_search_text(
            category,
            subject,
            content,
            owner,
            resolved_subject,
            resolved_visibility,
        )

        async with self._lock:
            with self._connect() as connection:
                existing = self._find_existing_for_save(
                    connection,
                    owner_key=owner,
                    subject_key=resolved_subject,
                    visibility=resolved_visibility,
                    category=category,
                    subject=subject,
                )
                if existing is not None and self._can_edit(existing, owner):
                    connection.execute(
                        """
                        UPDATE memories
                        SET subject_key = ?, visibility = ?, sensitivity = ?,
                            content = ?, search_text = ?, updated_at = ?,
                            updated_by = ?
                        WHERE id = ?
                        """,
                        (
                            resolved_subject,
                            resolved_visibility,
                            resolved_sensitivity,
                            content,
                            search_text,
                            now,
                            owner,
                            existing["id"],
                        ),
                    )
                    memory_id = int(existing["id"])
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO memories (
                            owner_key, subject_key, visibility, sensitivity,
                            category, subject, content, search_text,
                            created_at, updated_at, updated_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(owner_key, category, subject)
                        DO UPDATE SET
                            subject_key = excluded.subject_key,
                            visibility = excluded.visibility,
                            sensitivity = excluded.sensitivity,
                            content = excluded.content,
                            search_text = excluded.search_text,
                            updated_at = excluded.updated_at,
                            updated_by = excluded.updated_by
                        """,
                        (
                            owner,
                            resolved_subject,
                            resolved_visibility,
                            resolved_sensitivity,
                            category,
                            subject,
                            content,
                            search_text,
                            now,
                            now,
                            owner,
                        ),
                    )
                    if cursor.lastrowid:
                        memory_id = int(cursor.lastrowid)
                    else:
                        row = connection.execute(
                            """
                            SELECT id FROM memories
                            WHERE owner_key = ? AND category = ? AND subject = ?
                            """,
                            (owner, category, subject),
                        ).fetchone()
                        if row is None:
                            raise MemoryError("The memory could not be saved.")
                        memory_id = int(row["id"])
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()

        if row is None:
            raise MemoryError("The memory could not be saved.")
        return self._row_to_dict(row, owner)

    def _accessible_rows_sync(
        self,
        requester_key: str,
        *,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 2000))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [
            row for row in rows
            if self._can_view(row, requester_key)
        ]

    async def list_memories(
        self,
        limit: int = 100,
        owner_key: str = "aaron",
    ) -> list[dict[str, Any]]:
        requester = self._owner(owner_key)
        safe_limit = max(1, min(int(limit), 500))
        async with self._lock:
            rows = await asyncio.to_thread(
                self._accessible_rows_sync,
                requester,
                limit=max(500, safe_limit * 4),
            )
        return [
            self._row_to_dict(row, requester)
            for row in rows[:safe_limit]
        ]

    @classmethod
    def _query_concepts(cls, query: str) -> set[str]:
        return {
            name
            for name, pattern in cls._QUERY_CONCEPT_PATTERNS.items()
            if pattern.search(query)
        }

    @classmethod
    def _memory_concepts(cls, row: sqlite3.Row) -> set[str]:
        text = " ".join(
            str(row[key] or "")
            for key in ("category", "subject", "content")
        )
        return {
            name
            for name, pattern in cls._MEMORY_CONCEPT_PATTERNS.items()
            if pattern.search(text)
        }

    @staticmethod
    def _search_terms(query: str) -> list[str]:
        stop_words = {
            "a", "an", "and", "are", "about", "any", "do", "does",
            "for", "have", "i", "in", "is", "it", "me", "my", "of",
            "on", "please", "remember", "tell", "that", "the", "to",
            "what", "who", "you",
        }
        terms = [
            term for term in query.split()
            if len(term) >= 3 and term not in stop_words
        ]
        return terms or query.split()

    async def search(
        self,
        query: str,
        limit: int = 8,
        owner_key: str = "aaron",
    ) -> list[dict[str, Any]]:
        requester = self._owner(owner_key)
        query = self._normalise(query)
        safe_limit = max(1, min(int(limit), 20))
        if not query:
            return []
        terms = self._search_terms(query)
        query_concepts = self._query_concepts(query)

        async with self._lock:
            rows = await asyncio.to_thread(
                self._accessible_rows_sync,
                requester,
                limit=2000,
            )

        ranked: list[tuple[int, str, sqlite3.Row]] = []
        for row in rows:
            search_text = str(row["search_text"])
            term_score = sum(1 for term in terms if term in search_text)
            concept_matches = query_concepts & self._memory_concepts(row)
            if not term_score and not concept_matches:
                continue

            # Literal wording remains strongest. Concept matches provide the
            # semantic bridge for broad profile questions such as health, diet,
            # birthdays and work without introducing an external embedding store.
            score = (term_score * 10) + (len(concept_matches) * 14)
            if str(row["subject_key"]) == requester:
                score += 12
            if str(row["owner_key"]) == requester:
                score += 4
            if str(row["visibility"]) == "subject_and_owner":
                score += 2
            if str(row["sensitivity"]) == "sensitive" and "health_profile" in concept_matches:
                score += 3
            ranked.append((score, str(row["updated_at"]), row))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            self._row_to_dict(row, requester)
            for _, _, row in ranked[:safe_limit]
        ]

    def _matching_deletable_row(
        self,
        connection: sqlite3.Connection,
        requester: str,
        category: str,
        subject: str,
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            """
            SELECT * FROM memories
            WHERE category = ? AND lower(subject) = lower(?)
            ORDER BY updated_at DESC
            """,
            (category, subject),
        ).fetchall()
        return next(
            (row for row in rows if self._can_edit(row, requester)),
            None,
        )

    async def delete(
        self,
        category: str,
        subject: str,
        owner_key: str = "aaron",
    ) -> bool:
        requester = self._owner(owner_key)
        category = self._normalise(category)
        subject = self._clean_text(subject)
        async with self._lock:
            with self._connect() as connection:
                row = self._matching_deletable_row(
                    connection, requester, category, subject
                )
                if row is None:
                    return False
                cursor = connection.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (row["id"],),
                )
                connection.commit()
        return cursor.rowcount > 0

    async def delete_by_id(
        self,
        memory_id: int,
        owner_key: str = "aaron",
    ) -> bool:
        requester = self._owner(owner_key)
        async with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM memories WHERE id = ?",
                    (int(memory_id),),
                ).fetchone()
                if row is None or not self._can_edit(row, requester):
                    return False
                cursor = connection.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (int(memory_id),),
                )
                connection.commit()
        return cursor.rowcount > 0

    async def count(self, owner_key: str = "aaron") -> int:
        return len(await self.list_memories(limit=500, owner_key=owner_key))

    async def context_for(
        self,
        query: str,
        limit: int = 6,
        owner_key: str = "aaron",
    ) -> str:
        requester = self._owner(owner_key)
        memories = await self.search(
            query=query,
            limit=limit,
            owner_key=requester,
        )
        if not memories:
            return ""

        lines = [
            "Relevant saved memories the authenticated user is permitted to access:",
        ]
        for memory in memories:
            subject_key = str(memory["subject_key"])
            visibility = str(memory["visibility"])
            if subject_key == self.HOUSEHOLD_SUBJECT:
                scope = "household"
            elif subject_key == requester:
                scope = "about the current user"
            else:
                scope = f"about {subject_key.title()}"
            if visibility == "private":
                access = "private to the creator"
            elif visibility == "subject_and_owner":
                access = "shared with the person it concerns"
            else:
                access = "shared with the household"
            lines.append(
                f'- [{memory["category"]}; {scope}; {access}] '
                f'{memory["subject"]}: {memory["content"]}'
            )
        return "\n".join(lines)

    async def status(self, owner_key: str = "aaron") -> dict[str, Any]:
        requester = self._owner(owner_key)
        accessible = await self.count(requester)
        async with self._lock:
            with self._connect() as connection:
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM memories"
                    ).fetchone()[0]
                )
                shared = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM memories
                        WHERE visibility IN ('subject_and_owner', 'household')
                        """
                    ).fetchone()[0]
                )
                migrated = self._meta_get(
                    connection, "last_migrated_rows"
                ) or "0"
        return {
            "status": "ready",
            "schema_version": self.SCHEMA_VERSION,
            "requester_key": requester,
            "accessible_count": accessible,
            "total_count": total if requester == "aaron" else None,
            "shared_count": shared if requester == "aaron" else None,
            "last_migrated_rows": int(migrated),
            "visibility_modes": sorted(self.VALID_VISIBILITIES),
            "database": self.database_path.name,
        }
