from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from tools.supervised_development_controller import (
    APPROVAL_REQUESTED,
    PROPOSED,
    REVIEW_PENDING,
    LifecycleError,
    SupervisedDevelopmentController,
)

SHA = "a" * 40


def make_controller(tmp_path):
    return SupervisedDevelopmentController(tmp_path / "state" / "tasks.sqlite3")


def make_task(controller):
    return controller.create_task(
        base_sha=SHA,
        allowed_paths=["tools/example.py"],
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=0.09,
        deadline_seconds=120,
        goal="Improve a bounded diagnostic.",
    )


def request_approval(controller, task, owner="owner"):
    params = controller.snapshot(task)["parameters"]
    digest = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    controller.record_approval_request(
        task, owner_id=owner, parameters_sha256=digest, request_ref="owner-request-1"
    )


def test_approval_request_is_durable_but_cannot_execute(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    request_approval(controller, task)
    assert controller.snapshot(task)["state"] == APPROVAL_REQUESTED
    with pytest.raises(LifecycleError, match="trusted_execution_admission_unavailable"):
        controller.start(task, current_base_sha=SHA)


def test_missing_or_wrong_approval_request_is_rejected(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    with pytest.raises(LifecycleError, match="trusted_execution_admission_unavailable"):
        controller.start(task, current_base_sha=SHA)
    with pytest.raises(LifecycleError, match="approval_parameters_mismatch"):
        controller.record_approval_request(
            task, owner_id="owner", parameters_sha256="b" * 64, request_ref="request"
        )


def test_scope_and_bounds_are_stored_immutably(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    original = controller.snapshot(task)["parameters"]
    request_approval(controller, task)
    assert controller.snapshot(task)["parameters"] == original
    with pytest.raises(LifecycleError):
        controller.record_approval_request(
            task, owner_id="other", parameters_sha256="b" * 64, request_ref="replacement"
        )


def test_evidence_stays_review_pending_and_cannot_authorize_execution(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    request_approval(controller, task)
    with pytest.raises(LifecycleError, match="evidence_not_allowed"):
        controller.record_evidence(task, evidence={})
    assert controller.snapshot(task)["state"] == APPROVAL_REQUESTED


def test_restart_preserves_request_and_denial(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    request_approval(controller, task)
    controller.close()
    reopened = make_controller(tmp_path)
    assert reopened.snapshot(task)["state"] == APPROVAL_REQUESTED
    with pytest.raises(LifecycleError, match="trusted_execution_admission_unavailable"):
        reopened.start(task, current_base_sha=SHA)


def test_modified_state_cannot_grant_execution(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    controller.db.execute("UPDATE tasks SET state='APPROVED' WHERE id=?", (task,))
    with pytest.raises(LifecycleError, match="trusted_execution_admission_unavailable"):
        controller.start(task, current_base_sha=SHA)
    assert controller.snapshot(task)["provider_calls"] == 0


def test_malformed_request_is_not_execution_authority(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    controller.db.execute(
        "UPDATE tasks SET state=?,approval=? WHERE id=?",
        (APPROVAL_REQUESTED, "not-json", task),
    )
    with pytest.raises(LifecycleError, match="trusted_execution_admission_unavailable"):
        controller.start(task, current_base_sha=SHA)


def test_invalid_lifecycle_transition_and_no_effects(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    with pytest.raises(LifecycleError, match="invalid_transition"):
        controller._transition(task, REVIEW_PENDING)
    assert controller.snapshot(task)["provider_calls"] == 0


def test_sensitive_request_is_rejected_atomically(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    params = controller.snapshot(task)["parameters"]
    digest = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(LifecycleError, match="sensitive_value"):
        controller.record_approval_request(
            task, owner_id="owner", parameters_sha256=digest, request_ref="token=secret"
        )
    assert controller.snapshot(task)["state"] == PROPOSED


def test_database_failure_rolls_back_approval_request(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(controller)
    controller.db.execute(
        "CREATE TRIGGER fail_request BEFORE UPDATE OF approval ON tasks "
        "BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
    )
    params = controller.snapshot(task)["parameters"]
    digest = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(sqlite3.Error):
        controller.record_approval_request(
            task, owner_id="owner", parameters_sha256=digest, request_ref="request"
        )
    assert controller.snapshot(task)["state"] == PROPOSED
    assert controller.snapshot(task)["approval"] is None


def test_no_public_execution_override_exists(tmp_path):
    controller = make_controller(tmp_path)
    assert not hasattr(controller, "owner_verifier")
    assert not hasattr(controller, "verify_independent_review")
    task = make_task(controller)
    with pytest.raises(LifecycleError):
        controller.start(task, current_base_sha=SHA)
