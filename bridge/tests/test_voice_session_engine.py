from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.voice_session_engine import VoiceSessionEngine


class VoiceSessionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "voice.db")
        self.engine = VoiceSessionEngine(
            self.database,
            idle_timeout_seconds=45,
            max_session_seconds=300,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def create(self, session_id: str = "session-1") -> dict:
        return await self.engine.touch(
            session_id=session_id,
            conversation_id=f"conversation-{session_id}",
            user_key="aaron",
            satellite_id="satellite-1",
            device_id="device-1",
            endpoint_kind="satellite",
            turn_index=1,
        )

    async def test_first_turn_creates_active_session(self) -> None:
        row = await self.create()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["turn_count"], 1)
        self.assertEqual(row["interrupt_count"], 0)
        self.assertEqual(row["endpoint_kind"], "satellite")

    async def test_next_turn_updates_existing_session(self) -> None:
        await self.create()
        row = await self.engine.touch(
            session_id="session-1",
            conversation_id="conversation-session-1",
            user_key="aaron",
            turn_index=2,
        )
        self.assertEqual(row["turn_count"], 2)
        self.assertEqual(len(await self.engine.list_sessions()), 1)

    async def test_interrupt_is_recorded_without_closing_session(self) -> None:
        await self.create()
        row = await self.engine.record_interrupt(
            "session-1",
            reason="accepted_follow_up_during_playback",
            media_player_entity_id="media_player.home_assistant_voice_09f0ef",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["interrupt_count"], 1)
        self.assertEqual(
            row["last_interrupt_media_player"],
            "media_player.home_assistant_voice_09f0ef",
        )

    async def test_interrupt_keeps_session_and_conversation_identity(self) -> None:
        first = await self.engine.touch(
            session_id="session-keep",
            conversation_id="conversation-keep",
            user_key="aaron",
            endpoint_kind="mobile_app",
            turn_index=1,
        )
        await self.engine.record_interrupt("session-keep", reason="speech_started")
        second = await self.engine.touch(
            session_id="session-keep",
            conversation_id="conversation-keep",
            user_key="aaron",
            endpoint_kind="mobile_app",
            turn_index=2,
        )
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["conversation_id"], first["conversation_id"])
        self.assertEqual(second["started_at"], first["started_at"])
        self.assertEqual(second["interrupt_count"], 1)

    async def test_multiple_interrupts_accumulate(self) -> None:
        await self.create()
        await self.engine.record_interrupt("session-1")
        row = await self.engine.record_interrupt("session-1", reason="explicit_stop")
        self.assertEqual(row["interrupt_count"], 2)
        self.assertEqual(row["last_interrupt_reason"], "explicit_stop")

    async def test_interrupt_unknown_or_closed_session_is_not_recorded(self) -> None:
        self.assertIsNone(await self.engine.record_interrupt("missing"))
        await self.create()
        await self.engine.close("session-1")
        self.assertIsNone(await self.engine.record_interrupt("session-1"))

    async def test_status_reports_interrupt_totals(self) -> None:
        await self.create("one")
        await self.create("two")
        await self.engine.record_interrupt("one")
        await self.engine.record_interrupt("one")
        await self.engine.record_interrupt("two")
        status = await self.engine.status()
        self.assertEqual(status["total_interrupts"], 3)
        self.assertEqual(status["interrupted_sessions"], 2)

    async def test_close_marks_session_closed(self) -> None:
        await self.create()
        self.assertTrue(await self.engine.close("session-1", "explicit_closure"))
        status = await self.engine.status()
        self.assertEqual(status["active_count"], 0)
        self.assertEqual(status["session_counts"].get("closed"), 1)

    async def test_expire_stale_marks_old_session_expired(self) -> None:
        await self.create()
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        with self.engine._connection() as connection:
            connection.execute(
                "UPDATE voice_sessions SET expires_at = ? WHERE session_id = ?",
                (old.isoformat(), "session-1"),
            )
        self.assertEqual(await self.engine.expire_stale(), 1)
        self.assertEqual((await self.engine.status())["session_counts"].get("expired"), 1)

    async def test_active_only_filter(self) -> None:
        await self.create("active")
        await self.create("closed")
        await self.engine.close("closed")
        rows = await self.engine.list_sessions(active_only=True)
        self.assertEqual([row["session_id"] for row in rows], ["active"])

    async def test_existing_v1700_database_is_migrated(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            db = Path(temp.name) / "old.db"
            with sqlite3.connect(db) as connection:
                connection.executescript(
                    """
                    CREATE TABLE voice_sessions (
                        session_id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        user_key TEXT NOT NULL,
                        satellite_id TEXT,
                        device_id TEXT,
                        status TEXT NOT NULL,
                        turn_count INTEGER NOT NULL DEFAULT 0,
                        started_at TEXT NOT NULL,
                        last_activity_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        last_intent TEXT,
                        close_reason TEXT
                    );
                    """
                )
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """
                    INSERT INTO voice_sessions VALUES (
                        'legacy', 'conversation', 'aaron', 'sat', 'device',
                        'active', 1, ?, ?, ?, NULL, NULL
                    )
                    """,
                    (now, now, now),
                )
            migrated = VoiceSessionEngine(str(db))
            row = await migrated.record_interrupt("legacy")
            self.assertIsNotNone(row)
            self.assertEqual(row["interrupt_count"], 1)
        finally:
            temp.cleanup()

    async def test_missing_session_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.engine.touch(
                session_id="",
                conversation_id="conversation-1",
                user_key="aaron",
            )

    async def test_mobile_endpoint_is_stored_and_counted(self) -> None:
        row = await self.engine.touch(
            session_id="phone",
            conversation_id="phone-conversation",
            user_key="aaron",
            device_id="aaron-phone",
            endpoint_kind="mobile_app",
            turn_index=1,
        )
        self.assertEqual(row["endpoint_kind"], "mobile_app")
        self.assertEqual((await self.engine.status())["endpoint_counts"].get("mobile_app"), 1)

    async def test_invalid_endpoint_kind_is_normalised(self) -> None:
        row = await self.engine.touch(
            session_id="bad-endpoint",
            conversation_id="conversation",
            user_key="aaron",
            endpoint_kind="browser_magic",
        )
        self.assertEqual(row["endpoint_kind"], "unknown")

    async def test_existing_v1702_database_adds_endpoint_kind(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            db = Path(temp.name) / "v1702.db"
            with sqlite3.connect(db) as connection:
                connection.executescript(
                    """
                    CREATE TABLE voice_sessions (
                        session_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                        user_key TEXT NOT NULL, satellite_id TEXT, device_id TEXT,
                        status TEXT NOT NULL, turn_count INTEGER NOT NULL DEFAULT 0,
                        started_at TEXT NOT NULL, last_activity_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL, last_intent TEXT, close_reason TEXT,
                        interrupt_count INTEGER NOT NULL DEFAULT 0,
                        last_interrupted_at TEXT, last_interrupt_reason TEXT,
                        last_interrupt_media_player TEXT
                    );
                    """
                )
            migrated = VoiceSessionEngine(str(db))
            row = await migrated.touch(
                session_id="phone", conversation_id="conversation", user_key="aaron",
                device_id="phone", endpoint_kind="mobile_app",
            )
            self.assertEqual(row["endpoint_kind"], "mobile_app")
        finally:
            temp.cleanup()

    async def test_status_reports_release_version(self) -> None:
        status = await self.engine.status()
        self.assertEqual(status["version"], "17.0.3")
        self.assertEqual(status["idle_timeout_seconds"], 45)
        self.assertEqual(status["max_session_seconds"], 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
