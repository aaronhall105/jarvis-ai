"""Manual owner review is durable; execution still requires exact signed admission."""

from __future__ import annotations

import base64
import hashlib
import os
import time
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools import supervised_development_controller as control
from tools import supervised_development_runtime as runtime
from tools.autonomy_broker import Denied, Ledger, canonical
from tools.supervised_development_broker import (
    DOMAIN,
    MANUAL_DOMAIN,
    DevelopmentAdmissionBroker,
    _validate_limits,
    _verify_signature,
)
from tools.supervised_development_controller import (
    APPROVAL_REQUESTED,
    FAILED,
    LifecycleError,
    SupervisedDevelopmentController,
    approval_current,
    manual_review_deadline,
)
from tools.supervised_development_pipeline import GovernorTask, SupervisedDevelopmentPipeline
from tools.supervised_development_product import activation_request, require_readiness
from tools.supervised_development_publication import lifecycle_view

SHA = "a" * 40


def task(*, version=2, expiry=0):
    return GovernorTask(
        objective="Improve a bounded diagnostic.",
        source_sha=SHA,
        allowed_paths=("tools/example.py",),
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=1.0,
        deadline_seconds=120,
        owner_id="aaron",
        request_ref=str(uuid.uuid4()),
        candidate_id=81,
        guardian_generation=str(uuid.uuid4()),
        expires_at=expiry,
        approval_version=version,
    )


class NoExecution:
    def run(self, *args, **kwargs):
        pytest.fail("an owner-review test must not execute a provider")

    def review(self, *args, **kwargs):
        pytest.fail("an owner-review test must not run verification")


def submit(tmp_path, value=None):
    value = value or task()
    controller = SupervisedDevelopmentController(tmp_path / "tasks.sqlite3")
    task_id = SupervisedDevelopmentPipeline(controller, NoExecution(), NoExecution()).submit(value)
    return controller, task_id, value


@pytest.mark.parametrize("now", [1, 1_800_000_000, 9_000_000_000])
def test_v2_has_no_wall_clock_review_deadline(now):
    assert approval_current(2, 0, now=now)
    assert manual_review_deadline(2, 0)
    assert not approval_current(1, 0, now=now)
    assert not approval_current(1, now, now=now)
    assert approval_current(1, now + 1, now=now)


@pytest.mark.parametrize(
    ("version", "expiry"),
    [(True, 0), (2, False), (2, 0.0), (2, None), (2, -1), (2, 1), (3, 0), (None, 0), ("2", 0)],
)
def test_manual_deadline_is_explicit_and_strict(version, expiry):
    assert not manual_review_deadline(version, expiry)
    assert not approval_current(version, expiry, now=1_800_000_000)


def test_manual_task_remains_pending_after_days_and_database_reopen(tmp_path, monkeypatch):
    controller, task_id, value = submit(tmp_path)
    assert controller.pending_owner_task()["version"] == 2
    deadline = int(time.time()) + 30 * 86400
    assert controller.expire_stale_approval_requests(now=deadline) == []
    database = controller.database
    controller.close()
    monkeypatch.setattr(control, "_now", lambda: deadline)
    reader = SupervisedDevelopmentController(database, read_only=True)
    try:
        envelope = reader.pending_owner_task()
        assert envelope["task_id"] == task_id
        assert envelope["expires_at"] == 0
        assert envelope["version"] == 2
        assert envelope["max_calls"] == value.max_calls
        assert envelope["max_cost_usd"] == value.max_cost_usd
        assert envelope["deadline_seconds"] == value.deadline_seconds
        view = lifecycle_view(reader)
        assert view["status"] == "approval_requested"
        assert view["provider_calls"] == 0
        assert view["approval_accepted"] is False
    finally:
        reader.close()


def test_old_expired_task_is_never_upgraded_or_revived(tmp_path, monkeypatch):
    controller, task_id, _ = submit(tmp_path, task(version=1, expiry=int(time.time()) + 10))
    try:
        original = controller.snapshot(task_id)["approval"]
        monkeypatch.setenv("JARVIS_SUPERVISED_REVIEW_MODE", "manual")
        assert controller.expire_stale_approval_requests(now=original["expires_at"] + 1) == [
            task_id
        ]
        snapshot = controller.snapshot(task_id)
        assert snapshot["state"] == FAILED
        assert snapshot["approval"] == original
        assert controller.pending_owner_task() is None
    finally:
        controller.close()


def signed_manual(envelope):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    body = {k: v for k, v in envelope.items() if k not in {"goal", "request_ref"}}
    body["key_id"] = key_id
    body["request_id"] = envelope["request_ref"]
    body["signature"] = base64.b64encode(
        key.sign(MANUAL_DOMAIN + canonical(body), ec.ECDSA(hashes.SHA256()))
    ).decode()
    return body, {key_id: public}


def test_v2_requires_its_own_signature_domain_and_enrolled_key(tmp_path):
    controller, _, _ = submit(tmp_path)
    try:
        body, keys = signed_manual(controller.pending_owner_task())
        assert _verify_signature(body, keys)["version"] == 2
        _validate_limits(body)
        assert DOMAIN != MANUAL_DOMAIN
        with pytest.raises(Denied):
            _verify_signature({**body, "version": 1}, keys)
        with pytest.raises(Denied):
            _verify_signature(body, {})
        for malformed in ({**body, "expires_at": 1}, {**body, "expires_at": False}):
            with pytest.raises(Denied):
                _validate_limits(malformed)
    finally:
        controller.close()


def manual_broker(tmp_path, state, controller):
    body, keys = signed_manual(controller.pending_owner_task())
    ledger = Ledger(tmp_path / "admission.sqlite3", os.getuid())
    broker = DevelopmentAdmissionBroker(
        ledger,
        relay_uid=1000,
        worker_uid=1001,
        public_keys=keys,
        guardian=lambda: dict(state),
    )
    broker.approve(canonical(body), 1000)
    request = {
        k: body[k]
        for k in (
            "task_id",
            "source_sha",
            "parameters_sha256",
            "allowed_paths",
            "provider",
            "model",
            "max_calls",
            "max_cost_usd",
            "deadline_seconds",
        )
    }
    request["approval_id"] = body["request_id"]
    return broker, ledger, request


def test_manual_approval_still_needs_activation_and_cannot_cross_guardian_cycle(tmp_path):
    controller, _, value = submit(tmp_path)
    state = {"trusted": True, "state": "STOPPED", "generation": value.guardian_generation}
    broker, ledger, request = manual_broker(tmp_path, state, controller)
    try:
        with pytest.raises(Denied, match="guardian_not_active"):
            broker.admit(request, 1001)
        state.update(
            state="ACTIVE",
            previous_generation=value.guardian_generation,
            generation=str(uuid.uuid4()),
        )
        receipt = broker.admit(request, 1001)
        assert receipt["approval_version"] == 2
        assert receipt["expires_at"] == 0
        with pytest.raises(Denied, match="mismatch"):
            broker.admit({**request, "max_calls": 2}, 1001)
        state.update(generation=str(uuid.uuid4()), previous_generation=str(uuid.uuid4()))
        with pytest.raises(Denied):
            broker.admit(request, 1001)
    finally:
        ledger.close()
        controller.close()


def test_execution_admission_rejects_manual_receipt_for_legacy_task():
    request = {
        "task_id": str(uuid.uuid4()),
        "approval_id": str(uuid.uuid4()),
        "source_sha": SHA,
        "parameters_sha256": "b" * 64,
        "allowed_paths": ["tools/example.py"],
        "provider": "openai",
        "model": "gpt-5.3-codex",
        "max_calls": 1,
        "max_cost_usd": 1.0,
        "deadline_seconds": 120,
    }
    receipt = {
        **request,
        "status": "approved",
        "authority": "jarvis-autonomy-broker",
        "expires_at": 0,
        "approval_version": 2,
        "guardian_generation": str(uuid.uuid4()),
    }
    control._validate_admission_response(receipt, request, approval_version=2)
    with pytest.raises(LifecycleError):
        control._validate_admission_response(receipt, request, approval_version=1)


def test_no_review_deadline_does_not_remove_runtime_or_spending_limits(tmp_path, monkeypatch):
    controller, task_id, value = submit(tmp_path)
    state = {
        "trusted": True,
        "state": "ACTIVE",
        "generation": str(uuid.uuid4()),
        "previous_generation": value.guardian_generation,
    }
    broker, ledger, _ = manual_broker(tmp_path, state, controller)
    monkeypatch.setattr(
        control, "_request_protected_admission", lambda req: broker.admit(req, 1001)
    )
    try:
        with pytest.raises(LifecycleError, match="source_drift"):
            controller.start(task_id, current_base_sha="c" * 40)
        controller.start(task_id, current_base_sha=SHA)
        with pytest.raises(LifecycleError, match="spending_budget"):
            controller.reserve_provider_call(
                task_id, call_id="too-expensive", reserved_cost_usd=1.01
            )
        started = controller.db.execute(
            "SELECT started_at FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0]
        monkeypatch.setattr(control, "_now", lambda: started + 121)
        with pytest.raises(LifecycleError, match="deadline_expired"):
            controller.reserve_provider_call(task_id, call_id="too-late", reserved_cost_usd=0.01)
        assert controller.snapshot(task_id)["provider_calls"] == 0
    finally:
        ledger.close()
        controller.close()


def test_activation_preview_retains_freshness_and_exact_task_binding(tmp_path):
    controller, task_id, value = submit(tmp_path)
    state = {"trusted": True, "state": "STOPPED", "generation": value.guardian_generation}
    _, ledger, _ = manual_broker(tmp_path, state, controller)
    try:
        before = int(time.time())
        preview = activation_request(controller, task_id, state, ledger.db)
        assert preview["version"] == 1
        assert before < preview["expires_at"] <= int(time.time()) + 300
        assert preview["task_id"] == task_id
        assert preview["source_sha"] == SHA
        with pytest.raises(Denied):
            activation_request(
                controller, task_id, {**state, "generation": str(uuid.uuid4())}, ledger.db
            )
    finally:
        ledger.close()
        controller.close()


def test_manual_readiness_cannot_be_borrowed_by_a_timed_task(tmp_path, monkeypatch):
    from tools import supervised_development_product as product

    controller, task_id, _ = submit(tmp_path)
    snapshot = controller.snapshot(task_id)
    proof = {
        "version": 1,
        "status": "ready",
        "task_id": task_id,
        "request_id": snapshot["approval"]["request_ref"],
        "parameters_sha256": snapshot["approval"]["parameters_sha256"],
        "source_sha": SHA,
        "expires_at": 0,
        "approval_version": 2,
    }
    monkeypatch.setattr(product, "read_readiness", lambda: proof)
    try:
        assert require_readiness(controller, task_id) == proof
        with pytest.raises(Denied):
            proof["approval_version"] = 1
            require_readiness(controller, task_id)
    finally:
        controller.close()


def test_runtime_survives_human_delay_and_reconstructs_manual_task(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SUPERVISED_REVIEW_MODE", "manual")
    clock = [1_800_000_000]
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    candidate = {
        "candidate_id": 81,
        "status": "queued",
        "summary": "Improve a bounded diagnostic.",
        "evidence": {},
        "allowed_paths": ["tools/example.py"],
    }
    source = SimpleNamespace(next=lambda excluded: candidate)
    controller = SupervisedDevelopmentController(tmp_path / "runtime.sqlite3")
    generation = str(uuid.uuid4())
    state = {"state": "STOPPED", "trusted": True, "generation": generation}
    slept = []

    def pause(_seconds):
        slept.append(clock[0])
        if len(slept) == 1:
            clock[0] += 7 * 86400
        else:
            raise KeyboardInterrupt("end isolated idle rehearsal")

    instance = runtime.SupervisedDevelopmentRuntime(
        source,
        controller,
        guardian=lambda: state,
        sleep=pause,
        worker=NoExecution(),
        reviewer=NoExecution(),
        stop=lambda: pytest.fail("already-stopped review should not activate or stop services"),
    )
    monkeypatch.setattr(instance, "_source_sha", lambda: SHA)
    try:
        with pytest.raises(KeyboardInterrupt):
            instance.run_once()
        assert len(slept) == 2
        pending = controller.pending_owner_task()
        assert pending["version"] == 2 and pending["expires_at"] == 0
        restored_id, restored = instance._existing_task()
        assert restored_id == pending["task_id"]
        assert restored.approval_version == 2 and restored.expires_at == 0
        assert controller.snapshot(restored_id)["state"] == APPROVAL_REQUESTED
        assert controller.snapshot(restored_id)["provider_calls"] == 0
        assert controller.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    finally:
        controller.close()


def test_manual_mode_keeps_the_one_call_cap(tmp_path, monkeypatch):
    controller, task_id, value = submit(tmp_path)
    state = {
        "trusted": True,
        "state": "ACTIVE",
        "generation": str(uuid.uuid4()),
        "previous_generation": value.guardian_generation,
    }
    broker, ledger, _ = manual_broker(tmp_path, state, controller)
    monkeypatch.setattr(
        control, "_request_protected_admission", lambda req: broker.admit(req, 1001)
    )
    try:
        controller.start(task_id, current_base_sha=SHA)
        assert controller.reserve_provider_call(
            task_id, call_id="fixture-call", reserved_cost_usd=0.1
        )
        # Reconcile a synthetic test-only usage row; no provider is invoked.
        controller.db.execute(
            "UPDATE calls SET outcome='completed',actual_cost_usd=0.1 WHERE task_id=? AND call_id=?",
            (task_id, "fixture-call"),
        )
        with pytest.raises(LifecycleError, match="provider_budget_exhausted"):
            controller.reserve_provider_call(
                task_id, call_id="second-fixture-call", reserved_cost_usd=0.1
            )
        assert controller.snapshot(task_id)["provider_calls"] == 1
    finally:
        ledger.close()
        controller.close()
