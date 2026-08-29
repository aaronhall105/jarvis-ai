from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

SPEC = importlib.util.spec_from_file_location(
    "self_development_worker",
    TOOLS / "self_development_worker.py",
)
assert SPEC is not None and SPEC.loader is not None
development = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = development
SPEC.loader.exec_module(development)
base = development.base


def config() -> object:
    return base.WorkerConfig(
        model="test",
        poll_seconds=15,
        max_attempts_per_day=3,
        max_patch_lines=100,
        max_changed_files=3,
        github_enabled=False,
        ai_review_enabled=True,
        notify_enabled=False,
        notify_service="notify.mobile_app_aaron_s_phone",
        auto_deploy_low_risk=False,
        proposal_only=True,
        candidate_timeout_seconds=60,
        deploy_health_timeout_seconds=30,
        base_branch="jarvis/unified-production",
    )


def policy() -> dict[str, object]:
    return {
        "allowed_edit_paths": ["bridge/app/*.py", "bridge/tests/*.py"],
        "forbidden_paths": [".env", "tools/**", "docker-compose.yml"],
        "forbidden_added_patterns": [r"shell\s*=\s*True", r"\beval\s*\("],
    }


def initialise_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "bridge/app").mkdir(parents=True)
    (repo / "bridge/tests").mkdir(parents=True)
    (repo / "bridge/app/example.py").write_text(
        "value = 1\nresult = value + 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo


def test_incremental_repair_preserves_previous_good_edit(tmp_path: Path) -> None:
    repo = initialise_repo(tmp_path)

    first = {
        "edits": [
            {
                "path": "bridge/app/example.py",
                "old_text": "value = 1\n",
                "new_text": "value = 2\n",
            }
        ]
    }
    patch_one, paths_one, _ = development._apply_incremental_edits(
        repo, first, policy(), config()
    )
    assert paths_one == ["bridge/app/example.py"]
    assert "+value = 2" in patch_one

    repair = {
        "edits": [
            {
                "path": "bridge/app/example.py",
                "old_text": "result = value + 1\n",
                "new_text": "result = value + 2\n",
            }
        ]
    }
    patch_two, paths_two, _ = development._apply_incremental_edits(
        repo, repair, policy(), config()
    )

    source = (repo / "bridge/app/example.py").read_text(encoding="utf-8")
    assert source == "value = 2\nresult = value + 2\n"
    assert paths_two == ["bridge/app/example.py"]
    assert "+value = 2" in patch_two
    assert "+result = value + 2" in patch_two


def test_failed_incremental_step_is_atomic(tmp_path: Path) -> None:
    repo = initialise_repo(tmp_path)
    before = (repo / "bridge/app/example.py").read_text(encoding="utf-8")

    payload = {
        "edits": [
            {
                "path": "bridge/app/example.py",
                "old_text": "value = 1\n",
                "new_text": "value = 3\n",
            },
            {
                "path": "bridge/app/example.py",
                "old_text": "missing = True\n",
                "new_text": "missing = False\n",
            },
        ]
    }

    with pytest.raises(base.WorkerError):
        development._apply_incremental_edits(repo, payload, policy(), config())

    assert (repo / "bridge/app/example.py").read_text(encoding="utf-8") == before
    assert subprocess.run(
        ["git", "diff", "--quiet"], cwd=repo, check=False
    ).returncode == 0


def test_run_once_resumes_developing_before_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(base, "update_setting", lambda key, value: calls.append(("setting", key)))
    monkeypatch.setattr(base, "improvement_enabled", lambda: True)

    def fetch(statuses: tuple[str, ...]):
        calls.append(("fetch", statuses))
        if statuses == ("developing",):
            return {"candidate_id": 41, "failure_id": 9, "status": "developing"}
        if statuses == ("queued",):
            return {"candidate_id": 42, "failure_id": 10, "status": "queued"}
        return None

    monkeypatch.setattr(base, "fetch_candidate", fetch)
    monkeypatch.setattr(
        development,
        "process_development_candidate",
        lambda candidate, cfg, env: calls.append(("processed", candidate["candidate_id"])),
    )

    assert development.run_once(config(), {}) is True
    assert ("processed", 41) in calls
    assert ("processed", 42) not in calls


def test_repair_budget_is_bounded() -> None:
    assert development._env_int({}, "JARVIS_DEVELOPMENT_MAX_REPAIRS", 4, 0, 8) == 4


def test_v25_migrates_only_nonbudget_edit_failures() -> None:
    state = {
        "version": 1,
        "attempts": [
            {
                "number": 1,
                "stage": "edit",
                "outcome": "repair_required",
                "summary": "bad proposal",
            },
            {
                "number": 2,
                "stage": "semantic_review",
                "outcome": "repair_required",
                "summary": "real semantic failure",
                "feedback": "fix race",
            },
        ],
    }

    changed = development._migrate_legacy_attempt_state(state)

    assert changed is True
    assert len(state["attempts"]) == 1
    assert state["attempts"][0]["number"] == 1
    assert state["attempts"][0]["stage"] == "semantic_review"
    assert len(state["nonbudget_events"]) == 1
    assert state["nonbudget_events"][0]["stage"] == "edit"
    assert state["version"] == development.STATE_VERSION


def test_v25_record_attempt_persists_exact_repair_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[dict] = []

    monkeypatch.setattr(
        development,
        "_save_state",
        lambda candidate_id, state, **fields: saved.append(dict(state)),
    )
    monkeypatch.setattr(base, "audit", lambda *args, **kwargs: None)

    nodeid = (
        "bridge/tests/test_realtime_voice.py::"
        "test_stale_audio_without_response_id_after_barge_in_is_dropped"
    )
    state: dict = {}

    development._record_attempt(
        30,
        5,
        state,
        number=1,
        stage="semantic_review",
        outcome="repair_required",
        summary="repair required",
        feedback=f"Failure: {nodeid}",
    )

    assert nodeid in state["last_feedback"]
    assert nodeid in state["attempts"][0]["feedback"]
    assert saved


def test_v25_empty_proposal_retries_without_spending_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_step(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"edits": []}, {"tokens": 1}
        return {
            "edits": [
                {
                    "path": "bridge/app/example.py",
                    "old_text": "a",
                    "new_text": "b",
                }
            ]
        }, {"tokens": 2}

    monkeypatch.setattr(development, "_development_step", fake_step)
    monkeypatch.setattr(
        development,
        "_apply_incremental_edits",
        lambda *args, **kwargs: (
            "patch",
            ["bridge/app/example.py"],
            "hash",
        ),
    )
    monkeypatch.setattr(development, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(base, "audit", lambda *args, **kwargs: None)

    state = {"attempts": [], "step_retries": []}

    result = development._apply_development_step_with_retries(
        candidate_id=30,
        attempt_number=1,
        failure={},
        state=state,
        workspace=Path("."),
        policy={},
        config=config(),
        env_values={"JARVIS_DEVELOPMENT_STEP_RETRIES": "3"},
        feedback="semantic repair",
    )

    assert calls["count"] == 2
    assert state["attempts"] == []
    assert len(state["step_retries"]) == 1
    assert state["step_retries"][0]["retry"] == 1
    assert result[2] == "patch"


def test_v25_feedback_redaction_preserves_test_identifiers() -> None:
    nodeid = (
        "bridge/tests/test_realtime_voice.py::"
        "test_stale_audio_without_response_id_after_barge_in_is_dropped"
    )
    cleaned = development._clean_development_feedback(
        f"{nodeid} API_KEY=supersecretcredentialvalue"
    )

    assert nodeid in cleaned
    assert "supersecretcredentialvalue" not in cleaned
    assert "[REDACTED]" in cleaned


def test_v25_detects_external_credit_exhaustion() -> None:
    assert development._is_external_credit_block(
        "Error: credit_balance_exhausted"
    )
    assert development._is_external_credit_block(
        "OpenAI: insufficient_quota"
    )
    assert not development._is_external_credit_block(
        "ordinary validation failure"
    )


def test_v25_external_block_does_not_consume_internal_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "attempts": [{"number": 4}],
        "step_retries": [],
    }

    def blocked(*args):
        raise development.ExternalDependencyBlocked(
            "credit_balance_exhausted"
        )

    monkeypatch.setattr(development, "_development_step", blocked)

    with pytest.raises(development.ExternalDependencyBlocked):
        development._apply_development_step_with_retries(
            candidate_id=30,
            attempt_number=5,
            failure={},
            state=state,
            workspace=Path("/tmp"),
            policy={},
            config=object(),
            env_values={"JARVIS_DEVELOPMENT_STEP_RETRIES": "4"},
            feedback="validation failure",
        )

    assert state["step_retries"] == []
    assert len(state["attempts"]) == 1


def test_v25_external_pause_preserves_genuine_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved = []
    state = {
        "attempts": [
            {"number": 1},
            {"number": 2},
            {"number": 3},
            {"number": 4},
        ]
    }

    monkeypatch.setattr(
        development,
        "_save_state",
        lambda cid, st, **fields: saved.append(
            {"cid": cid, "state": dict(st), **fields}
        ),
    )
    monkeypatch.setattr(base, "audit", lambda *args, **kwargs: None)

    development._pause_external_dependency(
        candidate_id=30,
        failure_id=9,
        state=state,
        workspace=tmp_path,
        exc="credit_balance_exhausted",
    )

    assert len(state["attempts"]) == 4
    assert state["phase"] == "blocked_external"
    assert saved[-1]["status"] == "blocked_external"


def test_v25_resumes_persisted_retry_number_for_current_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    feedback_seen = []

    state = {
        "attempts": [
            {
                "number": 6,
                "stage": "semantic_review",
                "outcome": "repair_required",
            }
        ],
        "step_retries": [
            {
                "attempt": 5,
                "retry": 4,
                "error": "old attempt five error",
            },
            {
                "attempt": 7,
                "retry": 1,
                "error": "retry-one-error",
            },
            {
                "attempt": 7,
                "retry": 2,
                "error": "retry-two-error",
            },
            {
                "attempt": 7,
                "retry": 3,
                "error": "retry-three-error",
            },
        ],
    }

    def fake_step(*args):
        calls.append(1)
        feedback_seen.append(str(args[-1]))
        return (
            {
                "edits": [
                    {
                        "path": "bridge/app/example.py",
                        "old_text": "old",
                        "new_text": "new",
                    }
                ]
            },
            {"response_id": "retry-four-valid"},
        )

    monkeypatch.setattr(development, "_development_step", fake_step)
    monkeypatch.setattr(
        development,
        "_apply_incremental_edits",
        lambda *args, **kwargs: (
            "patch",
            ["bridge/app/example.py"],
            "hash-v25",
        ),
    )
    monkeypatch.setattr(development, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(base, "audit", lambda *args, **kwargs: None)

    development._apply_development_step_with_retries(
        candidate_id=30,
        attempt_number=7,
        failure={},
        state=state,
        workspace=Path("/tmp"),
        policy={},
        config=object(),
        env_values={"JARVIS_DEVELOPMENT_STEP_RETRIES": "4"},
        feedback="semantic review failure",
    )

    assert len(calls) == 1
    assert "semantic review failure" in feedback_seen[0]
    assert "retry-three-error" in feedback_seen[0]
    assert "old attempt five error" not in feedback_seen[0]


def test_v25_exhausted_persisted_retry_budget_never_calls_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    state = {
        "attempts": [
            {
                "number": 6,
                "stage": "semantic_review",
                "outcome": "repair_required",
            }
        ],
        "step_retries": [
            {"attempt": 4, "retry": 1, "error": "older attempt"},
            {"attempt": 7, "retry": 1, "error": "retry-one-error"},
            {"attempt": 7, "retry": 2, "error": "retry-two-error"},
            {"attempt": 7, "retry": 3, "error": "retry-three-error"},
            {"attempt": 7, "retry": 4, "error": "retry-four-error"},
        ],
    }

    def must_not_run(*args):
        calls.append(1)
        raise AssertionError("model call must not happen")

    monkeypatch.setattr(development, "_development_step", must_not_run)

    with pytest.raises(
        base.WorkerError,
        match="after 4 internal retries",
    ):
        development._apply_development_step_with_retries(
            candidate_id=30,
            attempt_number=7,
            failure={},
            state=state,
            workspace=Path("/tmp"),
            policy={},
            config=object(),
            env_values={"JARVIS_DEVELOPMENT_STEP_RETRIES": "4"},
            feedback="semantic review failure",
        )

    assert calls == []


def test_v25_junit_parser_retains_bounded_redacted_failure_evidence(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.xml"
    secret = "sk-proj-this-must-never-reach-repair-feedback"

    report.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="1" failures="1">
  <testcase
    classname="bridge.tests.test_voice"
    name="test_interrupt"
  >
    <failure message="AssertionError">
AssertionError: expected cancellation before audio
OPENAI_API_KEY={secret}
    </failure>
  </testcase>
</testsuite>
"""
    )

    failures, tests, total, details = base._load_pytest_junit(
        report
    )

    assert total == 1
    assert sum(failures.values()) == 1
    assert sum(tests.values()) == 1

    identity = next(iter(failures))
    evidence = "\n".join(details[identity])

    assert "test_interrupt" in identity
    assert "expected cancellation before audio" in evidence
    assert secret not in evidence
    assert len(evidence) <= 6000


def test_v25_pytest_baseline_attaches_evidence_only_to_new_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {
        "ok": True,
        "returncode": 1,
        "total_tests": 10,
        "failures": base.Counter(
            {"existing::failure": 1}
        ),
        "tests": base.Counter(
            {
                "existing::failure": 1,
                "new::failure": 1,
            }
        ),
        "failure_details": {
            "existing::failure": [
                "existing failure evidence"
            ]
        },
        "output": "",
    }
    candidate = {
        "ok": True,
        "returncode": 1,
        "total_tests": 10,
        "failures": base.Counter(
            {
                "existing::failure": 1,
                "new::failure": 1,
            }
        ),
        "tests": base.Counter(
            {
                "existing::failure": 1,
                "new::failure": 1,
            }
        ),
        "failure_details": {
            "existing::failure": [
                "existing failure evidence"
            ],
            "new::failure": [
                "exact new assertion evidence"
            ],
        },
        "output": "",
    }

    scans = iter(
        [baseline, candidate]
    )
    monkeypatch.setattr(
        base,
        "_docker_pytest_scan",
        lambda *args, **kwargs: next(scans),
    )

    result = base.pytest_baseline_result(
        Path("/tmp"),
        1,
    )

    assert result["passed"] is False
    assert result["new_failures_count"] == 1
    assert result["new_failures"] == [
        {
            "test": "new::failure",
            "count": 1,
            "evidence": "exact new assertion evidence",
        }
    ]
