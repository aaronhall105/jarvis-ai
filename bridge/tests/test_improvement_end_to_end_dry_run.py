from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
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
    *,
    real_docker: bool = False,
    docker_image: str | None = None,
    docker_failure_probe: bool = False,
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

    docker_project = ""

    if real_docker:
        image = str(
            docker_image
            or ""
        ).strip()

        if not image:
            raise AssertionError(
                "Real Docker dry-run requires "
                "an explicitly pinned local image."
            )

        docker_project = (
            "jarvis-v2115b-"
            + uuid.uuid4().hex[
                :12
            ]
        )

        compose_file = (
            repo
            / "compose.yaml"
        )

        boundary_command = (
            "sleep 300"
        )

        health_command = (
            "exit 0"
        )

        environment_block = ""

        if docker_failure_probe:
            boundary_command = (
                'if [ "$JARVIS_DRY_RUN_STATE" = "base" ]; '
                "then sleep 300; else exit 42; fi"
            )

            health_command = (
                'test "$JARVIS_DRY_RUN_STATE" = "base"'
            )

            environment_block = (
                "    environment:\n"
                "      JARVIS_DRY_RUN_STATE: "
                '"${JARVIS_DRY_RUN_STATE:-unknown}"\n'
            )

        compose_file.write_text(
            (
                "services:\n"
                "  boundary:\n"
                f"    image: {json.dumps(image)}\n"
                "    pull_policy: never\n"
                "    entrypoint:\n"
                "      - /bin/sh\n"
                "      - -c\n"
                f"      - {json.dumps(boundary_command)}\n"
                + environment_block
                + "    read_only: true\n"
                "    cap_drop:\n"
                "      - ALL\n"
                "    security_opt:\n"
                "      - no-new-privileges:true\n"
                "    healthcheck:\n"
                "      test:\n"
                "        - CMD\n"
                "        - /bin/sh\n"
                "        - -c\n"
                f"        - {json.dumps(health_command)}\n"
                "      interval: 1s\n"
                "      timeout: 2s\n"
                "      retries: 10\n"
                "    networks:\n"
                "      - isolated\n"
                "networks:\n"
                "  isolated:\n"
                "    internal: true\n"
            ),
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

        is_compose = (
            command[
                :2
            ]
            == [
                "docker",
                "compose",
            ]
        )

        effective_env = env

        if (
            is_compose
            and real_docker
        ):
            effective_env = dict(
                os.environ
                if env is None
                else env
            )

            effective_env[
                "COMPOSE_PROJECT_NAME"
            ] = docker_project

            if (
                docker_failure_probe
                and command[
                    :3
                ]
                == [
                    "docker",
                    "compose",
                    "up",
                ]
            ):
                source = (
                    repo
                    / "bridge"
                    / "app"
                    / "example.py"
                ).read_text(
                    encoding="utf-8"
                ).strip()

                if source == "VALUE = 1":
                    state = "base"

                elif source == "VALUE = 2":
                    state = "candidate"

                else:
                    state = "unknown"

                effective_env[
                    "JARVIS_DRY_RUN_STATE"
                ] = state

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

            if not real_docker:
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
            env=effective_env,
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
        "real_docker": real_docker,
        "docker_project": docker_project,
        "docker_image": docker_image,
        "docker_failure_probe": docker_failure_probe,
    }




def _real_compose_environment(
    environment: dict[str, Any],
) -> dict[str, str]:
    project = str(
        environment.get(
            "docker_project"
        )
        or ""
    ).strip()

    if not project:
        raise AssertionError(
            "Disposable Compose project is missing."
        )

    result = dict(
        os.environ
    )

    result[
        "COMPOSE_PROJECT_NAME"
    ] = project

    return result


def _docker_project_container_ids(
    project: str,
) -> list[str]:
    completed = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            (
                "label=com.docker.compose.project="
                + project
            ),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def _docker_project_network_ids(
    project: str,
) -> list[str]:
    completed = subprocess.run(
        [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            (
                "label=com.docker.compose.project="
                + project
            ),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def _cleanup_real_docker(
    environment: dict[str, Any],
) -> None:
    if not bool(
        environment.get(
            "real_docker"
        )
    ):
        return

    project = str(
        environment[
            "docker_project"
        ]
    )

    repo = Path(
        environment[
            "repo"
        ]
    )

    compose_env = _real_compose_environment(
        environment
    )

    subprocess.run(
        [
            "docker",
            "compose",
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "0",
        ],
        cwd=repo,
        env=compose_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    container_ids = (
        _docker_project_container_ids(
            project
        )
    )

    if container_ids:
        subprocess.run(
            [
                "docker",
                "rm",
                "-f",
                *container_ids,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    network_ids = (
        _docker_project_network_ids(
            project
        )
    )

    if network_ids:
        subprocess.run(
            [
                "docker",
                "network",
                "rm",
                *network_ids,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    remaining_containers = (
        _docker_project_container_ids(
            project
        )
    )

    remaining_networks = (
        _docker_project_network_ids(
            project
        )
    )

    if (
        remaining_containers
        or remaining_networks
    ):
        raise AssertionError(
            "Disposable Docker project cleanup "
            "left resources behind: "
            f"containers={remaining_containers}, "
            f"networks={remaining_networks}"
        )


def _wait_for_real_boundary(
    environment: dict[str, Any],
) -> dict[str, Any]:
    project = str(
        environment[
            "docker_project"
        ]
    )

    deadline = (
        time.monotonic()
        + 20.0
    )

    while time.monotonic() < deadline:
        ids = _docker_project_container_ids(
            project
        )

        if len(
            ids
        ) == 1:
            inspect = subprocess.run(
                [
                    "docker",
                    "inspect",
                    ids[
                        0
                    ],
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
            )

            payload = json.loads(
                inspect.stdout
            )[0]

            state = payload[
                "State"
            ]

            health = (
                state.get(
                    "Health"
                )
                or {}
            ).get(
                "Status"
            )

            if health == "healthy":
                return payload

            if state.get(
                "Status"
            ) in {
                "dead",
                "exited",
            }:
                raise AssertionError(
                    "Disposable Docker boundary "
                    f"exited early: {state}"
                )

        time.sleep(
            0.2
        )

    raise AssertionError(
        "Disposable Docker boundary did "
        "not become healthy."
    )


def _assert_real_docker_isolation(
    environment: dict[str, Any],
    container: dict[str, Any],
) -> None:
    project = str(
        environment[
            "docker_project"
        ]
    )

    host_config = container[
        "HostConfig"
    ]

    port_bindings = (
        host_config.get(
            "PortBindings"
        )
        or {}
    )

    assert port_bindings == {}

    mounts = container.get(
        "Mounts"
    ) or []

    assert not any(
        mount.get(
            "Type"
        )
        == "bind"
        for mount in mounts
    )

    assert host_config.get(
        "ReadonlyRootfs"
    ) is True

    security_opt = (
        host_config.get(
            "SecurityOpt"
        )
        or []
    )

    assert any(
        "no-new-privileges"
        in str(
            value
        )
        for value in security_opt
    )

    cap_drop = (
        host_config.get(
            "CapDrop"
        )
        or []
    )

    assert "ALL" in {
        str(
            value
        ).upper()
        for value in cap_drop
    }

    networks = (
        container[
            "NetworkSettings"
        ].get(
            "Networks"
        )
        or {}
    )

    assert set(
        networks
    ) == {
        (
            project
            + "_isolated"
        )
    }

    network_id = next(
        iter(
            _docker_project_network_ids(
                project
            )
        )
    )

    network_inspect = subprocess.run(
        [
            "docker",
            "network",
            "inspect",
            network_id,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    network = json.loads(
        network_inspect.stdout
    )[0]

    assert network.get(
        "Internal"
    ) is True


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




def _authorize_manual_rollback(
    environment: dict[str, Any],
    candidate_id: int,
) -> dict[str, Any]:
    engine = environment[
        "engine"
    ]

    issued = asyncio.run(
        engine.issue_rollback_ticket(
            candidate_id,
            "Aaron",
        )
    )

    assert issued.success is True
    assert issued.details is not None

    rollback_code = str(
        issued.details[
            "rollback_code"
        ]
    )

    assert len(
        rollback_code
    ) == 6

    assert rollback_code.isdigit()

    requested = asyncio.run(
        engine.request_rollback(
            candidate_id,
            rollback_code,
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
        == "rollback_requested"
    )

    assert (
        candidate[
            "deploy_phase"
        ]
        == "manual_rollback_requested"
    )

    assert (
        candidate[
            "rollback_ticket_consumed_at"
        ]
        is not None
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

@pytest.mark.skipif(
    os.environ.get(
        "JARVIS_V2115B_REAL_DOCKER"
    )
    != "1",
    reason=(
        "V2.1.15B real Docker test runs "
        "only when explicitly enabled."
    ),
)
def test_v2115b_real_compose_deployment_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    docker_image = str(
        os.environ.get(
            "JARVIS_V2115B_IMAGE"
        )
        or ""
    ).strip()

    assert docker_image

    environment = _install_isolated_runtime(
        monkeypatch,
        tmp_path,
        real_docker=True,
        docker_image=docker_image,
    )

    request.addfinalizer(
        lambda: _cleanup_real_docker(
            environment
        )
    )

    project = str(
        environment[
            "docker_project"
        ]
    )

    assert project.startswith(
        "jarvis-v2115b-"
    )

    assert (
        _docker_project_container_ids(
            project
        )
        == []
    )

    assert (
        _docker_project_network_ids(
            project
        )
        == []
    )

    candidate = _prepare_and_request_deploy(
        environment
    )

    config = environment[
        "config"
    ]

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

    container = _wait_for_real_boundary(
        environment
    )

    _assert_real_docker_isolation(
        environment,
        container,
    )

    assert (
        container[
            "Config"
        ][
            "Image"
        ]
        == docker_image
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
        == final[
            "candidate_commit"
        ]
    )

    # Explicit cleanup proves the successful path.
    # The pytest finalizer above independently guarantees
    # the same cleanup is attempted on any earlier failure.
    _cleanup_real_docker(
        environment
    )

    assert (
        _docker_project_container_ids(
            project
        )
        == []
    )

    assert (
        _docker_project_network_ids(
            project
        )
        == []
    )

@pytest.mark.skipif(
    os.environ.get(
        "JARVIS_V2115C_REAL_DOCKER"
    )
    != "1",
    reason=(
        "V2.1.15C real rollback test runs "
        "only when explicitly enabled."
    ),
)
def test_v2115c_real_compose_failure_auto_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    docker_image = str(
        os.environ.get(
            "JARVIS_V2115C_IMAGE"
        )
        or ""
    ).strip()

    assert docker_image

    environment = _install_isolated_runtime(
        monkeypatch,
        tmp_path,
        real_docker=True,
        docker_image=docker_image,
        docker_failure_probe=True,
    )

    request.addfinalizer(
        lambda: _cleanup_real_docker(
            environment
        )
    )

    candidate = _prepare_and_request_deploy(
        environment
    )

    candidate_id = int(
        candidate[
            "candidate_id"
        ]
    )

    base_commit = str(
        environment[
            "base_commit"
        ]
    )

    candidate_commit = str(
        candidate[
            "candidate_commit"
        ]
    )

    assert candidate_commit != base_commit

    observations: list[
        dict[str, Any]
    ] = []

    def deployment_health(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        del args
        del kwargs

        project = str(
            environment[
                "docker_project"
            ]
        )

        try:
            container = _wait_for_real_boundary(
                environment
            )

        except AssertionError as exc:
            ids = _docker_project_container_ids(
                project
            )

            assert len(ids) == 1

            completed = subprocess.run(
                [
                    "docker",
                    "inspect",
                    ids[
                        0
                    ],
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
            )

            container = json.loads(
                completed.stdout
            )[0]

            env = (
                container[
                    "Config"
                ].get(
                    "Env"
                )
                or []
            )

            state = next(
                (
                    value.split(
                        "=",
                        1,
                    )[
                        1
                    ]
                    for value in env
                    if value.startswith(
                        "JARVIS_DRY_RUN_STATE="
                    )
                ),
                "",
            )

            observations.append(
                {
                    "state": state,
                    "status": container[
                        "State"
                    ][
                        "Status"
                    ],
                    "exit_code": container[
                        "State"
                    ][
                        "ExitCode"
                    ],
                    "head": _git(
                        environment[
                            "repo"
                        ],
                        "rev-parse",
                        "HEAD",
                    ),
                }
            )

            return (
                False,
                str(
                    exc
                ),
            )

        env = (
            container[
                "Config"
            ].get(
                "Env"
            )
            or []
        )

        state = next(
            (
                value.split(
                    "=",
                    1,
                )[
                    1
                ]
                for value in env
                if value.startswith(
                    "JARVIS_DRY_RUN_STATE="
                )
            ),
            "",
        )

        observations.append(
            {
                "state": state,
                "status": container[
                    "State"
                ][
                    "Status"
                ],
                "health": (
                    container[
                        "State"
                    ].get(
                        "Health"
                    )
                    or {}
                ).get(
                    "Status"
                ),
                "exit_code": container[
                    "State"
                ][
                    "ExitCode"
                ],
                "head": _git(
                    environment[
                        "repo"
                    ],
                    "rev-parse",
                    "HEAD",
                ),
            }
        )

        return (
            True,
            (
                "Disposable rollback boundary "
                "is healthy."
            ),
        )

    monkeypatch.setattr(
        worker,
        "health_check",
        deployment_health,
    )

    config = environment[
        "config"
    ]

    config.proposal_only = False

    with pytest.raises(
        worker.WorkerError,
        match=(
            "Deployment health "
            "verification failed"
        ),
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
    assert final["status"] == "rolled_back"
    assert final["deploy_phase"] == "rolled_back"
    assert final["deploy_lease_id"] is None
    assert final["rolled_back_at"] is not None

    repo_head = _git(
        environment[
            "repo"
        ],
        "rev-parse",
        "HEAD",
    )

    assert repo_head == base_commit

    assert (
        environment[
            "repo"
        ]
        / "bridge"
        / "app"
        / "example.py"
    ).read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"

    assert len(observations) == 2

    candidate_observation = observations[
        0
    ]

    rollback_observation = observations[
        1
    ]

    assert (
        candidate_observation[
            "state"
        ]
        == "candidate"
    )

    assert (
        candidate_observation[
            "status"
        ]
        == "exited"
    )

    assert (
        candidate_observation[
            "exit_code"
        ]
        == 42
    )

    assert (
        candidate_observation[
            "head"
        ]
        == candidate_commit
    )

    assert (
        rollback_observation[
            "state"
        ]
        == "base"
    )

    assert (
        rollback_observation[
            "status"
        ]
        == "running"
    )

    assert (
        rollback_observation[
            "health"
        ]
        == "healthy"
    )

    assert (
        rollback_observation[
            "head"
        ]
        == base_commit
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
            ],
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
            ],
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
    assert failure["status"] == "recorded"

    container = _wait_for_real_boundary(
        environment
    )

    _assert_real_docker_isolation(
        environment,
        container,
    )

    assert (
        container[
            "Config"
        ][
            "Image"
        ]
        == docker_image
    )

    _cleanup_real_docker(
        environment
    )

    project = str(
        environment[
            "docker_project"
        ]
    )

    assert (
        _docker_project_container_ids(
            project
        )
        == []
    )

    assert (
        _docker_project_network_ids(
            project
        )
        == []
    )

@pytest.mark.skipif(
    os.environ.get(
        "JARVIS_V2116B_REAL_DOCKER"
    )
    != "1",
    reason=(
        "V2.1.16B real manual rollback test "
        "runs only when explicitly enabled."
    ),
)
def test_v2116b_manual_rollback_exact_candidate_real_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    docker_image = str(
        os.environ.get(
            "JARVIS_V2116B_IMAGE"
        )
        or ""
    ).strip()

    assert docker_image

    environment = _install_isolated_runtime(
        monkeypatch,
        tmp_path,
        real_docker=True,
        docker_image=docker_image,
    )

    request.addfinalizer(
        lambda: _cleanup_real_docker(
            environment
        )
    )

    candidate = _prepare_and_request_deploy(
        environment
    )

    candidate_id = int(
        candidate[
            "candidate_id"
        ]
    )

    base_commit = str(
        environment[
            "base_commit"
        ]
    )

    config = environment[
        "config"
    ]

    config.proposal_only = False

    worker.deploy_candidate(
        worker.fetch_candidate_by_id(
            candidate_id
        ),
        config,
        {},
    )

    deployed = asyncio.run(
        environment[
            "engine"
        ].get_candidate(
            candidate_id
        )
    )

    assert deployed is not None
    assert deployed["status"] == "deployed"

    candidate_commit = str(
        deployed[
            "candidate_commit"
        ]
    )

    assert (
        _git(
            environment[
                "repo"
            ],
            "rev-parse",
            "HEAD",
        )
        == candidate_commit
    )

    _authorize_manual_rollback(
        environment,
        candidate_id,
    )

    worker.rollback_candidate(
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
    assert final["status"] == "rolled_back"
    assert final["deploy_phase"] == "rolled_back"

    assert (
        _git(
            environment[
                "repo"
            ],
            "rev-parse",
            "HEAD",
        )
        == base_commit
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
            ],
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
            ],
        ]
    )

    container = _wait_for_real_boundary(
        environment
    )

    _assert_real_docker_isolation(
        environment,
        container,
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
    assert failure["status"] == "recorded"

    _cleanup_real_docker(
        environment
    )

    project = str(
        environment[
            "docker_project"
        ]
    )

    assert (
        _docker_project_container_ids(
            project
        )
        == []
    )

    assert (
        _docker_project_network_ids(
            project
        )
        == []
    )


def test_v2116b_manual_rollback_already_at_base_is_idempotent(
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

    base_commit = str(
        environment[
            "base_commit"
        ]
    )

    config = environment[
        "config"
    ]

    config.proposal_only = False

    worker.deploy_candidate(
        worker.fetch_candidate_by_id(
            candidate_id
        ),
        config,
        {},
    )

    assert len(
        environment[
            "docker_commands"
        ]
    ) == 1

    _git(
        environment[
            "repo"
        ],
        "reset",
        "--hard",
        base_commit,
    )

    assert (
        _git(
            environment[
                "repo"
            ],
            "rev-parse",
            "HEAD",
        )
        == base_commit
    )

    _authorize_manual_rollback(
        environment,
        candidate_id,
    )

    def forbidden_reset(
        *args: Any,
        **kwargs: Any,
    ) -> str:
        del args
        del kwargs

        raise AssertionError(
            "Idempotent base rollback must not "
            "execute git reset."
        )

    monkeypatch.setattr(
        worker,
        "reset_repository_to_ref",
        forbidden_reset,
    )

    worker.rollback_candidate(
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
    assert final["status"] == "rolled_back"
    assert final["deploy_phase"] == "rolled_back"

    assert (
        _git(
            environment[
                "repo"
            ],
            "rev-parse",
            "HEAD",
        )
        == base_commit
    )

    assert len(
        environment[
            "docker_commands"
        ]
    ) == 2


def test_v2116b_manual_rollback_refuses_newer_unrelated_head(
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

    config.proposal_only = False

    worker.deploy_candidate(
        worker.fetch_candidate_by_id(
            candidate_id
        ),
        config,
        {},
    )

    assert len(
        environment[
            "docker_commands"
        ]
    ) == 1

    newer_file = (
        environment[
            "repo"
        ]
        / "bridge"
        / "app"
        / "newer_commit.py"
    )

    newer_file.write_text(
        "NEWER = True\n",
        encoding="utf-8",
    )

    _git(
        environment[
            "repo"
        ],
        "add",
        "bridge/app/newer_commit.py",
    )

    _git(
        environment[
            "repo"
        ],
        "commit",
        "-m",
        "newer unrelated commit",
    )

    newer_head = _git(
        environment[
            "repo"
        ],
        "rev-parse",
        "HEAD",
    )

    deployed = asyncio.run(
        environment[
            "engine"
        ].get_candidate(
            candidate_id
        )
    )

    assert deployed is not None

    assert (
        newer_head
        != deployed[
            "candidate_commit"
        ]
    )

    assert (
        newer_head
        != deployed[
            "base_commit"
        ]
    )

    _authorize_manual_rollback(
        environment,
        candidate_id,
    )

    def forbidden_reset(
        *args: Any,
        **kwargs: Any,
    ) -> str:
        del args
        del kwargs

        raise AssertionError(
            "Unexpected HEAD must never reach git reset."
        )

    monkeypatch.setattr(
        worker,
        "reset_repository_to_ref",
        forbidden_reset,
    )

    with pytest.raises(
        worker.WorkerError,
        match="unexpected live HEAD",
    ):
        worker.rollback_candidate(
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
        == "recovery_required"
    )

    assert (
        final[
            "deploy_phase"
        ]
        == "manual_rollback_blocked"
    )

    assert (
        _git(
            environment[
                "repo"
            ],
            "rev-parse",
            "HEAD",
        )
        == newer_head
    )

    # Only the original deployment crossed the
    # Docker boundary. The blocked rollback did not.
    assert len(
        environment[
            "docker_commands"
        ]
    ) == 1

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

    assert (
        failure[
            "status"
        ]
        == "deployed"
    )
