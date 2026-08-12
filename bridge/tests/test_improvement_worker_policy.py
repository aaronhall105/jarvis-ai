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
