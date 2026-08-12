from __future__ import annotations

import importlib.util
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
