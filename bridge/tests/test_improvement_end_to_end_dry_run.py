from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.self_improvement import (
    SelfImprovementEngine,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)

SPEC = importlib.util.spec_from_file_location(
    "self_improvement_worker_e2e_dry_run",
    PROJECT_ROOT
    / "tools"
    / "self_improvement_worker.py",
)

assert SPEC is not None
assert SPEC.loader is not None

worker = importlib.util.module_from_spec(
    SPEC
)

sys.modules[
    SPEC.name
] = worker

SPEC.loader.exec_module(
    worker
)


PATCH = (
    "diff --git a/bridge/app/example.py "
    "b/bridge/app/example.py\n"
    "--- a/bridge/app/example.py\n"
    "+++ b/bridge/app/example.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
    "diff --git a/bridge/tests/test_example.py "
    "b/bridge/tests/test_example.py\n"
    "--- a/bridge/tests/test_example.py\n"
    "+++ b/bridge/tests/test_example.py\n"
    "@@ -5 +5 @@ def test_value() -> None:\n"
    "-    assert VALUE == 1\n"
    "+    assert VALUE == 2\n"
)


def _git(
    repo: Path,
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


def _config() -> Any:
    return worker.WorkerConfig(
        model="deterministic-dry-run",
        poll_seconds=15,
        max_attempts_per_day=3,
        max_patch_lines=100,
        max_changed_files=3,
        github_enabled=False,
        ai_review_enabled=True,
        notify_enabled=False,
        notify_service=(
            "notify.mobile_app_aaron_s_phone"
        ),
        auto_deploy_low_risk=False,
        proposal_only=True,
        candidate_timeout_seconds=60,
        deploy_health_timeout_seconds=30,
        base_branch="main",
    )


def _seed_failure_and_candidate(
    engine: SelfImprovementEngine,
) -> tuple[int, int]:
    now = engine._utc_now()  # noqa: SLF001

    with engine._connect() as connection:  # noqa: SLF001
        failure = connection.execute(
            """
            INSERT INTO improvement_failures (
                created_at,
                updated_at,
                last_seen_at,
                conversation_id,
                user_key,
                signature,
                category,
                severity,
                summary,
                evidence_json,
                occurrences,
                explicit,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                now,
                "dry-run-conversation",
                "aaron",
                "dry-run-signature",
                "general",
                "low",
                "Dry-run VALUE regression",
                json.dumps(
                    {
                        "expected": 2,
                        "actual": 1,
                    }
                ),
                1,
                1,
                "recorded",
            ),
        )

        failure_id = int(
            failure.lastrowid
        )

        candidate = connection.execute(
            """
            INSERT INTO improvement_candidates (
                failure_id,
                created_at,
                updated_at,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                failure_id,
                now,
                now,
                "queued",
            ),
        )

        candidate_id = int(
            candidate.lastrowid
        )

    return (
        failure_id,
        candidate_id,
    )


def _install_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Any]:
    sandbox = (
        tmp_path
        / "jarvis-e2e-sandbox"
    )

    repo = (
        sandbox
        / "repo"
    )

    work_root = (
        sandbox
        / "improver"
    )

    data_dir = (
        sandbox
        / "data"
    )

    repo.mkdir(
        parents=True
    )

    (
        repo
        / "bridge"
        / "app"
    ).mkdir(
        parents=True
    )

    (
        repo
        / "bridge"
        / "tests"
    ).mkdir(
        parents=True
    )

    (
        repo
        / "config"
    ).mkdir(
        parents=True
    )

    (
        repo
        / "bridge"
        / "app"
        / "__init__.py"
    ).write_text(
        "",
        encoding="utf-8",
    )

    (
        repo
        / "bridge"
        / "app"
        / "example.py"
    ).write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    (
        repo
        / "bridge"
        / "tests"
        / "test_example.py"
    ).write_text(
        "from app.example import VALUE\n"
        "\n"
        "\n"
        "def test_value() -> None:\n"
        "    assert VALUE == 1\n",
        encoding="utf-8",
    )

    policy = {
        "allowed_edit_paths": [
            "bridge/app/*.py",
            "bridge/tests/*.py",
        ],
        "forbidden_paths": [
            ".env",
            ".env.*",
            "data/**",
            "logs/**",
            "backup/**",
            ".git/**",
            "tools/**",
        ],
        "forbidden_added_patterns": [
            r"shell\s*=\s*True",
            r"\beval\s*\(",
            r"\bexec\s*\(",
        ],
        "allowed_context_paths": [
            "bridge/app/*.py",
            "bridge/tests/*.py",
        ],
        "context_files_by_category": {
            "general": [
                "bridge/app/example.py",
            ],
        },
        "max_context_files": 3,
        "max_context_characters": 10000,
        "high_risk_paths": [],
        "medium_risk_paths": [],
    }

    policy_path = (
        repo
        / "config"
        / "self_improvement_policy.json"
    )

    policy_path.write_text(
        json.dumps(
            policy,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    _git(
        repo,
        "init",
        "-q",
    )

    _git(
        repo,
        "config",
        "user.email",
        "jarvis-dry-run@example.invalid",
    )

    _git(
        repo,
        "config",
        "user.name",
        "Jarvis Dry Run",
    )

    _git(
        repo,
        "add",
        ".",
    )

    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "dry-run base",
    )

    _git(
        repo,
        "branch",
        "-M",
        "main",
    )

    base_commit = _git(
        repo,
        "rev-parse",
        "HEAD",
    )

    database = (
        data_dir
        / "jarvis_improvement.db"
    )

    engine = SelfImprovementEngine(
        str(
            database
        ),
        enabled=True,
        auto_prepare=False,
        core_version="dry-run",
    )

    (
        failure_id,
        candidate_id,
    ) = _seed_failure_and_candidate(
        engine
    )

    monkeypatch.setattr(
        worker,
        "ROOT",
        repo,
    )

    monkeypatch.setattr(
        worker,
        "DATA_DIR",
        data_dir,
    )

    monkeypatch.setattr(
        worker,
        "DB_PATH",
        database,
    )

    monkeypatch.setattr(
        worker,
        "POLICY_PATH",
        policy_path,
    )

    monkeypatch.setattr(
        worker,
        "ENV_PATH",
        sandbox
        / "missing.env",
    )

    monkeypatch.setattr(
        worker,
        "WORK_ROOT",
        work_root,
    )

    monkeypatch.setattr(
        worker,
        "WORKTREES",
        work_root
        / "worktrees",
    )

    monkeypatch.setattr(
        worker,
        "ARTIFACTS",
        work_root
        / "artifacts",
    )

    monkeypatch.setattr(
        worker,
        "LOCK_PATH",
        work_root
        / "worker.lock",
    )

    monkeypatch.setattr(
        worker,
        "VENV_PYTHON",
        Path(
            sys.executable
        ),
    )

    sandbox_root = sandbox.resolve()

    docker_commands: list[
        list[str]
    ] = []

    def safe_run(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        actual_cwd = (
            repo
            if cwd is None
            else Path(
                cwd
            )
        )

        resolved_cwd = (
            actual_cwd
            .resolve()
        )

        if not resolved_cwd.is_relative_to(
            sandbox_root
        ):
            raise AssertionError(
                "DRY-RUN SAFETY VIOLATION: "
                "command attempted outside sandbox: "
                f"{resolved_cwd}: {command}"
            )

        if command[
            :3
        ] == [
            "docker",
            "compose",
            "up",
        ]:
            docker_commands.append(
                list(
                    command
                )
            )

            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=(
                    "DRY-RUN: isolated Docker "
                    "boundary simulated"
                ),
            )

        completed = subprocess.run(
            command,
            cwd=actual_cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
            check=False,
        )

        if (
            check
            and completed.returncode != 0
        ):
            raise worker.WorkerError(
                "Dry-run command failed "
                f"({completed.returncode}): "
                + " ".join(
                    command
                )
                + "\n"
                + completed.stdout[
                    -8000:
                ]
            )

        return completed

    monkeypatch.setattr(
        worker,
        "run",
        safe_run,
    )

    payload = {
        "summary": (
            "Set the deterministic example value to 2"
        ),
        "root_cause": (
            "The example constant retained its old value."
        ),
        "risk": "low",
        "patch": PATCH,
        "tests_added": [
            "bridge/tests/test_example.py",
        ],
        "notes": [
            "Deterministic end-to-end dry run.",
        ],
    }

    monkeypatch.setattr(
        worker,
        "request_patch",
        lambda **kwargs: (
            payload,
            {
                "dry_run": True,
                "openai_call": False,
            },
        ),
    )

    monkeypatch.setattr(
        worker,
        "run_validation",
        lambda *args, **kwargs: (
            {
                "passed": True,
                "dry_run": True,
            },
            {
                "passed": True,
                "dry_run": True,
            },
        ),
    )

    monkeypatch.setattr(
        worker,
        "docker_smoke_test",
        lambda *args, **kwargs: {
            "passed": True,
            "dry_run": True,
        },
    )

    monkeypatch.setattr(
        worker,
        "request_independent_review",
        lambda **kwargs: {
            "verdict": "approve",
            "risk": "low",
            "summary": (
                "Deterministic dry-run reviewer approval."
            ),
            "findings": [],
            "required_changes": [],
        },
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
            "dry-run healthy",
        ),
    )

    monkeypatch.setattr(
        worker,
        "monitor_logs",
        lambda *args, **kwargs: (
            True,
            "dry-run logs clean",
        ),
    )

    return {
        "sandbox": sandbox,
        "repo": repo,
        "engine": engine,
        "failure_id": failure_id,
        "candidate_id": candidate_id,
        "base_commit": base_commit,
        "config": _config(),
        "docker_commands": docker_commands,
    }


def _prepare_and_request_deploy(
    environment: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = int(
        environment[
            "candidate_id"
        ]
    )

    failure_id = int(
        environment[
            "failure_id"
        ]
    )

    config = environment[
        "config"
    ]

    engine: SelfImprovementEngine = (
        environment[
            "engine"
        ]
    )

    worker.process_queued_candidate(
        {
            "candidate_id": candidate_id,
            "failure_id": failure_id,
        },
        config,
        {},
    )

    candidate = asyncio.run(
        engine.get_candidate(
            candidate_id
        )
    )

    assert candidate is not None
    assert (
        candidate[
            "status"
        ]
        == "awaiting_approval"
    )

    review_code = str(
        candidate[
            "approval_code"
        ]
    )

    assert (
        len(
            review_code
        )
        == 6
    )

    assert review_code.isdigit()

    base_commit = str(
        candidate[
            "base_commit"
        ]
    )

    candidate_commit = str(
        candidate[
            "candidate_commit"
        ]
    )

    validated_hash = str(
        candidate[
            "validated_patch_sha256"
        ]
    )

    assert (
        base_commit
        == environment[
            "base_commit"
        ]
    )

    assert (
        candidate_commit
        != base_commit
    )

    assert (
        len(
            validated_hash
        )
        == 64
    )

    approved = asyncio.run(
        engine.approve_candidate(
            candidate_id,
            review_code,
            "Aaron",
        )
    )

    assert approved.success is True
    assert approved.details is not None

    deploy_code = str(
        approved.details[
            "deploy_code"
        ]
    )

    assert (
        len(
            deploy_code
        )
        == 6
    )

    assert deploy_code.isdigit()

    requested = asyncio.run(
        engine.request_deploy(
            candidate_id,
            deploy_code,
            "Aaron",
        )
    )

    assert requested.success is True

    candidate = asyncio.run(
        engine.get_candidate(
            candidate_id
        )
    )

    assert candidate is not None
    assert (
        candidate[
            "status"
        ]
        == "deploy_requested"
    )
    assert (
        candidate[
            "deploy_phase"
        ]
        == "requested"
    )

    return candidate


def test_v2115a_end_to_end_transaction_succeeds_in_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _install_isolated_runtime(
        monkeypatch,
        tmp_path,
    )

    candidate = _prepare_and_request_deploy(
        environment
    )

    config = environment[
        "config"
    ]

    # Production remains Proposal Mode ON. Only this disposable
    # test configuration is allowed to enter the deployment
    # transaction.
    config.proposal_only = False

    worker.deploy_candidate(
        worker.fetch_candidate_by_id(
            int(
                candidate[
                    "candidate_id"
                ]
            )
        ),
        config,
        {},
    )

    final = asyncio.run(
        environment[
            "engine"
        ].get_candidate(
            int(
                candidate[
                    "candidate_id"
                ]
            )
        )
    )

    assert final is not None
    assert final["status"] == "deployed"
    assert final["deploy_phase"] == "deployed"
    assert final["deploy_lease_id"] is None

    repo_head = _git(
        environment[
            "repo"
        ],
        "rev-parse",
        "HEAD",
    )

    assert (
        repo_head
        == final[
            "candidate_commit"
        ]
    )

    assert (
        environment[
            "docker_commands"
        ]
        == [
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
            ]
        ]
    )

    failure = asyncio.run(
        environment[
            "engine"
        ].get_failure(
            int(
                environment[
                    "failure_id"
                ]
            )
        )
    )

    assert failure is not None
    assert failure["status"] == "deployed"


def test_v2115a_tampered_binding_fails_before_merge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _install_isolated_runtime(
        monkeypatch,
        tmp_path,
    )

    candidate = _prepare_and_request_deploy(
        environment
    )

    candidate_id = int(
        candidate[
            "candidate_id"
        ]
    )

    with worker.connect() as connection:
        connection.execute(
            """
            UPDATE improvement_candidates
            SET validated_patch_sha256 = ?
            WHERE candidate_id = ?
            """,
            (
                "0" * 64,
                candidate_id,
            ),
        )

    config = environment[
        "config"
    ]

    config.proposal_only = False

    with pytest.raises(
        worker.WorkerError,
        match="diff changed",
    ):
        worker.deploy_candidate(
            worker.fetch_candidate_by_id(
                candidate_id
            ),
            config,
            {},
        )

    final = asyncio.run(
        environment[
            "engine"
        ].get_candidate(
            candidate_id
        )
    )

    assert final is not None
    assert (
        final[
            "status"
        ]
        == "deployment_blocked"
    )
    assert (
        final[
            "deploy_phase"
        ]
        == "premerge_failed"
    )

    repo_head = _git(
        environment[
            "repo"
        ],
        "rev-parse",
        "HEAD",
    )

    assert (
        repo_head
        == environment[
            "base_commit"
        ]
    )

    assert (
        environment[
            "docker_commands"
        ]
        == []
    )


def test_v2115a_proposal_mode_blocks_transaction_before_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _install_isolated_runtime(
        monkeypatch,
        tmp_path,
    )

    candidate = _prepare_and_request_deploy(
        environment
    )

    candidate_id = int(
        candidate[
            "candidate_id"
        ]
    )

    config = environment[
        "config"
    ]

    assert config.proposal_only is True

    with pytest.raises(
        worker.WorkerError,
        match="Proposal Mode",
    ):
        worker.deploy_candidate(
            worker.fetch_candidate_by_id(
                candidate_id
            ),
            config,
            {},
        )

    final = asyncio.run(
        environment[
            "engine"
        ].get_candidate(
            candidate_id
        )
    )

    assert final is not None
    assert (
        final[
            "status"
        ]
        == "deploy_requested"
    )
    assert (
        final[
            "deploy_phase"
        ]
        == "requested"
    )
    assert final["deploy_lease_id"] is None

    repo_head = _git(
        environment[
            "repo"
        ],
        "rev-parse",
        "HEAD",
    )

    assert (
        repo_head
        == environment[
            "base_commit"
        ]
    )

    assert (
        environment[
            "docker_commands"
        ]
        == []
    )
