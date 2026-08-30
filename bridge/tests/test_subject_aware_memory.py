import sqlite3
from pathlib import Path

import pytest

from app.memory_engine import MemoryEngine, MemoryError


def make_engine(tmp_path: Path) -> MemoryEngine:
    return MemoryEngine(str(tmp_path / "memory.db"))


def create_v2_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE memories (
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
        connection.execute(
            """
            INSERT INTO memories (
                owner_key, category, subject, content, search_text,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aaron",
                "personal",
                "Amber health conditions",
                "Amber is lactose intolerant.",
                "personal amber health conditions amber is lactose intolerant",
                "2026-07-01T00:00:00+00:00",
                "2026-07-01T00:00:00+00:00",
            ),
        )
        connection.commit()


@pytest.mark.asyncio
async def test_migration_shares_amber_health_memory_with_amber(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    create_v2_database(database)

    engine = MemoryEngine(str(database))
    matches = await engine.search(
        "Do I have any health conditions?",
        owner_key="amber",
    )

    assert len(matches) == 1
    assert matches[0]["subject_key"] == "amber"
    assert matches[0]["visibility"] == "subject_and_owner"
    assert matches[0]["sensitivity"] == "sensitive"
    assert "lactose intolerant" in matches[0]["content"].lower()


@pytest.mark.asyncio
async def test_creator_can_still_access_migrated_shared_memory(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    create_v2_database(database)
    engine = MemoryEngine(str(database))

    matches = await engine.search("Amber health", owner_key="aaron")
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_unrelated_user_cannot_access_shared_subject_memory(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "personal",
        "Amber health conditions",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    assert await engine.search("health conditions", owner_key="guest") == []


@pytest.mark.asyncio
async def test_explicit_private_memory_about_amber_stays_private(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "personal",
        "Amber surprise present",
        "Amber's surprise present is hidden in the wardrobe.",
        owner_key="aaron",
        subject_key="amber",
        visibility="private",
        sensitivity="normal",
    )

    assert await engine.search("surprise present", owner_key="amber") == []
    assert len(await engine.search("surprise present", owner_key="aaron")) == 1


@pytest.mark.asyncio
async def test_household_memory_visible_to_both_known_users_only(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "home",
        "Bin collection day",
        "The household bin collection day is Friday.",
        owner_key="aaron",
        subject_key="household",
        visibility="household",
        sensitivity="normal",
    )

    assert len(await engine.search("bin collection", owner_key="aaron")) == 1
    assert len(await engine.search("bin collection", owner_key="amber")) == 1
    assert await engine.search("bin collection", owner_key="anonymous") == []


@pytest.mark.asyncio
async def test_sensitive_memory_cannot_be_household_wide(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(MemoryError, match="cannot be shared household-wide"):
        await engine.save(
            "personal",
            "Amber health conditions",
            "Amber is lactose intolerant.",
            owner_key="aaron",
            subject_key="household",
            visibility="household",
            sensitivity="sensitive",
        )


@pytest.mark.asyncio
async def test_subject_can_update_shared_memory_about_themselves(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    first = await engine.save(
        "personal",
        "Amber health conditions",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )
    updated = await engine.save(
        "personal",
        "Amber health conditions",
        "Amber is lactose intolerant and avoids ordinary milk.",
        owner_key="amber",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    assert updated["id"] == first["id"]
    assert updated["owner_key"] == "aaron"
    assert updated["updated_by"] == "amber"
    assert "avoids ordinary milk" in updated["content"]


@pytest.mark.asyncio
async def test_subject_can_delete_shared_memory_about_themselves(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    saved = await engine.save(
        "personal",
        "Amber health conditions",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    assert await engine.delete_by_id(saved["id"], owner_key="amber") is True
    assert await engine.search("lactose", owner_key="aaron") == []


@pytest.mark.asyncio
async def test_subject_cannot_delete_creators_private_memory(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    saved = await engine.save(
        "personal",
        "Amber surprise present",
        "A private present note.",
        owner_key="aaron",
        subject_key="amber",
        visibility="private",
        sensitivity="normal",
    )

    assert await engine.delete_by_id(saved["id"], owner_key="amber") is False
    assert len(await engine.search("present", owner_key="aaron")) == 1


@pytest.mark.asyncio
async def test_backward_compatible_save_infers_named_subject(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    saved = await engine.save(
        "personal",
        "Amber health conditions",
        "Amber is lactose intolerant.",
        owner_key="aaron",
    )

    assert saved["subject_key"] == "amber"
    assert saved["visibility"] == "subject_and_owner"
    assert saved["sensitivity"] == "sensitive"


@pytest.mark.asyncio
async def test_first_person_health_query_gets_shared_context(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "personal",
        "Amber health conditions",
        "Amber is lactose intolerant.",
        owner_key="aaron",
    )

    context = await engine.context_for(
        "Do I have any health conditions?",
        owner_key="amber",
    )
    assert "lactose intolerant" in context.lower()
    assert "about the current user" in context.lower()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    create_v2_database(database)
    first = MemoryEngine(str(database))
    first_status = await first.status("aaron")
    second = MemoryEngine(str(database))
    second_status = await second.status("aaron")

    assert first_status["schema_version"] == 4
    assert second_status["schema_version"] == 4
    assert second_status["total_count"] == 1


@pytest.mark.asyncio
async def test_legacy_surprise_about_amber_is_not_auto_shared(tmp_path: Path) -> None:
    database = tmp_path / "legacy_private.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE memories (
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
        connection.execute(
            """
            INSERT INTO memories (
                owner_key, category, subject, content, search_text,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aaron",
                "personal",
                "Amber surprise present",
                "Amber's surprise present is hidden in the wardrobe.",
                "personal amber surprise present hidden wardrobe",
                "2026-07-01T00:00:00+00:00",
                "2026-07-01T00:00:00+00:00",
            ),
        )
        connection.commit()

    engine = MemoryEngine(str(database))
    assert await engine.search("surprise present", owner_key="amber") == []
    owner_matches = await engine.search("surprise present", owner_key="aaron")
    assert owner_matches[0]["visibility"] == "private"


@pytest.mark.asyncio
async def test_backward_compatible_surprise_save_defaults_private(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    saved = await engine.save(
        "personal",
        "Amber surprise present",
        "Amber's surprise present is hidden in the wardrobe.",
        owner_key="aaron",
    )
    assert saved["subject_key"] == "amber"
    assert saved["visibility"] == "private"
    assert await engine.search("surprise present", owner_key="amber") == []
