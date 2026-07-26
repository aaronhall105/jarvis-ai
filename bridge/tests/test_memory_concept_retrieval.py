from pathlib import Path

import pytest

from app.memory_engine import MemoryEngine


def make_engine(tmp_path: Path) -> MemoryEngine:
    return MemoryEngine(str(tmp_path / "memory.db"))


@pytest.mark.asyncio
async def test_first_person_health_question_finds_lactose_intolerance(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    saved = await engine.save(
        "personal",
        "Amber lactose intolerance",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    matches = await engine.search(
        "Do I have any health conditions?",
        owner_key="amber",
    )

    assert [item["id"] for item in matches] == [saved["id"]]
    assert matches[0]["subject_key"] == "amber"


@pytest.mark.asyncio
async def test_named_health_question_finds_subject_memory_for_creator(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    saved = await engine.save(
        "personal",
        "Amber lactose intolerance",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    matches = await engine.search(
        "Does Amber have any medical conditions?",
        owner_key="aaron",
    )

    assert matches[0]["id"] == saved["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Do I have any dietary requirements?",
        "Is there any food I cannot eat?",
        "Do I have any allergies or intolerances?",
        "What health information is saved about me?",
    ],
)
async def test_equivalent_profile_questions_retrieve_intolerance(
    tmp_path: Path,
    question: str,
) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "personal",
        "Amber lactose intolerance",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    matches = await engine.search(question, owner_key="amber")
    assert matches
    assert "lactose intolerant" in matches[0]["content"].lower()


@pytest.mark.asyncio
async def test_concept_retrieval_does_not_bypass_private_visibility(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "personal",
        "Aaron medical note",
        "Aaron has a private medical note.",
        owner_key="aaron",
        subject_key="aaron",
        visibility="private",
        sensitivity="sensitive",
    )

    assert await engine.search(
        "Do I have any health conditions?",
        owner_key="amber",
    ) == []


@pytest.mark.asyncio
async def test_unrelated_memory_is_not_returned_for_health_question(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "preference",
        "Amber favourite colour",
        "Amber's favourite colour is blue.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="normal",
    )

    assert await engine.search(
        "Do I have any health conditions?",
        owner_key="amber",
    ) == []


@pytest.mark.asyncio
async def test_literal_match_still_works_and_ranks_precisely(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    health = await engine.save(
        "personal",
        "Amber lactose intolerance",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )
    await engine.save(
        "preference",
        "Amber dietary preference",
        "Amber prefers tea.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="normal",
    )

    matches = await engine.search("lactose", owner_key="amber")
    assert matches[0]["id"] == health["id"]


@pytest.mark.asyncio
async def test_context_for_contains_semantically_retrieved_health_fact(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    await engine.save(
        "personal",
        "Amber lactose intolerance",
        "Amber is lactose intolerant.",
        owner_key="aaron",
        subject_key="amber",
        visibility="subject_and_owner",
        sensitivity="sensitive",
    )

    context = await engine.context_for(
        "Do I have any health conditions?",
        owner_key="amber",
    )

    assert "lactose intolerant" in context.lower()
    assert "about the current user" in context.lower()
