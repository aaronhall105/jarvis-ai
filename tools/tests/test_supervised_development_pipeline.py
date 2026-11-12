from __future__ import annotations

import time
import uuid

import pytest

from tools import supervised_development_controller as controller_module
from tools.supervised_development_controller import LifecycleError, SupervisedDevelopmentController
from tools.supervised_development_pipeline import (
    GovernorAdapter,
    GovernorTask,
    SupervisedDevelopmentPipeline,
)


SHA = "a" * 40


class Worker:
    def run(self, task: GovernorTask, *, call_ledger):
        call_ledger.reserve("fixture-provider-call", 0.02)
        call_ledger.completed("fixture-provider-call", 0.01)
        return {
            "proposal": task.objective,
            "diff_sha256": "b" * 64,
            "tests": {"passed": 2},
            "security": {"passed": True},
            "changed_files": list(task.allowed_paths),
        }


class Reviewer:
    def review(self, task: GovernorTask, evidence):
        assert evidence["proposal"] == task.objective
        return {"verified": True, "checks": ["semantic", "scope", "security"]}


def task() -> GovernorTask:
    return GovernorTask(
        objective="Improve a bounded diagnostic.",
        source_sha=SHA,
        allowed_paths=("tools/example.py", "tools/tests/test_example.py"),
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=0.09,
        deadline_seconds=120,
        owner_id="owner",
        request_ref="owner-request-1",
    )


def admission(request):
    return {
        **request,
        "status": "approved",
        "authority": "jarvis-autonomy-broker",
        "expires_at": int(time.time()) + 60,
        "guardian_generation": str(uuid.uuid4()),
    }


def test_approved_task_flows_to_review_required(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, "_request_protected_admission", admission)
    controller = SupervisedDevelopmentController(tmp_path / "state.sqlite3")
    task_value = task()
    pipeline = SupervisedDevelopmentPipeline(controller, Worker(), Reviewer())
    task_id = pipeline.submit(task_value)
    snapshot = pipeline.execute(task_id, task_value, current_base_sha=SHA)
    assert snapshot["state"] == "REVIEW_REQUIRED"
    assert snapshot["provider_calls"] == 1
    assert snapshot["evidence"]["independent_review"]["verified"] is True


def test_source_drift_stops_before_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, "_request_protected_admission", admission)
    controller = SupervisedDevelopmentController(tmp_path / "state.sqlite3")
    worker = Worker()
    pipeline = SupervisedDevelopmentPipeline(controller, worker, Reviewer())
    task_id = pipeline.submit(task())
    with pytest.raises(LifecycleError, match="source_drift_before_admission"):
        pipeline.execute(task_id, task(), current_base_sha="c" * 40)
    assert controller.snapshot(task_id)["state"] == "APPROVAL_REQUESTED"


def test_untrusted_attestation_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        controller_module,
        "_request_protected_admission",
        lambda request: {**admission(request), "authority": "caller"},
    )
    controller = SupervisedDevelopmentController(tmp_path / "state.sqlite3")
    pipeline = SupervisedDevelopmentPipeline(controller, Worker(), Reviewer())
    task_id = pipeline.submit(task())
    with pytest.raises(LifecycleError, match="untrusted_admission_authority"):
        pipeline.execute(task_id, task(), current_base_sha=SHA)
    assert controller.snapshot(task_id)["provider_calls"] == 0


def test_parameter_tampering_between_admission_and_transition_fails_closed(tmp_path, monkeypatch):
    controller = SupervisedDevelopmentController(tmp_path / "state.sqlite3")

    def mutate_then_attest(request):
        controller.db.execute(
            "UPDATE tasks SET parameters=? WHERE id=?",
            ('{"base_sha":"' + SHA + '","goal":"changed"}', request["task_id"]),
        )
        return admission(request)

    monkeypatch.setattr(controller_module, "_request_protected_admission", mutate_then_attest)
    pipeline = SupervisedDevelopmentPipeline(controller, Worker(), Reviewer())
    task_id = pipeline.submit(task())
    with pytest.raises(LifecycleError, match="execution_parameters_changed"):
        pipeline.execute(task_id, task(), current_base_sha=SHA)
    assert controller.snapshot(task_id)["state"] == "APPROVAL_REQUESTED"


def test_worker_failure_becomes_reconciliation_required(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, "_request_protected_admission", admission)

    class FailingWorker:
        def run(self, task, *, call_ledger):
            call_ledger.reserve("uncertain-fixture-call", 0.02)
            call_ledger.uncertain("uncertain-fixture-call")
            raise OSError("lost worker acknowledgement")

    controller = SupervisedDevelopmentController(tmp_path / "state.sqlite3")
    pipeline = SupervisedDevelopmentPipeline(controller, FailingWorker(), Reviewer())
    task_id = pipeline.submit(task())
    with pytest.raises(OSError):
        pipeline.execute(task_id, task(), current_base_sha=SHA)
    assert controller.snapshot(task_id)["state"] == "RECONCILIATION_REQUIRED"


def test_review_failure_after_completed_call_cannot_leave_task_running(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, "_request_protected_admission", admission)

    class FailingReviewer:
        def review(self, task, evidence):
            raise OSError("reviewer unavailable")

    controller = SupervisedDevelopmentController(tmp_path / "state.sqlite3")
    pipeline = SupervisedDevelopmentPipeline(controller, Worker(), FailingReviewer())
    task_id = pipeline.submit(task())
    with pytest.raises(OSError):
        pipeline.execute(task_id, task(), current_base_sha=SHA)
    assert controller.snapshot(task_id)["state"] == "RECONCILIATION_REQUIRED"


def test_governor_adapter_binds_bounded_policy():
    selected = GovernorAdapter.from_candidate(
        {"candidate_id": 7, "status": "queued", "summary": "Improve diagnostic counts."},
        owner_id="owner",
        request_ref="request",
        source_sha=SHA,
        allowed_paths=("tools/example.py",),
    )
    assert selected.max_calls == 1
    assert selected.max_cost_usd == 1.0
    assert selected.deadline_seconds == 120


@pytest.mark.parametrize("summary", ["Deploy the release", "Read the production secret"])
def test_governor_adapter_rejects_unsafe_objective(summary):
    with pytest.raises(LifecycleError, match="governor_objective_out_of_policy"):
        GovernorAdapter.from_candidate(
            {"candidate_id": 7, "status": "queued", "summary": summary},
            owner_id="owner",
            request_ref="request",
            source_sha=SHA,
            allowed_paths=("tools/example.py",),
        )
