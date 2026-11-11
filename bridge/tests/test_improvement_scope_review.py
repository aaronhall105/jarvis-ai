"""Disposable evidence review, queue classification and append-only re-evaluation."""

import hashlib
import json
import sqlite3
import subprocess

import pytest

from app.self_improvement import SelfImprovementEngine
from app.user_context import UserContext
from tools.supervised_development_runtime import GovernorCandidateSource


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
async def case(tmp_path):
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app/latency.py").write_text("VALUE = 1\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    engine = SelfImprovementEngine(
        str(tmp_path / "fixture.db"), auto_prepare=False, development_source_root=str(root)
    )
    actor = UserContext.from_request(
        user_id="owner",
        user_name="Owner",
        user_is_admin=True,
        device_id="fixture",
        voice_mode=False,
        privilege_verified=True,
    )
    await engine.observe_interaction(
        conversation_id="fixture",
        actor=actor,
        raw_text="Measure latency",
        result={"success": True, "intent": "general", "timings": {"jarvis_request_total_ms": 9000}},
    )
    return engine, root


def review(engine, root):
    evidence = engine._get_failure_sync(1)["evidence"]
    return {
        "source_sha": git(root, "rev-parse", "HEAD"),
        "evidence_sha256": hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "reproduction": "Disposable test measured 9000ms, exceeding 7000ms budget.",
        "file_findings": {
            "app/latency.py": "The instrumented latency function accounts for the measured delay."
        },
    }


async def prepare(engine, root, **kwargs):
    return await engine.prepare_evidenced_improvement(
        failure_id=1,
        affected_files=["app/latency.py"],
        summary="Repair measured latency",
        actor="fixture-reviewer",
        review=review(engine, root),
        **kwargs,
    )


def report(engine, root):
    reader = GovernorCandidateSource(engine.database_path, root)
    try:
        return reader.review_queue(), reader.next()
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_41_unscoped_candidates_preserved_and_ineligible(case):
    engine, root = case
    original = engine._get_failure_sync(1)["evidence"]
    with engine._connection() as db:
        for _ in range(41):
            db.execute(
                "INSERT INTO improvement_candidates (failure_id,created_at,updated_at,status) VALUES (1,'fixture','fixture','queued')"
            )
    before = engine.database_path.read_bytes()
    classifications, selected = report(engine, root)
    assert len(classifications) == 41
    assert {r["classification"] for r in classifications} == {"unscoped_review_required"}
    assert selected is None
    assert engine.database_path.read_bytes() == before
    assert engine._get_failure_sync(1)["evidence"] == original
    assert len(await engine.list_candidates(limit=100)) == 41


@pytest.mark.asyncio
async def test_paths_and_feedback_alone_do_not_verify_software_defect(case):
    engine, root = case
    ok, identifier, reason = await engine.prepare_evidenced_improvement(
        failure_id=1, affected_files=["app/latency.py"], summary="Claim a defect", actor="fixture"
    )
    assert not ok and identifier is None and "review evidence" in reason
    assert report(engine, root)[1] is None


@pytest.mark.asyncio
async def test_review_admits_exact_source_and_changed_evidence_blocks(case):
    engine, root = case
    assert (await prepare(engine, root))[0]
    assert report(engine, root)[1]["candidate_id"] == 1
    with engine._connection() as db:
        db.execute(
            "UPDATE improvement_failures SET evidence_json=? WHERE failure_id=1",
            (json.dumps({"source": {"changed": True}}),),
        )
    classifications, selected = report(engine, root)
    assert selected is None
    assert classifications[0]["classification"] == "stale_failure_evidence"


@pytest.mark.asyncio
async def test_five_old_sources_require_explicit_append_only_reevaluation(case):
    engine, root = case
    assert (await prepare(engine, root))[0]
    with engine._connection() as db:
        old = dict(
            db.execute("SELECT * FROM improvement_candidates WHERE candidate_id=1").fetchone()
        )
        for _ in range(4):
            db.execute(
                "INSERT INTO improvement_candidates (failure_id,created_at,updated_at,status,summary,development_scope_json) VALUES (1,'fixture','fixture','queued',?,?)",
                (old["summary"], old["development_scope_json"]),
            )
        before = [
            tuple(row)
            for row in db.execute("SELECT * FROM improvement_candidates ORDER BY candidate_id")
        ]
        audit_before = [
            tuple(row) for row in db.execute("SELECT * FROM improvement_audit ORDER BY audit_id")
        ]
    failure_before = await engine.get_failure(1)
    (root / "app/latency.py").write_text("VALUE = 2\n")
    assert report(engine, root)[1] is None  # Dirty snapshot never admitted.
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "changed source",
    )
    classifications, selected = report(engine, root)
    assert selected is None
    assert [r["classification"] for r in classifications] == [
        "historical_source_review_required"
    ] * 5
    # Normal preparation cannot silently refresh an old source binding.
    assert not (await prepare(engine, root))[0]
    ok, new_id, _ = await prepare(engine, root, reevaluate_candidate_id=1)
    assert ok and new_id == 6
    assert (await prepare(engine, root, reevaluate_candidate_id=1))[1] == new_id
    new = await engine.get_candidate(new_id)
    scope = json.loads(new["development_scope_json"])
    assert scope["reevaluation"]["original_scope"] == json.loads(old["development_scope_json"])
    assert scope["source_sha"] == git(root, "rev-parse", "HEAD")
    assert report(engine, root)[1]["candidate_id"] == new_id
    assert await engine.get_failure(1) == failure_before
    with engine._connection() as db:
        assert [
            tuple(row)
            for row in db.execute(
                "SELECT * FROM improvement_candidates WHERE candidate_id<=5 ORDER BY candidate_id"
            )
        ] == before
        assert [
            tuple(row) for row in db.execute("SELECT * FROM improvement_audit ORDER BY audit_id")
        ][: len(audit_before)] == audit_before


@pytest.mark.asyncio
async def test_stale_review_and_deleted_files_cannot_be_rebound(case):
    engine, root = case
    old_review = review(engine, root)
    assert (await prepare(engine, root))[0]
    (root / "app/latency.py").unlink()
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "remove file",
    )
    result = await engine.prepare_evidenced_improvement(
        failure_id=1,
        affected_files=["app/latency.py"],
        summary="Old reproduction",
        actor="fixture",
        review=old_review,
        reevaluate_candidate_id=1,
    )
    assert not result[0]
    assert len(await engine.list_candidates()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "./app/latency.py",
        "app//latency.py",
        "../latency.py",
        "app\\latency.py",
        ".git/config.py",
        "tools/supervised_development_worker.py",
    ],
)
async def test_noncanonical_or_protected_scope_never_admitted(case, path):
    engine, root = case
    assert not GovernorCandidateSource._is_scope_eligible(
        "test defect", {"scope": "application", "affected_files": [path]}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_review_audit_failure_rolls_back_candidate_scope(case, existing):
    engine, root = case
    if existing:
        assert (await engine.queue_failure(1, "fixture"))[0]
    with engine._connection() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM improvement_candidates")]
        db.execute(
            "CREATE TRIGGER fail_review BEFORE INSERT ON improvement_audit "
            "WHEN NEW.event_type='candidate_reviewed' BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture"):
        await prepare(engine, root)
    with engine._connection() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM improvement_candidates")] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["approved", "developing", "deploying", "rejected"])
async def test_reevaluation_cannot_replace_nonqueued_candidate(case, state):
    engine, root = case
    assert (await prepare(engine, root))[0]
    with engine._connection() as db:
        db.execute("UPDATE improvement_candidates SET status=? WHERE candidate_id=1", (state,))
        before = tuple(db.execute("SELECT * FROM improvement_candidates").fetchone())
    (root / "app/latency.py").write_text("VALUE = 2\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "advance",
    )
    assert not (await prepare(engine, root, reevaluate_candidate_id=1))[0]
    with engine._connection() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM improvement_candidates")] == [
            before
        ]
