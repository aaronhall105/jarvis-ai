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
        base_branch="main",
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
