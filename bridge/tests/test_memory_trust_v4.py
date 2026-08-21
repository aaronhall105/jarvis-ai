from pathlib import Path

import pytest

from app.memory_engine import MemoryEngine, MemoryError


@pytest.mark.asyncio
async def test_update_preserves_previous_value_with_provenance(tmp_path: Path) -> None:
    engine = MemoryEngine(str(tmp_path / "memory.db"))
    saved = await engine.save(
        "preference", "Favourite drink", "Aaron prefers coffee.",
        owner_key="aaron", source="explicit_user", confidence=1.0,
    )
    updated = await engine.save(
        "preference", "Favourite drink", "Aaron now prefers tea.",
        owner_key="aaron", source="explicit_user", confidence=1.0,
    )

    assert updated["id"] == saved["id"]
    assert updated["content"] == "Aaron now prefers tea."
    assert updated["source"] == "explicit_user"
    assert updated["confidence"] == 1.0
    history = await engine.history(saved["id"], owner_key="aaron")
    assert history[0]["operation"] == "updated"
    assert history[0]["snapshot"]["content"] == "Aaron prefers coffee."


@pytest.mark.asyncio
async def test_expired_memory_is_not_current_but_history_metadata_remains(tmp_path: Path) -> None:
    engine = MemoryEngine(str(tmp_path / "memory.db"))
    saved = await engine.save(
        "personal", "Working location", "Aaron is working from home today.",
        owner_key="aaron", expires_at="2000-01-01T00:00:00Z",
    )
    assert saved["expires_at"].endswith("+00:00")
    assert await engine.search("working location", owner_key="aaron") == []
    assert await engine.list_memories(owner_key="aaron") == []


@pytest.mark.asyncio
async def test_invalid_provenance_and_confidence_are_rejected(tmp_path: Path) -> None:
    engine = MemoryEngine(str(tmp_path / "memory.db"))
    with pytest.raises(MemoryError):
        await engine.save("general", "Fact", "Value", source="rumour")
    with pytest.raises(MemoryError):
        await engine.save("general", "Fact", "Value", confidence=1.5)


@pytest.mark.asyncio
async def test_subject_cannot_view_private_revision_history(tmp_path: Path) -> None:
    engine = MemoryEngine(str(tmp_path / "memory.db"))
    saved = await engine.save(
        "personal", "Amber surprise", "Aaron bought Amber a surprise gift.",
        owner_key="aaron", subject_key="amber", visibility="private",
    )
    await engine.save(
        "personal", "Amber surprise", "Aaron changed Amber's surprise gift.",
        owner_key="aaron", subject_key="amber", visibility="private",
    )
    assert await engine.history(saved["id"], owner_key="amber") == []
    assert len(await engine.history(saved["id"], owner_key="aaron")) == 1


@pytest.mark.asyncio
async def test_retirement_is_idempotent_and_recoverable(tmp_path: Path) -> None:
    engine = MemoryEngine(str(tmp_path / "memory.db"))
    saved = await engine.save(
        "general", "Door code hint", "The code hint is the blue book.",
        owner_key="aaron", visibility="private",
    )

    assert await engine.delete_by_id(saved["id"], owner_key="aaron") is True
    assert await engine.delete_by_id(saved["id"], owner_key="aaron") is False
    assert await engine.search("blue book", owner_key="aaron") == []
    assert await engine.list_memories(owner_key="aaron") == []

    restored = await engine.restore(saved["id"], owner_key="aaron")
    assert restored is not None
    assert restored["retired_at"] is None
    assert await engine.restore(saved["id"], owner_key="aaron") is None
    assert len(await engine.search("blue book", owner_key="aaron")) == 1
    operations = [
        item["operation"]
        for item in await engine.history(saved["id"], owner_key="aaron")
    ]
    assert operations == ["restored", "retired"]
