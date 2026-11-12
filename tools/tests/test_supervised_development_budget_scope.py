"""Ledger/scope unit regressions; process/socket acceptance is tested separately."""

import hashlib
import json
import sqlite3
import time
import uuid

import pytest

from app.development_scope import editable_path as core_editable
from tools import supervised_development_controller as module
from tools.supervised_development_pipeline import ControllerCallLedger
from tools.supervised_development_scope import editable_path
from tools.supervised_development_runtime import _configured_call_limit
from tools.supervised_development_runtime import SupervisedDevelopmentRuntime


@pytest.mark.parametrize(
    "path",
    [
        "bridge/app/ai_engine.py",
        "bridge/tests/test_executive_agent.py",
        "bridge/app/new_feature.py",
        "bridge/tests/test_new_feature.py",
        "config/display.json",
    ],
)
def test_core_and_worker_share_normal_edit_scope(path):
    assert editable_path(path) and core_editable(path)


@pytest.mark.parametrize(
    "path",
    [
        "bridge/app/../app/ai_engine.py",
        "bridge//app/ai_engine.py",
        "./bridge/app/ai_engine.py",
        "bridge\\app\\ai_engine.py",
        "/bridge/app/ai_engine.py",
        "bridge/.env",
        "bridge/app/development_scope.py",
        "bridge/app/self_improvement.py",
        "bridge/app/main.py",
        "tools/supervised_development_edits.py",
        "tools/autonomy_guardian.py",
        "bridge/app/auth.py",
        "bridge/app/credentials.json",
        "config/security_policy.json",
        "developer_gateway/autonomy.py",
        ".github/workflows/ci.yml",
        "pyproject.toml",
        "docs/JARVIS_PRODUCT_BASELINE.json",
        "docker-compose.override.yml",
        "compose.production.yaml",
        "bridge/tests/conftest.py",
        "sitecustomize.py",
        "bridge/ruff.toml",
    ],
)
def test_scope_cannot_edit_its_authority_or_alias_paths(path):
    assert not editable_path(path) and not core_editable(path)


def running_controller(tmp_path, monkeypatch, *, calls=2, budget=1.0):
    controller = module.SupervisedDevelopmentController(tmp_path / "tasks.sqlite3")
    task_id = controller.create_task(
        base_sha="a" * 40,
        allowed_paths=["diagnostic.py"],
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=calls,
        max_cost_usd=budget,
        deadline_seconds=120,
        goal="Improve evidence-backed diagnostic.",
    )
    parameters = controller.snapshot(task_id)["parameters"]
    controller.record_approval_request(
        task_id,
        owner_id="test-owner",
        request_ref="test-request",
        parameters_sha256=hashlib.sha256(
            json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    # Only the accounting unit boundary is substituted here, never production.
    monkeypatch.setattr(
        module,
        "_request_protected_admission",
        lambda request: {
            **request,
            "status": "approved",
            "authority": "jarvis-autonomy-broker",
            "expires_at": int(time.time()) + 60,
            "guardian_generation": str(uuid.uuid4()),
        },
    )
    controller.start(task_id, current_base_sha="a" * 40)
    return controller, task_id, ControllerCallLedger(controller, task_id)


def test_actual_call_and_cost_reservations_are_durable(tmp_path, monkeypatch):
    controller, task_id, ledger = running_controller(tmp_path, monkeypatch)
    ledger.reserve("first", 0.6)
    with pytest.raises(module.LifecycleError, match="requires_reconciliation"):
        ledger.reserve("second", 0.4)
    ledger.completed("first", 0.25)
    with pytest.raises(module.LifecycleError, match="spending_budget"):
        ledger.reserve("second", 0.8)
    ledger.reserve("second", 0.7)
    ledger.completed("second", 0.5)
    with pytest.raises(module.LifecycleError, match="provider_budget"):
        ledger.reserve("third", 0.1)
    controller.close()
    reopened = module.SupervisedDevelopmentController(tmp_path / "tasks.sqlite3")
    assert reopened.snapshot(task_id)["provider_calls"] == 2
    assert reopened.db.execute("SELECT SUM(actual_cost_usd) FROM calls").fetchone()[0] == 0.75
    assert reopened.recover_after_restart(task_id) == "RECONCILIATION_REQUIRED"
    with pytest.raises(module.LifecycleError, match="task_not_running"):
        ControllerCallLedger(reopened, task_id).reserve("replay", 0.1)
    reopened.close()


def test_ambiguous_transport_is_terminal_and_never_retried(tmp_path, monkeypatch):
    controller, task_id, ledger = running_controller(tmp_path, monkeypatch)
    ledger.reserve("network-write", 0.5)
    ledger.uncertain("network-write")
    assert controller.snapshot(task_id)["state"] == "RECONCILIATION_REQUIRED"
    with pytest.raises(module.LifecycleError, match="task_not_running"):
        ledger.reserve("retry", 0.5)
    assert controller.snapshot(task_id)["provider_calls"] == 1
    controller.close()


def test_unverified_legacy_usage_cannot_fund_another_call(tmp_path, monkeypatch):
    controller, task_id, ledger = running_controller(tmp_path, monkeypatch)
    ledger.reserve("old", 0.5)
    controller.finish_provider_call(task_id, call_id="old", outcome="completed")
    with pytest.raises(module.LifecycleError, match="cost_unverified"):
        ledger.reserve("new", 0.1)
    controller.close()


@pytest.mark.parametrize("value", ["0", "5", "-1", "unlimited", "1.0"])
def test_service_call_policy_is_bounded(monkeypatch, value):
    monkeypatch.setenv("JARVIS_SUPERVISED_MAX_CALLS", value)
    with pytest.raises(module.LifecycleError, match="call_policy_invalid"):
        _configured_call_limit()


def test_service_call_policy_defaults_to_one(monkeypatch):
    monkeypatch.delenv("JARVIS_SUPERVISED_MAX_CALLS", raising=False)
    assert _configured_call_limit() == 1
    monkeypatch.setenv("JARVIS_SUPERVISED_MAX_CALLS", "2")
    assert _configured_call_limit() == 2


def test_legacy_worker_cannot_claim_supervised_candidates(tmp_path, monkeypatch):
    from tools import self_improvement_worker as legacy

    database = tmp_path / "governor.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE improvement_candidates(candidate_id INTEGER, status TEXT, development_scope_json TEXT)"
        )
        connection.executemany(
            "INSERT INTO improvement_candidates VALUES(?,?,?)",
            [
                (1, "queued", '{"source_sha":"preserved"}'),
                (2, "queued", None),
                (3, "deploy_requested", '{"historic":"preserved"}'),
            ],
        )
    before = database.read_bytes()
    monkeypatch.setattr(legacy, "DB_PATH", database)
    assert legacy.fetch_candidate(("queued",))["candidate_id"] == 2
    assert legacy.fetch_candidate(("deploy_requested",))["candidate_id"] == 3
    assert database.read_bytes() == before


@pytest.mark.parametrize("stop_fails", [False, True])
def test_failure_during_activation_observation_requires_confirmed_stop(tmp_path, stop_fails):
    from types import SimpleNamespace

    status = {"state": "STOPPED", "trusted": True, "generation": str(uuid.uuid4())}
    controller = module.SupervisedDevelopmentController(tmp_path / "tasks.sqlite3")

    def source_failure(_claimed):
        status["state"] = "ACTIVE"
        raise OSError("disposable observation failure")

    calls = []

    def stop():
        calls.append("STOP")
        if not stop_fails:
            status["state"] = "STOPPED"

    runtime = SupervisedDevelopmentRuntime(
        SimpleNamespace(next=source_failure), controller, guardian=lambda: dict(status), stop=stop
    )
    error = module.LifecycleError if stop_fails else OSError
    with pytest.raises(error, match="emergency_stop_unconfirmed" if stop_fails else "observation"):
        runtime.run_once()
    assert calls == ["STOP"]
    assert controller.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    controller.close()
