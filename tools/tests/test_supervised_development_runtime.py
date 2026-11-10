from __future__ import annotations

import json
import hashlib
import subprocess
import sqlite3
import uuid
from pathlib import Path

from tools import supervised_development_controller as controller_module
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_runtime import (
    GovernorCandidateSource,
    GOVERNOR_DATABASE,
    SupervisedDevelopmentRuntime,
)
from tools.supervised_development_worker import BoundedDevelopmentWorker


def test_governor_source_is_the_authoritative_core_data_mount():
    assert GOVERNOR_DATABASE == Path(
        "/home/aaron/.local/share/jarvis-runtime/data/jarvis_improvement.db"
    )


def _source(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    (root / "tools/tests").mkdir(parents=True)
    (root / "tools/developer_session_diagnostics.py").write_text("VALUE = 1\n")
    (root / "tools/tests/test_developer_session_diagnostics.py").write_text(
        "from tools.developer_session_diagnostics import VALUE\n\n"
        "\n"
        "def test_value():\n    assert VALUE == 2\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"])
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"])
    subprocess.run(["git", "-C", str(root), "add", "."])
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"])
    return root, subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def _governor_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE improvement_failures(
                failure_id INTEGER PRIMARY KEY, summary TEXT NOT NULL,
                evidence_json TEXT NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE improvement_candidates(
                candidate_id INTEGER PRIMARY KEY, failure_id INTEGER NOT NULL,
                status TEXT NOT NULL, summary TEXT
            );
            INSERT INTO improvement_failures VALUES(1,'diagnostic failure','{"scope":"application","source":{"fixture":"test"},"affected_files":["tools/developer_session_diagnostics.py"]}','queued');
            INSERT INTO improvement_candidates VALUES(1,1,'queued','Bound diagnostics warning');
            """
        )


def _bind_review(database, root, candidate_id=1):
    with sqlite3.connect(database) as db:
        if "development_scope_json" not in {
            r[1] for r in db.execute("PRAGMA table_info(improvement_candidates)")
        }:
            db.execute(
                "ALTER TABLE improvement_candidates ADD COLUMN development_scope_json TEXT DEFAULT '{}'"
            )
        failure_id, raw = db.execute(
            "SELECT c.failure_id,f.evidence_json FROM improvement_candidates c JOIN improvement_failures f ON c.failure_id=f.failure_id WHERE c.candidate_id=?",
            (candidate_id,),
        ).fetchone()
        evidence = json.loads(raw)
        binding = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        paths = ["tools/developer_session_diagnostics.py"]
        scope = {
            "scope": "application",
            "evidence_failure_id": failure_id,
            "evidence_sha256": binding,
            "source_sha": sha,
            "affected_files": paths,
            "review": {
                "reviewed_by": "fixture",
                "source_sha": sha,
                "evidence_sha256": binding,
                "reproduction": "Disposable diagnostics test demonstrates the failure.",
                "file_findings": {paths[0]: "The fixture diagnostic returns an incorrect value."},
            },
        }
        db.execute(
            "UPDATE improvement_candidates SET development_scope_json=? WHERE candidate_id=?",
            (json.dumps(scope), candidate_id),
        )


def test_runtime_entrypoint_reaches_review_required_once(tmp_path, monkeypatch):
    source, source_sha = _source(tmp_path)
    governor_db = tmp_path / "governor.sqlite3"
    _governor_db(governor_db)
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    _bind_review(governor_db, source)
    source_reader = GovernorCandidateSource(governor_db, source)
    generation = str(uuid.uuid4())
    active_generation = str(uuid.uuid4())
    active = {"calls": 0}
    stopped = []
    guardian_state = {"state": "STOPPED"}

    def guardian():
        active["calls"] += 1
        if active["calls"] == 2 and guardian_state["state"] == "STOPPED":
            guardian_state["state"] = "ACTIVE"
        current_generation = (
            generation if guardian_state["state"] == "STOPPED" else active_generation
        )
        return {
            "state": guardian_state["state"],
            "trusted": True,
            "generation": current_generation,
            **({"previous_generation": generation} if guardian_state["state"] == "ACTIVE" else {}),
        }

    def admit(request):
        return {
            **request,
            "status": "approved",
            "authority": "jarvis-autonomy-broker",
            "expires_at": request.get("expires_at", 0) or 2_000_000_000,
            "guardian_generation": active_generation,
        }

    monkeypatch.setattr(controller_module, "_request_protected_admission", admit)

    def provider(prompt, model, timeout):
        return (
            '{"protocol_version":1,"explanation":"Bound diagnostics warning.","edits":'
            '[{"path":"tools/developer_session_diagnostics.py",'
            '"old_text":"VALUE = 1\\n","new_text":"VALUE = 2\\n"}]}',
            {"input_tokens": 100, "output_tokens": 40},
        )

    worker = BoundedDevelopmentWorker(
        source,
        worktree_root=tmp_path / "worktrees",
        provider_call=provider,
        input_price_per_million=10,
        output_price_per_million=10,
    )

    def stop_and_mark():
        stopped.append(True)
        guardian_state["state"] = "STOPPED"

    runtime = SupervisedDevelopmentRuntime(
        source_reader,
        controller,
        source_root=source,
        worktree_root=tmp_path / "worktrees",
        guardian=guardian,
        stop=stop_and_mark,
        sleep=lambda _: None,
        worker=worker,
    )
    # The admission fixture turns the disposable Guardian active only after
    # the runtime reaches the real controller start boundary.
    result = runtime.run_once()
    assert result["state"] == "REVIEW_REQUIRED"
    assert result["provider_calls"] == 1
    assert worker.actual_provider_calls == 1
    assert stopped
    assert controller.snapshot(result["id"])["parameters"]["candidate_id"] == 1


def test_runtime_has_no_execution_without_eligible_governor_candidate(tmp_path):
    database = tmp_path / "governor.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE improvement_failures(failure_id INTEGER PRIMARY KEY, summary TEXT, evidence_json TEXT, status TEXT);"
            "CREATE TABLE improvement_candidates(candidate_id INTEGER PRIMARY KEY, failure_id INTEGER, status TEXT, summary TEXT);"
        )
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    source = GovernorCandidateSource(database)
    runtime = SupervisedDevelopmentRuntime(
        source,
        controller,
        source_root=tmp_path,
        guardian=lambda: {"state": "STOPPED", "trusted": True, "generation": str(uuid.uuid4())},
    )
    assert runtime.run_once() == {"status": "no_eligible_candidate"}
    assert controller.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_candidate_eligibility_skips_unrelated_and_missing_evidence(tmp_path):
    database = tmp_path / "governor.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE improvement_failures(
                failure_id INTEGER PRIMARY KEY, summary TEXT, evidence_json TEXT, status TEXT
            );
            CREATE TABLE improvement_candidates(
                candidate_id INTEGER PRIMARY KEY, failure_id INTEGER, status TEXT, summary TEXT
            );
            INSERT INTO improvement_failures VALUES
                (1,'unrelated','{"scope":"other","affected_files":["tools/other.py"]}','queued'),
                (2,'missing evidence','{}','queued'),
                (3,'diagnostic failure','{"scope":"application","affected_files":["tools/developer_session_diagnostics.py"]}','queued');
            INSERT INTO improvement_candidates VALUES
                (33,1,'queued','Unrelated Home Assistant issue'),
                (34,2,'queued','Diagnostics issue without evidence'),
                (35,3,'queued','Bound diagnostics warning');
            """
        )
    source = GovernorCandidateSource(database)
    try:
        selected = source.next()
        assert selected is None  # Legacy conversation fields are not verified scope.
        assert source.next({35}) is None
    finally:
        source.close()


def test_candidate_selection_survives_read_only_restart(tmp_path):
    database = tmp_path / "governor.sqlite3"
    _governor_db(database)
    root, _ = _source(tmp_path)
    _bind_review(database, root)
    first = GovernorCandidateSource(database, root)
    try:
        expected = first.next()
    finally:
        first.close()
    second = GovernorCandidateSource(database, root)
    try:
        assert second.next() == expected
    finally:
        second.close()


def test_candidate_scope_metadata_binds_preserved_failure_evidence(tmp_path):
    database = tmp_path / "governor.sqlite3"
    failure_evidence = {"source": {"interaction_id": 9}}
    evidence_hash = hashlib.sha256(
        json.dumps(failure_evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE improvement_failures(
                failure_id INTEGER PRIMARY KEY, summary TEXT, evidence_json TEXT, status TEXT
            );
            CREATE TABLE improvement_candidates(
                candidate_id INTEGER PRIMARY KEY, failure_id INTEGER, status TEXT,
                summary TEXT, development_scope_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        db.execute(
            "INSERT INTO improvement_failures VALUES (?,?,?,?)",
            (9, "Slow response", json.dumps(failure_evidence), "queued"),
        )
        db.execute(
            "INSERT INTO improvement_candidates VALUES (?,?,?,?,?)",
            (
                42,
                9,
                "queued",
                "Reduce latency",
                json.dumps(
                    {
                        "scope": "application",
                        "evidence_failure_id": 9,
                        "evidence_sha256": evidence_hash,
                        "source_sha": "a" * 40,
                        "affected_files": ["app/latency.py"],
                    }
                ),
            ),
        )
    root, _ = _source(tmp_path)
    _bind_review(database, root, 42)
    source = GovernorCandidateSource(database, root)
    try:
        selected = source.next()
        assert selected is not None
        assert selected["candidate_id"] == 42
        assert selected["allowed_paths"] == ("tools/developer_session_diagnostics.py",)
    finally:
        source.close()


def test_candidate_scope_metadata_rejects_tampered_evidence_binding(tmp_path):
    database = tmp_path / "governor.sqlite3"
    evidence = {"source": {"interaction_id": 1}}
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE improvement_failures(
                failure_id INTEGER PRIMARY KEY, summary TEXT, evidence_json TEXT, status TEXT
            );
            CREATE TABLE improvement_candidates(
                candidate_id INTEGER PRIMARY KEY, failure_id INTEGER, status TEXT,
                summary TEXT, development_scope_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        db.execute(
            "INSERT INTO improvement_failures VALUES (?,?,?,?)",
            (1, "Failure", json.dumps(evidence), "queued"),
        )
        db.execute(
            "INSERT INTO improvement_candidates VALUES (?,?,?,?,?)",
            (
                1,
                1,
                "queued",
                "Tampered",
                json.dumps(
                    {
                        "scope": "application",
                        "evidence_failure_id": 1,
                        "evidence_sha256": "0" * 64,
                        "source_sha": "a" * 40,
                        "affected_files": ["app/example.py"],
                    }
                ),
            ),
        )
    source = GovernorCandidateSource(database)
    try:
        assert source.next() is None
    finally:
        source.close()


def test_candidate_scope_skips_source_drift_before_task_creation(tmp_path, monkeypatch):
    database = tmp_path / "governor.sqlite3"
    evidence = {"source": {"interaction_id": 7}}
    evidence_hash = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE improvement_failures(
                failure_id INTEGER PRIMARY KEY, summary TEXT, evidence_json TEXT, status TEXT
            );
            CREATE TABLE improvement_candidates(
                candidate_id INTEGER PRIMARY KEY, failure_id INTEGER, status TEXT,
                summary TEXT, development_scope_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        db.execute(
            "INSERT INTO improvement_failures VALUES (?,?,?,?)",
            (1, "Owner-requested improvement", json.dumps(evidence), "queued"),
        )
        db.execute(
            "INSERT INTO improvement_candidates VALUES (?,?,?,?,?)",
            (
                74,
                1,
                "queued",
                "Reduce response latency",
                json.dumps(
                    {
                        "scope": "application",
                        "evidence_failure_id": 1,
                        "evidence_sha256": evidence_hash,
                        "source_sha": "a" * 40,
                        "affected_files": ["bridge/app/ai_engine.py"],
                    }
                ),
            ),
        )
    source = GovernorCandidateSource(database)
    monkeypatch.setattr(source, "_current_source_sha", lambda: "b" * 40)
    try:
        assert source.next() is None
    finally:
        source.close()


def test_candidate_scope_supports_multiple_normal_files_and_rejects_protected():
    evidence = {
        "scope": "application",
        "affected_files": ["app/example.py", "tests/test_example.py", "pyproject.toml"],
    }
    assert GovernorCandidateSource._is_scope_eligible(
        "Improve diagnostics for malformed developer results", evidence
    )
    assert not GovernorCandidateSource._is_scope_eligible(
        "Improve diagnostics", {"affected_files": ["tools/autonomy_guardian.py"]}
    )


def test_controller_uses_pinned_git_for_source_queries(tmp_path, monkeypatch):
    from tools import supervised_development_runtime as runtime

    source_root = tmp_path / "source"
    source_root.mkdir()
    requests = []

    def checked_run(command, **kwargs):
        requests.append((command, kwargs))
        assert command[:2] == ["/usr/bin/git", "--no-optional-locks"]
        assert ["-c", "safe.directory=" + str(source_root)] == command[2:4]
        assert ["-c", "core.fsmonitor=false"] == command[4:6]
        assert ["-c", "core.hooksPath=/dev/null"] == command[6:8]
        assert ["-c", "core.attributesFile=/dev/null"] == command[8:10]
        assert command[10:12] == ["-C", str(source_root)]
        assert kwargs["env"] == runtime.SAFE_ENV
        assert kwargs["stdin"] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, b"a" * 40 + b"\n", b"")

    monkeypatch.setattr(runtime.subprocess, "run", checked_run)
    assert runtime._trusted_source_git(source_root, "rev-parse", "HEAD") == "a" * 40
    assert requests[-1][0][-2:] == ["rev-parse", "HEAD"]
    assert runtime._trusted_source_git(source_root, "status", "--porcelain") == "a" * 40
    assert requests[-1][0][-2:] == ["status", "--porcelain"]


def test_controller_git_rejects_failed_or_untrusted_checkout(tmp_path, monkeypatch):
    import pytest
    from tools import supervised_development_runtime as runtime
    from tools.supervised_development_controller import LifecycleError

    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 128, b"", b"dubious ownership"),
    )
    with pytest.raises(LifecycleError, match="trusted_source_snapshot_unavailable"):
        runtime._trusted_source_git(root, "rev-parse", "HEAD")
    with pytest.raises(LifecycleError, match="trusted_source_snapshot_unavailable"):
        runtime._trusted_source_git(tmp_path / "missing", "rev-parse", "HEAD")
