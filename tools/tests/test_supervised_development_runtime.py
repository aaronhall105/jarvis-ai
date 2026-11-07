from __future__ import annotations

import json
import subprocess
import sqlite3
import uuid
from pathlib import Path

from tools import supervised_development_controller as controller_module
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_runtime import (
    GovernorCandidateSource,
    SupervisedDevelopmentRuntime,
)
from tools.supervised_development_worker import BoundedDevelopmentWorker


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
            INSERT INTO improvement_failures VALUES(1,'diagnostic failure','{"source":"test"}','queued');
            INSERT INTO improvement_candidates VALUES(1,1,'queued','Bound diagnostics warning');
            """
        )


def test_runtime_entrypoint_reaches_review_required_once(tmp_path, monkeypatch):
    source, source_sha = _source(tmp_path)
    governor_db = tmp_path / "governor.sqlite3"
    _governor_db(governor_db)
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    source_reader = GovernorCandidateSource(governor_db)
    generation = str(uuid.uuid4())
    active = {"calls": 0}
    stopped = []
    guardian_state = {"state": "STOPPED"}

    def guardian():
        active["calls"] += 1
        if active["calls"] == 2 and guardian_state["state"] == "STOPPED":
            guardian_state["state"] = "ACTIVE"
        return {
            "state": guardian_state["state"],
            "trusted": True,
            "generation": generation,
        }

    def admit(request):
        return {
            **request,
            "status": "approved",
            "authority": "jarvis-autonomy-broker",
            "expires_at": request.get("expires_at", 0) or 2_000_000_000,
            "guardian_generation": generation,
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
