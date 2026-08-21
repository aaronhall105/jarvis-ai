from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path


_MAX_CORRECTION_UTTERANCE = 256


def _clean(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' -]+", " ", value.casefold()).split())


class SpeechCorrectionEngine:
    """Persist explicit, user-scoped STT corrections; never stores audio."""

    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS speech_corrections (
                    user_key TEXT NOT NULL,
                    wrong_phrase TEXT NOT NULL,
                    correct_phrase TEXT NOT NULL,
                    PRIMARY KEY (user_key, wrong_phrase)
                )"""
            )

    async def learn_explicit(
        self, text: str, user_key: str, valid_phrases: set[str]
    ) -> tuple[str, str] | None:
        # Parse fixed phrases with bounded string operations. This consumes an
        # untrusted transcript, so avoid backtracking regexes whose separators
        # can overlap with the captured text.
        utterance = text.strip()
        if not utterance or len(utterance) > _MAX_CORRECTION_UTTERANCE:
            return None
        lowered = utterance.casefold()
        if lowered.startswith("no,"):
            utterance = utterance[3:].lstrip()
            lowered = utterance.casefold()

        wrong_raw: str
        correct_raw: str
        if lowered.startswith("i said "):
            body = utterance[len("i said ") :]
            separator = body.casefold().find(", not ")
            if separator < 0:
                return None
            correct_raw = body[:separator]
            wrong_raw = body[separator + len(", not ") :]
        elif lowered.startswith("when i say "):
            body = utterance[len("when i say ") :]
            separator = body.casefold().find(", i mean ")
            if separator < 0:
                return None
            wrong_raw = body[:separator]
            correct_raw = body[separator + len(", i mean ") :]
        else:
            return None

        wrong = _clean(wrong_raw.rstrip(".!"))
        correct = _clean(correct_raw.rstrip(".!"))
        if not wrong or wrong == correct or len(wrong) > 60 or correct not in valid_phrases:
            return None

        def write() -> None:
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    """INSERT INTO speech_corrections(user_key, wrong_phrase, correct_phrase)
                       VALUES (?, ?, ?)
                       ON CONFLICT(user_key, wrong_phrase) DO UPDATE SET
                         correct_phrase=excluded.correct_phrase""",
                    (user_key, wrong, correct),
                )

        await asyncio.to_thread(write)
        return wrong, correct

    async def apply(self, text: str, user_key: str) -> tuple[str, tuple[str, ...]]:
        def read() -> list[tuple[str, str]]:
            with sqlite3.connect(self.path) as connection:
                return connection.execute(
                    "SELECT wrong_phrase, correct_phrase FROM speech_corrections WHERE user_key = ?",
                    (user_key,),
                ).fetchall()

        result = text
        applied: list[str] = []
        for wrong, correct in await asyncio.to_thread(read):
            updated, count = re.subn(
                rf"(?<![a-z0-9]){re.escape(wrong)}(?![a-z0-9])",
                correct,
                result,
                flags=re.I,
            )
            if count:
                result = updated
                applied.append(f"{wrong} → {correct}")
        return result, tuple(applied)

    async def prompt_terms(self, user_key: str, *, limit: int = 24) -> tuple[str, ...]:
        """Return safe learned vocabulary for future transcription sessions."""

        def read() -> list[str]:
            with sqlite3.connect(self.path) as connection:
                bounded_limit = max(1, min(int(limit), 100))
                if user_key == "guest":
                    # A Voice Preview session starts before biometric identity is
                    # known. Sharing only registry-validated destination names is
                    # safe and helps every household member pronounce devices.
                    rows = connection.execute(
                        """SELECT DISTINCT correct_phrase FROM speech_corrections
                           ORDER BY correct_phrase LIMIT ?""",
                        (bounded_limit,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """SELECT DISTINCT correct_phrase FROM speech_corrections
                           WHERE user_key = ? ORDER BY correct_phrase LIMIT ?""",
                        (user_key, bounded_limit),
                    ).fetchall()
            return [str(row[0]) for row in rows if row and _clean(str(row[0]))]

        # Do not return mistaken phrases: prompting them would reinforce the error.
        return tuple(await asyncio.to_thread(read))

    async def list_for_user(self, user_key: str) -> tuple[dict[str, str], ...]:
        def read() -> list[tuple[str, str]]:
            with sqlite3.connect(self.path) as connection:
                return connection.execute(
                    """SELECT wrong_phrase, correct_phrase FROM speech_corrections
                       WHERE user_key = ? ORDER BY wrong_phrase""",
                    (user_key,),
                ).fetchall()

        return tuple(
            {"heard_as": wrong, "means": correct}
            for wrong, correct in await asyncio.to_thread(read)
        )

    async def forget(self, user_key: str, wrong_phrase: str | None = None) -> int:
        clean_wrong = _clean(wrong_phrase or "")

        def delete() -> int:
            with sqlite3.connect(self.path) as connection:
                if clean_wrong:
                    cursor = connection.execute(
                        "DELETE FROM speech_corrections WHERE user_key = ? AND wrong_phrase = ?",
                        (user_key, clean_wrong),
                    )
                else:
                    cursor = connection.execute(
                        "DELETE FROM speech_corrections WHERE user_key = ?",
                        (user_key,),
                    )
                return max(0, int(cursor.rowcount))

        return await asyncio.to_thread(delete)
