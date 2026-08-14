from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "self_improvement_worker",
    ROOT / "tools" / "self_improvement_worker.py",
)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = worker
SPEC.loader.exec_module(worker)


def config() -> object:
    return worker.WorkerConfig(
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
        base_branch="main",
    )


def policy() -> dict[str, object]:
    return {
        "allowed_edit_paths": ["bridge/app/*.py", "bridge/tests/*.py"],
        "forbidden_paths": [".env", "tools/**", "docker-compose.yml"],
        "forbidden_added_patterns": [r"shell\s*=\s*True", r"\beval\s*\("],
    }


def test_valid_bounded_patch_is_accepted() -> None:
    patch = """diff --git a/bridge/app/example.py b/bridge/app/example.py
--- a/bridge/app/example.py
+++ b/bridge/app/example.py
@@ -1 +1 @@
-old = 1
+new = 2
"""
    paths, digest = worker.validate_patch_policy(patch, policy(), config())
    assert paths == ["bridge/app/example.py"]
    assert len(digest) == 64


def test_env_patch_is_rejected() -> None:
    patch = """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1 +1 @@
-A=1
+A=2
"""
    with pytest.raises(worker.WorkerError):
        worker.validate_patch_policy(patch, policy(), config())


def test_dangerous_added_code_is_rejected() -> None:
    patch = """diff --git a/bridge/app/example.py b/bridge/app/example.py
--- a/bridge/app/example.py
+++ b/bridge/app/example.py
@@ -1 +1 @@
-old = 1
+subprocess.run('x', shell=True)
"""
    with pytest.raises(worker.WorkerError):
        worker.validate_patch_policy(patch, policy(), config())

def _initialise_attempt_cap_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = (
        tmp_path
        / "data"
    )

    monkeypatch.setattr(
        worker,
        "DATA_DIR",
        data_dir,
    )

    monkeypatch.setattr(
        worker,
        "DB_PATH",
        (
            data_dir
            / "improvement.db"
        ),
    )

    with worker.connect() as connection:
        connection.execute(
            """
            CREATE TABLE improvement_candidates (
                candidate_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE improvement_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT,
                failure_id INTEGER,
                candidate_id INTEGER,
                details_json TEXT
            )
            """
        )


def test_attempts_today_counts_previous_day_queue_generated_today(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _initialise_attempt_cap_db(
        monkeypatch,
        tmp_path,
    )

    now = worker.datetime.now(
        worker.timezone.utc
    )

    today = (
        now.date().isoformat()
    )

    yesterday = (
        (
            now
            - worker.timedelta(
                days=1
            )
        )
        .date()
        .isoformat()
    )

    with worker.connect() as connection:
        connection.execute(
            """
            INSERT INTO improvement_candidates (
                candidate_id,
                created_at,
                status
            ) VALUES (?, ?, ?)
            """,
            (
                11,
                yesterday
                + "T23:50:00+00:00",
                "generating",
            ),
        )

        connection.execute(
            """
            INSERT INTO improvement_audit (
                created_at,
                event_type,
                actor,
                failure_id,
                candidate_id,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                today
                + "T00:05:00+00:00",
                "candidate_generation_started",
                "worker",
                99,
                11,
                "{}",
            ),
        )

    assert (
        worker.attempts_today()
        == 1
    )


def test_attempts_today_does_not_count_candidate_state_without_start_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _initialise_attempt_cap_db(
        monkeypatch,
        tmp_path,
    )

    today = (
        worker.datetime.now(
            worker.timezone.utc
        )
        .date()
        .isoformat()
    )

    with worker.connect() as connection:
        connection.execute(
            """
            INSERT INTO improvement_candidates (
                candidate_id,
                created_at,
                status
            ) VALUES (?, ?, ?)
            """,
            (
                12,
                today
                + "T00:10:00+00:00",
                "generating",
            ),
        )

    assert (
        worker.attempts_today()
        == 0
    )


def test_attempts_today_counts_only_today_generation_start_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _initialise_attempt_cap_db(
        monkeypatch,
        tmp_path,
    )

    now = worker.datetime.now(
        worker.timezone.utc
    )

    today = (
        now.date().isoformat()
    )

    yesterday = (
        (
            now
            - worker.timedelta(
                days=1
            )
        )
        .date()
        .isoformat()
    )

    rows = [
        (
            today
            + "T00:01:00+00:00",
            "candidate_generation_started",
            11,
        ),
        (
            today
            + "T12:00:00+00:00",
            "candidate_generation_started",
            12,
        ),
        (
            today
            + "T12:01:00+00:00",
            "candidate_failed",
            12,
        ),
        (
            yesterday
            + "T23:59:59+00:00",
            "candidate_generation_started",
            10,
        ),
    ]

    with worker.connect() as connection:
        for (
            created_at,
            event_type,
            candidate_id,
        ) in rows:
            connection.execute(
                """
                INSERT INTO improvement_audit (
                    created_at,
                    event_type,
                    actor,
                    failure_id,
                    candidate_id,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    event_type,
                    "worker",
                    99,
                    candidate_id,
                    "{}",
                ),
            )

    assert (
        worker.attempts_today()
        == 2
    )


def test_proposal_only_blocks_direct_deploy() -> None:
    candidate = {
        "candidate_id": 1,
        "failure_id": 1,
    }

    with pytest.raises(
        worker.WorkerError,
        match="Proposal Mode",
    ):
        worker.deploy_candidate(
            candidate,
            config(),
            {},
        )


def test_proposal_only_blocks_direct_rollback() -> None:
    candidate = {
        "candidate_id": 1,
        "failure_id": 1,
    }

    with pytest.raises(
        worker.WorkerError,
        match="Proposal Mode",
    ):
        worker.rollback_candidate(
            candidate,
            config(),
            {},
        )


def test_proposal_only_skips_live_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config()

    processed: list[int] = []

    monkeypatch.setattr(
        worker,
        "update_setting",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        worker,
        "improvement_enabled",
        lambda: True,
    )

    def fake_fetch(
        statuses: tuple[str, ...],
    ) -> dict[str, object] | None:
        if statuses == ("queued",):
            return {
                "candidate_id": 3,
                "failure_id": 3,
            }

        if statuses == ("deploy_requested",):
            pytest.fail(
                "Proposal Mode queried deployment"
            )

        if statuses == ("rollback_requested",):
            pytest.fail(
                "Proposal Mode queried rollback"
            )

        return None

    monkeypatch.setattr(
        worker,
        "fetch_candidate",
        fake_fetch,
    )

    monkeypatch.setattr(
        worker,
        "process_queued_candidate",
        lambda candidate, *args: processed.append(
            int(candidate["candidate_id"])
        ),
    )

    assert worker.run_once(
        cfg,
        {},
    ) is True

    assert processed == [3]

def _bandit_issue(
    *,
    filename: str = "bridge/app/example.py",
    test_id: str = "B101",
    issue_text: str = "Use of assert detected.",
    severity: str = "LOW",
    confidence: str = "HIGH",
    line_number: int = 10,
) -> dict[str, object]:
    return {
        "filename": filename,
        "test_id": test_id,
        "issue_text": issue_text,
        "issue_severity": severity,
        "issue_confidence": confidence,
        "line_number": line_number,
    }


def _bandit_completed(
    findings: list[dict[str, object]],
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["bandit"],
        returncode=1 if findings else 0,
        stdout=json.dumps(
            {
                "results": findings,
            }
        ),
    )


def test_bandit_baseline_allows_existing_findings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = [
        _bandit_issue(
            line_number=10,
        ),
    ]

    candidate = [
        _bandit_issue(
            line_number=200,
        ),
    ]

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del command
        del timeout
        del check
        del env

        if Path(cwd) == worker.ROOT:
            return _bandit_completed(
                baseline
            )

        return _bandit_completed(
            candidate
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    result = worker.bandit_baseline_result(
        tmp_path
    )

    assert result["passed"] is True
    assert result["blocking"] is True
    assert result["baseline_findings"] == 1
    assert result["candidate_findings"] == 1
    assert result["new_findings_count"] == 0


def test_bandit_baseline_blocks_new_finding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = [
        _bandit_issue(
            line_number=10,
        ),
    ]

    candidate = [
        _bandit_issue(
            line_number=200,
        ),
        _bandit_issue(
            line_number=240,
        ),
    ]

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del command
        del timeout
        del check
        del env

        if Path(cwd) == worker.ROOT:
            return _bandit_completed(
                baseline
            )

        return _bandit_completed(
            candidate
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    result = worker.bandit_baseline_result(
        tmp_path
    )

    assert result["passed"] is False
    assert result["blocking"] is True
    assert result["baseline_findings"] == 1
    assert result["candidate_findings"] == 2
    assert result["new_findings_count"] == 1
    assert len(
        result["new_findings"]
    ) == 1


def test_bandit_baseline_blocks_unparseable_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del command
        del cwd
        del timeout
        del check
        del env

        return subprocess.CompletedProcess(
            args=["bandit"],
            returncode=2,
            stdout="Bandit execution failure",
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    result = worker.bandit_baseline_result(
        tmp_path
    )

    assert result["passed"] is False
    assert result["blocking"] is True

def _pytest_scan(
    failures: dict[str, int],
    *,
    total: int,
    tests: dict[str, int] | None = None,
    ok: bool = True,
    returncode: int = 1,
) -> dict[str, object]:
    if tests is None:
        tests = dict(
            failures
        )

    return {
        "ok": ok,
        "stage": "complete",
        "returncode": returncode,
        "total_tests": total,
        "failures": worker.Counter(
            failures
        ),
        "tests": worker.Counter(
            tests
        ),
        "output": "",
    }


def test_pytest_baseline_allows_existing_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_scan(
        workspace: Path,
        *,
        label: str,
        timeout: int,
    ) -> dict[str, object]:
        del workspace
        del timeout

        assert label in {
            "baseline",
            "candidate",
        }

        return _pytest_scan(
            {
                "bridge.tests.test_old::test_known": 1,
            },
            total=255,
        )

    monkeypatch.setattr(
        worker,
        "_docker_pytest_scan",
        fake_scan,
    )

    result = worker.pytest_baseline_result(
        tmp_path,
        60,
    )

    assert result["passed"] is True
    assert result["blocking"] is True
    assert result["baseline_failures"] == 1
    assert result["candidate_failures"] == 1
    assert result["new_failures_count"] == 0


def test_pytest_baseline_blocks_new_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_scan(
        workspace: Path,
        *,
        label: str,
        timeout: int,
    ) -> dict[str, object]:
        del workspace
        del timeout

        if label == "baseline":
            return _pytest_scan(
                {
                    "bridge.tests.test_old::test_known": 1,
                },
                total=255,
            )

        return _pytest_scan(
            {
                "bridge.tests.test_old::test_known": 1,
                "bridge.tests.test_new::test_regression": 1,
            },
            total=256,
        )

    monkeypatch.setattr(
        worker,
        "_docker_pytest_scan",
        fake_scan,
    )

    result = worker.pytest_baseline_result(
        tmp_path,
        60,
    )

    assert result["passed"] is False
    assert result["blocking"] is True
    assert result["baseline_failures"] == 1
    assert result["candidate_failures"] == 2
    assert result["new_failures_count"] == 1
    assert result["new_failures"] == [
        {
            "test": "bridge.tests.test_new::test_regression",
            "count": 1,
        }
    ]


def test_pytest_baseline_fails_closed_on_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_scan(
        workspace: Path,
        *,
        label: str,
        timeout: int,
    ) -> dict[str, object]:
        del workspace
        del label
        del timeout

        return {
            "ok": False,
            "stage": "build",
            "returncode": 2,
            "total_tests": None,
            "failures": worker.Counter(),
            "output": "container failure",
        }

    monkeypatch.setattr(
        worker,
        "_docker_pytest_scan",
        fake_scan,
    )

    result = worker.pytest_baseline_result(
        tmp_path,
        60,
    )

    assert result["passed"] is False
    assert result["blocking"] is True
    assert result["new_failures_count"] is None


def test_run_validation_uses_baseline_pytest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    monkeypatch.setattr(
        worker,
        "pytest_baseline_result",
        lambda workspace, timeout: {
            "name": "pytest_baseline",
            "passed": True,
            "blocking": True,
            "returncode": 0,
            "new_failures_count": 0,
            "output": "",
        },
    )

    monkeypatch.setattr(
        worker,
        "bandit_baseline_result",
        lambda workspace: {
            "name": "bandit_baseline",
            "passed": True,
            "blocking": True,
            "returncode": 0,
            "new_findings_count": 0,
            "output": "",
        },
    )

    monkeypatch.setattr(
        worker,
        "security_diff_scan",
        lambda workspace, policy: {
            "passed": True,
            "findings": [],
        },
    )

    results, security = worker.run_validation(
        tmp_path,
        policy(),
        config(),
    )

    names = [
        item["name"]
        for item in results[
            "checks"
        ]
    ]

    assert "pytest_baseline" in names
    assert "pytest" not in names
    assert results["passed"] is True
    assert security["passed"] is True


def test_docker_smoke_uses_writable_mounts_and_keeps_container_for_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[
        list[str]
    ] = []

    monkeypatch.setattr(
        worker,
        "WORK_ROOT",
        tmp_path,
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        commands.append(
            list(command)
        )

        if (
            command[:2]
            == [
                "docker",
                "build",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="built",
            )

        if (
            command[:3]
            == [
                "docker",
                "run",
                "-d",
            ]
        ):
            assert "--rm" not in command

            mounts = [
                command[index + 1]
                for index, item in enumerate(
                    command
                )
                if item == "-v"
            ]

            for mount in mounts:
                source = Path(
                    mount.split(
                        ":",
                        1,
                    )[0]
                )

                assert (
                    source.stat().st_mode
                    & 0o777
                ) == 0o777

            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="container-id",
            )

        if (
            command[:2]
            == [
                "docker",
                "inspect",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="true 0",
            )

        if (
            command[:2]
            == [
                "docker",
                "exec",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=(
                    '{"status":"healthy",'
                    '"service":"Jarvis Core"}'
                ),
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    result = worker.docker_smoke_test(
        tmp_path,
        123,
        30,
    )

    assert result["passed"] is True
    assert result["stage"] == "health"

    assert any(
        command[:4]
        == [
            "docker",
            "rm",
            "-f",
            "jarvis-candidate-123",
        ]
        for command in commands
    )

    assert any(
        command[:4]
        == [
            "docker",
            "image",
            "rm",
            "-f",
        ]
        for command in commands
    )


def test_docker_smoke_preserves_startup_logs_for_exited_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        worker,
        "WORK_ROOT",
        tmp_path,
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        if (
            command[:2]
            == [
                "docker",
                "build",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="built",
            )

        if (
            command[:3]
            == [
                "docker",
                "run",
                "-d",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="container-id",
            )

        if (
            command[:2]
            == [
                "docker",
                "inspect",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="false 1",
            )

        if (
            command[:2]
            == [
                "docker",
                "logs",
            ]
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=(
                    "sqlite3.OperationalError: "
                    "unable to open database file"
                ),
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    result = worker.docker_smoke_test(
        tmp_path,
        124,
        30,
    )

    assert result["passed"] is False
    assert result["stage"] == "startup"

    assert (
        "unable to open database file"
        in result["output"]
    )

def test_response_completion_metadata_includes_reasoning_tokens() -> None:
    class OutputDetails:
        reasoning_tokens = 321

    class Usage:
        input_tokens = 100
        output_tokens = 500
        output_tokens_details = OutputDetails()

    class Response:
        status = "completed"
        incomplete_details = None
        usage = Usage()
        id = "resp_test_completed"

    metadata = worker._response_completion_metadata(
        Response()
    )

    assert metadata == {
        "response_status": "completed",
        "incomplete_reason": "",
        "input_tokens": 100,
        "output_tokens": 500,
        "reasoning_tokens": 321,
        "response_id": "resp_test_completed",
    }


def test_require_completed_response_rejects_token_incomplete() -> None:
    class Incomplete:
        reason = "max_tokens"

    class OutputDetails:
        reasoning_tokens = 15900

    class Usage:
        input_tokens = 1000
        output_tokens = 16000
        output_tokens_details = OutputDetails()

    class Response:
        status = "incomplete"
        incomplete_details = Incomplete()
        usage = Usage()
        id = "resp_test_incomplete"

    with pytest.raises(
        worker.WorkerError,
        match="max_tokens",
    ):
        worker._require_completed_response(
            Response(),
            purpose="Patch generation",
        )


def test_generation_and_review_have_completion_guards() -> None:
    import inspect

    request_source = inspect.getsource(
        worker.request_patch
    )

    review_source = inspect.getsource(
        worker.request_independent_review
    )

    assert '"max_output_tokens": 32000' in request_source

    assert (
        'kwargs["reasoning"] = {"effort": "medium"}'
        in request_source
    )

    assert (
        'purpose="Patch generation"'
        in request_source
    )

    assert (
        "_require_completed_response("
        in request_source
    )

    assert '"max_output_tokens": 16000' in review_source

    assert (
        'purpose="Independent review"'
        in review_source
    )

    assert (
        "_require_completed_response("
        in review_source
    )

def _make_minimal_pytest_workspace(
    root: Path,
) -> Path:
    workspace = (
        root
        / "repo"
    )

    (
        workspace
        / "bridge"
        / "app"
    ).mkdir(
        parents=True
    )

    (
        workspace
        / "bridge"
        / "tests"
    ).mkdir(
        parents=True
    )

    (
        workspace
        / "config"
    ).mkdir(
        parents=True
    )

    (
        workspace
        / "tools"
    ).mkdir(
        parents=True
    )

    (
        workspace
        / "bridge"
        / "requirements.txt"
    ).write_text(
        "pytest\n",
        encoding="utf-8",
    )

    (
        workspace
        / "bridge"
        / "app"
        / "example.py"
    ).write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    (
        workspace
        / "bridge"
        / "tests"
        / "test_example.py"
    ).write_text(
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    (
        workspace
        / "config"
        / "policy.json"
    ).write_text(
        "{}\n",
        encoding="utf-8",
    )

    (
        workspace
        / "tools"
        / "helper.py"
    ).write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    (
        workspace
        / "requirements-improver.txt"
    ).write_text(
        "pytest\n",
        encoding="utf-8",
    )

    return workspace


def test_pytest_build_context_excludes_private_repository_paths(
    tmp_path: Path,
) -> None:
    workspace = (
        _make_minimal_pytest_workspace(
            tmp_path
        )
    )

    (
        workspace
        / ".env"
    ).write_text(
        "SECRET=do-not-copy\n",
        encoding="utf-8",
    )

    (
        workspace
        / "data"
    ).mkdir()

    (
        workspace
        / "data"
        / "secret.db"
    ).write_text(
        "private",
        encoding="utf-8",
    )

    (
        workspace
        / ".git"
    ).mkdir()

    (
        workspace
        / ".git"
        / "config"
    ).write_text(
        "private",
        encoding="utf-8",
    )

    (
        workspace
        / "backup"
    ).mkdir()

    (
        workspace
        / "backup"
        / "secret.txt"
    ).write_text(
        "private",
        encoding="utf-8",
    )

    destination = (
        tmp_path
        / "safe-context"
    )

    worker._prepare_pytest_build_context(
        workspace,
        destination,
    )

    assert (
        destination
        / "bridge"
        / "app"
        / "example.py"
    ).is_file()

    assert (
        destination
        / "config"
        / "policy.json"
    ).is_file()

    assert (
        destination
        / "tools"
        / "helper.py"
    ).is_file()

    assert (
        destination
        / "requirements-improver.txt"
    ).is_file()

    for forbidden in (
        ".env",
        "data",
        ".git",
        "backup",
        ".jarvis-improver",
        ".venv",
        ".venv-improver",
    ):
        assert not (
            destination
            / forbidden
        ).exists()


def test_pytest_build_context_rejects_symlink(
    tmp_path: Path,
) -> None:
    workspace = (
        _make_minimal_pytest_workspace(
            tmp_path
        )
    )

    outside = (
        tmp_path
        / "outside.py"
    )

    outside.write_text(
        "SECRET = 1\n",
        encoding="utf-8",
    )

    link = (
        workspace
        / "bridge"
        / "app"
        / "escape.py"
    )

    link.symlink_to(
        outside
    )

    with pytest.raises(
        worker.WorkerError,
        match="symlink",
    ):
        worker._prepare_pytest_build_context(
            workspace,
            tmp_path
            / "unsafe-context",
        )


def test_pytest_baseline_blocks_missing_existing_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_scan(
        workspace: Path,
        *,
        label: str,
        timeout: int,
    ) -> dict[str, object]:
        del workspace
        del timeout

        if label == "baseline":
            return _pytest_scan(
                {},
                total=2,
                tests={
                    "suite::test_one": 1,
                    "suite::test_two": 1,
                },
                returncode=0,
            )

        return _pytest_scan(
            {},
            total=1,
            tests={
                "suite::test_two": 1,
            },
            returncode=0,
        )

    monkeypatch.setattr(
        worker,
        "_docker_pytest_scan",
        fake_scan,
    )

    result = worker.pytest_baseline_result(
        tmp_path,
        60,
    )

    assert result["passed"] is False
    assert result["new_failures_count"] == 0
    assert result["missing_tests_count"] == 1
    assert result["missing_tests"] == [
        {
            "test": "suite::test_one",
            "count": 1,
        }
    ]


def test_pytest_baseline_allows_added_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_scan(
        workspace: Path,
        *,
        label: str,
        timeout: int,
    ) -> dict[str, object]:
        del workspace
        del timeout

        if label == "baseline":
            return _pytest_scan(
                {},
                total=1,
                tests={
                    "suite::test_existing": 1,
                },
                returncode=0,
            )

        return _pytest_scan(
            {},
            total=2,
            tests={
                "suite::test_existing": 1,
                "suite::test_new": 1,
            },
            returncode=0,
        )

    monkeypatch.setattr(
        worker,
        "_docker_pytest_scan",
        fake_scan,
    )

    result = worker.pytest_baseline_result(
        tmp_path,
        60,
    )

    assert result["passed"] is True
    assert result["missing_tests_count"] == 0
    assert result["added_tests_count"] == 1


def test_worker_transaction_schema_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sqlite3

    database = (
        tmp_path
        / "improvement.db"
    )

    monkeypatch.setattr(
        worker,
        "DATA_DIR",
        tmp_path,
    )

    monkeypatch.setattr(
        worker,
        "DB_PATH",
        database,
    )

    with sqlite3.connect(
        database
    ) as connection:
        connection.execute(
            """
            CREATE TABLE improvement_candidates (
                candidate_id INTEGER PRIMARY KEY
            )
            """
        )

    worker.ensure_candidate_transaction_columns()

    with sqlite3.connect(
        database
    ) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(improvement_candidates)"
            ).fetchall()
        }

    assert set(
        worker.TRANSACTION_COLUMNS
    ).issubset(
        columns
    )


def test_candidate_diff_hash_is_stable(
    tmp_path: Path,
) -> None:
    repo = (
        tmp_path
        / "repo"
    )

    repo.mkdir()

    def git(
        *args: str,
    ) -> str:
        completed = subprocess.run(
            [
                "git",
                *args,
            ],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

        return completed.stdout.strip()

    git(
        "init",
        "-q",
    )

    git(
        "config",
        "user.email",
        "jarvis-test@example.invalid",
    )

    git(
        "config",
        "user.name",
        "Jarvis Test",
    )

    file = (
        repo
        / "example.py"
    )

    file.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    git(
        "add",
        "example.py",
    )

    git(
        "commit",
        "-q",
        "-m",
        "base",
    )

    base = git(
        "rev-parse",
        "HEAD",
    )

    file.write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )

    git(
        "add",
        "example.py",
    )

    git(
        "commit",
        "-q",
        "-m",
        "candidate",
    )

    candidate = git(
        "rev-parse",
        "HEAD",
    )

    first = worker.candidate_diff_sha256(
        base,
        candidate,
        cwd=repo,
    )

    second = worker.candidate_diff_sha256(
        base,
        candidate,
        cwd=repo,
    )

    assert first == second
    assert len(first) == 64


def test_exact_candidate_binding_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40
    digest = "c" * 64
    candidate_id = 7

    worktrees = (
        tmp_path
        / "worktrees"
    )

    workspace = (
        worktrees
        / str(candidate_id)
    )

    workspace.mkdir(
        parents=True
    )

    monkeypatch.setattr(
        worker,
        "WORKTREES",
        worktrees,
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "candidate_diff_sha256",
        lambda *args, **kwargs: digest,
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        del check
        del env

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            output = (
                candidate_commit
                if Path(cwd) == workspace
                else base
            )

        elif command == [
            "git",
            "rev-parse",
            "--verify",
            f"jarvis/improvement-{candidate_id}",
        ]:
            output = candidate_commit

        elif command == [
            "git",
            "merge-base",
            base,
            candidate_commit,
        ]:
            output = base

        else:
            pytest.fail(
                f"Unexpected Git command: {command}"
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=output,
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    result = worker.verify_candidate_deploy_binding(
        {
            "candidate_id": candidate_id,
            "status": "deploy_requested",
            "branch_name": (
                f"jarvis/improvement-{candidate_id}"
            ),
            "workspace_path": str(
                workspace
            ),
            "base_commit": base,
            "candidate_commit": candidate_commit,
            "validated_patch_sha256": digest,
        }
    )

    assert result["base_commit"] == base
    assert (
        result["candidate_commit"]
        == candidate_commit
    )
    assert (
        result["validated_patch_sha256"]
        == digest
    )


def test_exact_candidate_binding_rejects_moved_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40
    moved = "d" * 40
    digest = "c" * 64
    candidate_id = 8

    worktrees = (
        tmp_path
        / "worktrees"
    )

    workspace = (
        worktrees
        / str(candidate_id)
    )

    workspace.mkdir(
        parents=True
    )

    monkeypatch.setattr(
        worker,
        "WORKTREES",
        worktrees,
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            output = base

        elif command == [
            "git",
            "rev-parse",
            "--verify",
            f"jarvis/improvement-{candidate_id}",
        ]:
            output = moved

        else:
            pytest.fail(
                f"Unexpected Git command: {command}"
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=output,
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    with pytest.raises(
        worker.WorkerError,
        match="branch moved",
    ):
        worker.verify_candidate_deploy_binding(
            {
                "candidate_id": candidate_id,
                "status": "deploy_requested",
                "branch_name": (
                    f"jarvis/improvement-{candidate_id}"
                ),
                "workspace_path": str(
                    workspace
                ),
                "base_commit": base,
                "candidate_commit": candidate_commit,
                "validated_patch_sha256": digest,
            }
        )


def test_candidate_generation_requires_transactional_approval() -> None:
    import inspect

    source = inspect.getsource(
        worker.process_queued_candidate
    )

    assert (
        "auto_deploy_low_risk"
        not in source
    )

    assert (
        'next_status = "awaiting_approval"'
        in source
    )

    assert (
        "base_commit=base_commit"
        in source
    )

    assert (
        "candidate_commit=commit_sha"
        in source
    )

    assert (
        "validated_patch_sha256=validated_patch_hash"
        in source
    )

    assert (
        "approval_code_expires_at="
        "approval_code_expires_at"
        in source
    )

    assert (
        "ready for you to review"
        in source
    )
    assert (
        "Nothing has been installed yet."
        in source
    )


def _create_lease_test_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    status: str,
    base_commit: str,
    candidate_commit: str,
    lease_expires_at: str | None = None,
    phase: str | None = None,
) -> Path:
    import sqlite3

    database = (
        tmp_path
        / "lease-test.db"
    )

    monkeypatch.setattr(
        worker,
        "DATA_DIR",
        tmp_path,
    )

    monkeypatch.setattr(
        worker,
        "DB_PATH",
        database,
    )

    with sqlite3.connect(
        database
    ) as connection:
        connection.execute(
            """
            CREATE TABLE improvement_candidates (
                candidate_id INTEGER PRIMARY KEY,
                failure_id INTEGER NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                status TEXT,
                base_commit TEXT,
                candidate_commit TEXT,
                validated_patch_sha256 TEXT,
                rollback_ref TEXT,
                error TEXT,
                deployed_at TEXT,
                rolled_back_at TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE improvement_failures (
                failure_id INTEGER PRIMARY KEY,
                status TEXT,
                updated_at TEXT
            )
            """
        )

        connection.execute(
            """
            INSERT INTO improvement_failures (
                failure_id,
                status,
                updated_at
            )
            VALUES (?, ?, ?)
            """,
            (
                1,
                "candidate_ready",
                worker.utc_now(),
            ),
        )

        connection.execute(
            """
            INSERT INTO improvement_candidates (
                candidate_id,
                failure_id,
                created_at,
                updated_at,
                status,
                base_commit,
                candidate_commit,
                validated_patch_sha256,
                rollback_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                1,
                worker.utc_now(),
                worker.utc_now(),
                status,
                base_commit,
                candidate_commit,
                "c" * 64,
                base_commit,
            ),
        )

    worker.ensure_candidate_transaction_columns()

    effective_phase = phase

    if (
        effective_phase is None
        and status == "deploying"
    ):
        effective_phase = "claimed"

    if (
        lease_expires_at is not None
        or effective_phase is not None
    ):
        with worker.connect() as connection:
            connection.execute(
                """
                UPDATE improvement_candidates
                SET
                    deploy_lease_expires_at = COALESCE(
                        ?,
                        deploy_lease_expires_at
                    ),
                    deploy_phase = COALESCE(
                        ?,
                        deploy_phase
                    )
                WHERE candidate_id = 1
                """,
                (
                    lease_expires_at,
                    effective_phase,
                ),
            )

    return database


def test_deployment_claim_is_leased_and_one_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploy_requested",
        base_commit=base,
        candidate_commit=candidate_commit,
    )

    claimed = worker.claim_deployment(
        1
    )

    assert claimed["status"] == "deploying"
    assert claimed["deploy_lease_id"]
    assert claimed["deploy_lease_started_at"]
    assert claimed["deploy_lease_expires_at"]
    assert claimed["deploy_phase"] == "claimed"
    assert claimed["rollback_ref"] == base

    with pytest.raises(
        worker.WorkerError,
        match="not available",
    ):
        worker.claim_deployment(
            1
        )


def test_stale_deployment_before_merge_is_requeued(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base,
            )

        pytest.fail(
            f"Unexpected command: {command}"
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    cfg = config()

    cfg.proposal_only = False

    result = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert result == "requeued"

    recovered = worker.fetch_candidate_by_id(
        1
    )

    assert recovered is not None
    assert (
        recovered["status"]
        == "deploy_requested"
    )
    assert (
        recovered["deploy_phase"]
        == "recovered_requeued"
    )
    assert recovered["deploy_lease_id"] is None


def test_stale_deployment_unknown_head_never_resets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40
    unexpected = "d" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        worker,
        "notify_aaron",
        lambda *args, **kwargs: False,
    )

    commands: list[
        list[str]
    ] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        commands.append(
            command
        )

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=unexpected,
            )

        pytest.fail(
            f"Unexpected command: {command}"
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    cfg = config()

    cfg.proposal_only = False

    result = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert result == "recovery_required"

    assert not any(
        command[
            :3
        ]
        == [
            "git",
            "reset",
            "--hard",
        ]
        for command in commands
    )

    recovered = worker.fetch_candidate_by_id(
        1
    )

    assert recovered is not None
    assert (
        recovered["status"]
        == "recovery_required"
    )
    assert (
        recovered["deploy_phase"]
        == "unexpected_head"
    )


def test_exact_reset_helper_uses_temporary_git_repository(
    tmp_path: Path,
) -> None:
    repo = (
        tmp_path
        / "repo"
    )

    repo.mkdir()

    def git(
        *args: str,
    ) -> str:
        completed = subprocess.run(
            [
                "git",
                *args,
            ],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

        return completed.stdout.strip()

    git(
        "init",
        "-q",
    )

    git(
        "config",
        "user.email",
        "jarvis-test@example.invalid",
    )

    git(
        "config",
        "user.name",
        "Jarvis Test",
    )

    file = (
        repo
        / "value.txt"
    )

    file.write_text(
        "base\n",
        encoding="utf-8",
    )

    git(
        "add",
        "value.txt",
    )

    git(
        "commit",
        "-q",
        "-m",
        "base",
    )

    base = git(
        "rev-parse",
        "HEAD",
    )

    file.write_text(
        "candidate\n",
        encoding="utf-8",
    )

    git(
        "add",
        "value.txt",
    )

    git(
        "commit",
        "-q",
        "-m",
        "candidate",
    )

    candidate = git(
        "rev-parse",
        "HEAD",
    )

    assert candidate != base

    reset = worker.reset_repository_to_ref(
        base,
        repo_root=repo,
    )

    assert reset == base
    assert (
        git(
            "rev-parse",
            "HEAD",
        )
        == base
    )

    assert (
        file.read_text(
            encoding="utf-8"
        )
        == "base\n"
    )


def test_run_once_prioritises_deployment_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config()

    cfg.proposal_only = False

    monkeypatch.setattr(
        worker,
        "update_setting",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        worker,
        "improvement_enabled",
        lambda: True,
    )

    deploying = {
        "candidate_id": 44,
        "failure_id": 9,
        "status": "deploying",
    }

    def fake_fetch(
        statuses: tuple[str, ...],
    ) -> dict[str, object] | None:
        if statuses == (
            "deploying",
        ):
            return deploying

        pytest.fail(
            "run_once continued past interrupted "
            f"deployment: {statuses}"
        )

    monkeypatch.setattr(
        worker,
        "fetch_candidate",
        fake_fetch,
    )

    monkeypatch.setattr(
        worker,
        "deployment_lease_is_expired",
        lambda candidate: True,
    )

    recovered: list[int] = []

    monkeypatch.setattr(
        worker,
        "recover_interrupted_deployment",
        lambda candidate, *args: (
            recovered.append(
                int(
                    candidate[
                        "candidate_id"
                    ]
                )
            )
            or "requeued"
        ),
    )

    assert worker.run_once(
        cfg,
        {},
    ) is True

    assert recovered == [
        44
    ]


def test_v2114b_recovery_claim_preserves_phase_and_fences_old_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
        phase="merging",
    )

    old_lease = "old-lease-token"

    with worker.connect() as connection:
        connection.execute(
            """
            UPDATE improvement_candidates
            SET deploy_lease_id = ?
            WHERE candidate_id = 1
            """,
            (
                old_lease,
            ),
        )

    claimed = worker.claim_stale_deployment_recovery(
        1
    )

    assert claimed is not None
    assert claimed["deploy_phase"] == "merging"

    new_lease = str(
        claimed[
            "deploy_lease_id"
        ]
    )

    assert new_lease
    assert new_lease != old_lease

    with pytest.raises(
        worker.WorkerError,
        match="lease was lost",
    ):
        worker.update_deployment_phase(
            1,
            old_lease,
            "stale_worker_resumed",
        )

    with pytest.raises(
        worker.WorkerError,
        match="lease was lost",
    ):
        worker.transition_deployment_state(
            1,
            old_lease,
            status="deployed",
            phase="deployed",
        )

    worker.update_deployment_phase(
        1,
        new_lease,
        "merging",
    )


@pytest.mark.parametrize(
    "phase",
    (
        "claimed",
        "premerge_verified",
        "merging",
    ),
)
def test_v2114b_premerge_base_requeues_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
        phase=phase,
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base,
            )

        pytest.fail(
            f"Unexpected command: {command}"
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    cfg = config()
    cfg.proposal_only = False

    first = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert first == "requeued"

    recovered = worker.fetch_candidate_by_id(
        1
    )

    assert recovered is not None
    assert recovered["status"] == "deploy_requested"
    assert (
        recovered["deploy_phase"]
        == "recovered_requeued"
    )

    second = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert second == "active"


@pytest.mark.parametrize(
    "phase",
    (
        "merged",
        "rebuilding",
        "verifying",
        "rollback_started",
        "rollback_rebuilding",
        "rollback_verifying",
        "recovery_rolling_back",
        "recovery_rebuilding",
        "recovery_verifying",
    ),
)
def test_v2114b_base_after_postmerge_never_redeploys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
        phase=phase,
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        worker,
        "notify_aaron",
        lambda *args, **kwargs: False,
    )

    monkeypatch.setattr(
        worker,
        "health_check",
        lambda *args, **kwargs: (
            True,
            "healthy",
        ),
    )

    monkeypatch.setattr(
        worker,
        "monitor_logs",
        lambda *args, **kwargs: (
            True,
            "clean",
        ),
    )

    resets: list[str] = []

    monkeypatch.setattr(
        worker,
        "reset_repository_to_ref",
        lambda ref, **kwargs: (
            resets.append(
                ref
            )
            or ref
        ),
    )

    commands: list[
        list[str]
    ] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        commands.append(
            command
        )

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            output = base

        elif command[
            :3
        ] == [
            "docker",
            "compose",
            "up",
        ]:
            output = "rebuilt"

        else:
            pytest.fail(
                f"Unexpected command: {command}"
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=output,
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    cfg = config()
    cfg.proposal_only = False

    result = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert result == "rolled_back"

    recovered = worker.fetch_candidate_by_id(
        1
    )

    assert recovered is not None
    assert recovered["status"] == "rolled_back"
    assert (
        recovered["deploy_phase"]
        == "interrupted_rolled_back"
    )

    # HEAD was already at the exact base. Recovery verifies
    # the base runtime but must not reset or redeploy the
    # failed candidate.
    assert resets == []

    assert sum(
        1
        for command in commands
        if command[
            :3
        ]
        == [
            "docker",
            "compose",
            "up",
        ]
    ) == 1


def test_v2114b_candidate_head_rolls_back_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
        phase="verifying",
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        worker,
        "notify_aaron",
        lambda *args, **kwargs: False,
    )

    monkeypatch.setattr(
        worker,
        "health_check",
        lambda *args, **kwargs: (
            True,
            "healthy",
        ),
    )

    monkeypatch.setattr(
        worker,
        "monitor_logs",
        lambda *args, **kwargs: (
            True,
            "clean",
        ),
    )

    resets: list[str] = []

    monkeypatch.setattr(
        worker,
        "reset_repository_to_ref",
        lambda ref, **kwargs: (
            resets.append(
                ref
            )
            or ref
        ),
    )

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            output = candidate_commit

        elif command[
            :3
        ] == [
            "docker",
            "compose",
            "up",
        ]:
            output = "rebuilt"

        else:
            pytest.fail(
                f"Unexpected command: {command}"
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=output,
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    cfg = config()
    cfg.proposal_only = False

    first = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert first == "rolled_back"
    assert resets == [
        base
    ]

    second = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert second == "active"

    # Recovery is idempotent: the exact rollback happens once.
    assert resets == [
        base
    ]


def test_v2114b_unknown_phase_fails_closed_even_on_base(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = "a" * 40
    candidate_commit = "b" * 40

    _create_lease_test_database(
        monkeypatch,
        tmp_path,
        status="deploying",
        base_commit=base,
        candidate_commit=candidate_commit,
        lease_expires_at=worker.utc_after(
            -60
        ),
        phase="mystery_phase",
    )

    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )

    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )

    commands: list[
        list[str]
    ] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path = worker.ROOT,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        del timeout
        del check
        del env

        commands.append(
            command
        )

        if command == [
            "git",
            "rev-parse",
            "HEAD",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base,
            )

        pytest.fail(
            f"Unexpected command: {command}"
        )

    monkeypatch.setattr(
        worker,
        "run",
        fake_run,
    )

    cfg = config()
    cfg.proposal_only = False

    result = worker.recover_interrupted_deployment(
        {
            "candidate_id": 1,
            "failure_id": 1,
            "status": "deploying",
        },
        cfg,
        {},
    )

    assert result == "recovery_required"

    recovered = worker.fetch_candidate_by_id(
        1
    )

    assert recovered is not None
    assert (
        recovered["status"]
        == "recovery_required"
    )
    assert (
        recovered["deploy_phase"]
        == "ambiguous_phase"
    )

    assert not any(
        command[
            :3
        ]
        in (
            [
                "git",
                "reset",
                "--hard",
            ],
            [
                "docker",
                "compose",
                "up",
            ],
        )
        for command in commands
    )


def test_v2114b_active_lease_blocks_all_other_worker_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config()
    cfg.proposal_only = False

    monkeypatch.setattr(
        worker,
        "update_setting",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        worker,
        "improvement_enabled",
        lambda: True,
    )

    deploying = {
        "candidate_id": 77,
        "failure_id": 12,
        "status": "deploying",
        "deploy_lease_expires_at": worker.utc_after(
            300
        ),
    }

    fetches: list[
        tuple[str, ...]
    ] = []

    def fake_fetch(
        statuses: tuple[str, ...],
    ) -> dict[str, object] | None:
        fetches.append(
            statuses
        )

        if statuses == (
            "deploying",
        ):
            return deploying

        pytest.fail(
            "Worker continued past an active deployment lease."
        )

    monkeypatch.setattr(
        worker,
        "fetch_candidate",
        fake_fetch,
    )

    monkeypatch.setattr(
        worker,
        "deployment_lease_is_expired",
        lambda candidate: False,
    )

    assert worker.run_once(
        cfg,
        {},
    ) is False

    assert fetches == [
        (
            "deploying",
        )
    ]

def test_normalise_unified_diff_repairs_hunk_counts() -> None:
    patch = """diff --git a/bridge/app/example.py b/bridge/app/example.py
--- a/bridge/app/example.py
+++ b/bridge/app/example.py
@@ -1,99 +1,88 @@
-old = 1
+new = 2
 context = True
"""

    fixed = worker.normalise_unified_diff_hunk_counts(
        patch
    )

    assert "@@ -1,2 +1,2 @@" in fixed
    assert "-old = 1" in fixed
    assert "+new = 2" in fixed


def test_build_context_balances_large_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = Path("bridge/app/first.py")
    second = Path("bridge/app/second.py")

    for path in (first, second):
        target = tmp_path / path
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    filler = "\n".join(
        f"filler_{i} = {i}"
        for i in range(1200)
    )

    (tmp_path / first).write_text(
        filler
        + "\ncompleted_calls = []\n"
        + "for call in completed_calls:\n"
        + "    pass\n",
        encoding="utf-8",
    )

    (tmp_path / second).write_text(
        filler
        + "\nfailed_tool = any([])\n"
        + "failure_like = (failed_tool)\n"
        + "await self.record_failure(source=None)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        worker,
        "ROOT",
        tmp_path,
    )

    monkeypatch.setattr(
        worker,
        "infer_context_files",
        lambda failure, policy: [
            str(first),
            str(second),
        ],
    )

    failure = {
        "summary": "Jarvis request failed",
        "category": "general",
        "evidence": {
            "completed_calls": True,
            "failed_tool": True,
        },
    }

    policy = {
        "max_context_characters": 12000,
    }

    context, included = worker.build_context(
        failure,
        policy,
    )

    assert len(context) <= 12000
    assert included == [
        str(first),
        str(second),
    ]
    assert "for call in completed_calls" in context
    assert "failed_tool = any([])" in context
    assert "failure_like = (failed_tool)" in context
    assert "await self.record_failure" in context



def test_run_once_resumes_interrupted_manual_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config()
    cfg.proposal_only = False

    candidate = {
        "candidate_id": 41,
        "failure_id": 9,
        "status": "rolling_back",
        "deploy_phase": "manual_rollback_claimed",
    }

    processed: list[int] = []

    monkeypatch.setattr(
        worker,
        "update_setting",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        worker,
        "improvement_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        worker,
        "fetch_candidate",
        lambda statuses: None,
    )
    monkeypatch.setattr(
        worker,
        "fetch_manual_rollback_candidate",
        lambda: candidate,
    )
    monkeypatch.setattr(
        worker,
        "rollback_candidate",
        lambda item, *args: processed.append(
            int(item["candidate_id"])
        ),
    )

    assert worker.run_once(cfg, {}) is True
    assert processed == [41]


def test_manual_rollback_resumes_after_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config()
    cfg.proposal_only = False

    base = "a" * 40
    candidate_ref = "b" * 40

    candidate = {
        "candidate_id": 42,
        "failure_id": 10,
        "status": "rolling_back",
        "deploy_phase": "manual_rollback_claimed",
        "base_commit": base,
        "candidate_commit": candidate_ref,
        "rollback_ref": base,
    }

    updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        worker,
        "fetch_candidate_by_id",
        lambda candidate_id: dict(candidate),
    )
    monkeypatch.setattr(
        worker,
        "verify_manual_rollback_binding",
        lambda item: {
            "base_commit": base,
            "candidate_commit": candidate_ref,
            "rollback_ref": base,
            "current_ref": base,
            "action": "already_base",
        },
    )
    monkeypatch.setattr(
        worker,
        "update_candidate",
        lambda candidate_id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(
        worker,
        "update_failure",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        worker,
        "notify_aaron",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        worker,
        "health_check",
        lambda timeout: (True, "healthy"),
    )
    monkeypatch.setattr(
        worker,
        "reset_repository_to_ref",
        lambda *args, **kwargs: pytest.fail(
            "already-base recovery must not reset Git"
        ),
    )

    def fake_run(command, **kwargs):
        del kwargs
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=base + "\n"
            )
        if command[:3] == ["docker", "compose", "up"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=""
            )
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(worker, "run", fake_run)

    worker.rollback_candidate(candidate, cfg, {})

    assert any(
        item.get("status") == "rolled_back"
        for item in updates
    )


def test_manual_rollback_unexpected_head_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config()
    cfg.proposal_only = False

    base = "a" * 40
    candidate_ref = "b" * 40
    unexpected = "c" * 40

    candidate = {
        "candidate_id": 43,
        "failure_id": 11,
        "status": "rolling_back",
        "deploy_phase": "manual_rollback_claimed",
        "base_commit": base,
        "candidate_commit": candidate_ref,
        "rollback_ref": base,
    }

    updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        worker,
        "fetch_candidate_by_id",
        lambda candidate_id: dict(candidate),
    )
    monkeypatch.setattr(
        worker,
        "ensure_repo",
        lambda: None,
    )
    monkeypatch.setattr(
        worker,
        "update_candidate",
        lambda candidate_id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(
        worker,
        "audit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        worker,
        "notify_aaron",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        worker,
        "reset_repository_to_ref",
        lambda *args, **kwargs: pytest.fail(
            "unexpected HEAD must never be reset"
        ),
    )
    monkeypatch.setattr(
        worker,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=unexpected + "\n",
        ),
    )

    with pytest.raises(
        worker.WorkerError,
        match="unexpected live HEAD",
    ):
        worker.rollback_candidate(
            candidate,
            cfg,
            {},
        )

    assert any(
        item.get("status") == "recovery_required"
        for item in updates
    )


def test_manual_improvement_does_not_use_autonomous_quota() -> None:
    assert (
        worker.uses_autonomous_attempt_quota(
            {"category": "requested_improvement"}
        )
        is False
    )
    assert (
        worker.uses_autonomous_attempt_quota(
            {"category": "general"}
        )
        is True
    )


def test_attempts_today_excludes_manual_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _initialise_attempt_cap_db(
        monkeypatch,
        tmp_path,
    )

    today = (
        worker.datetime.now(
            worker.timezone.utc
        )
        .date()
        .isoformat()
    )

    with worker.connect() as connection:
        for candidate_id, manual in (
            (101, False),
            (102, True),
        ):
            connection.execute(
                """
                INSERT INTO improvement_audit (
                    created_at,
                    event_type,
                    actor,
                    failure_id,
                    candidate_id,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    today + "T12:00:00+00:00",
                    "candidate_generation_started",
                    "worker",
                    candidate_id,
                    candidate_id,
                    worker.json_dump(
                        {
                            "model": "test",
                            "manual_request": manual,
                        }
                    ),
                ),
            )

    assert worker.attempts_today() == 1
