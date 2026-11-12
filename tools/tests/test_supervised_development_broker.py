from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import socket
import sqlite3
import struct
import threading
import time
import uuid
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools import supervised_development_controller as controller_module
from tools.autonomy_broker import Denied, Ledger, canonical
from tools.supervised_development_broker import (
    DOMAIN,
    DevelopmentAdmissionBroker,
    serve_admission_connection,
)
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_pipeline import (
    GovernorAdapter,
    GovernorTask,
    SupervisedDevelopmentPipeline,
)


SHA = "a" * 40


def approved_request(tmp_path, status, *, max_calls=2):
    broker, ledger, key, key_id, _ = make_broker(tmp_path, lambda: status)
    task = replace(make_task(), max_calls=max_calls)
    approval, _ = signed_approval(str(uuid.uuid4()), task, "reviewed", "a" * 64)
    approval["key_id"] = key_id
    approval["guardian_generation"] = status.get("previous_generation", str(uuid.uuid4()))
    unsigned = {key: value for key, value in approval.items() if key != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(unsigned), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.approve(canonical(approval), 1000)
    request = {
        name: unsigned[name]
        for name in (
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
    request["approval_id"] = unsigned["request_id"]
    return broker, ledger, request


def test_receipt_retry_cannot_cross_stop_or_activation_cycle(tmp_path):
    status = {
        "state": "ACTIVE",
        "trusted": True,
        "generation": str(uuid.uuid4()),
        "previous_generation": str(uuid.uuid4()),
    }
    broker, ledger, request = approved_request(tmp_path, status)
    receipt = broker.admit(request, 1001)
    assert receipt["max_calls"] == 2
    assert ledger.verify_audit() == 2
    status["state"] = "STOPPED"
    with pytest.raises(Denied, match="guardian_not_active"):
        broker.admit(request, 1001)
    status["state"] = "ACTIVE"
    status["generation"] = str(uuid.uuid4())
    with pytest.raises(Denied, match="stale_cycle"):
        broker.admit(request, 1001)
    assert ledger.verify_audit() == 2
    ledger.close()


@pytest.mark.parametrize(
    "observation",
    [
        None,
        {},
        {"state": "ACTIVE", "trusted": True, "generation": "malformed"},
        {"state": "STOPPED", "trusted": True},
        "throws",
    ],
)
def test_uncertain_guardian_never_persists_admission(tmp_path, observation):
    status = {
        "state": "ACTIVE",
        "trusted": True,
        "generation": str(uuid.uuid4()),
        "previous_generation": str(uuid.uuid4()),
    }
    broker, ledger, request = approved_request(tmp_path, status)

    def observe():
        if observation == "throws":
            raise OSError("fixture observation unavailable")
        return observation

    broker.guardian = observe
    with pytest.raises(Denied, match="guardian_not_active"):
        broker.admit(request, 1001)
    assert ledger.db.execute("SELECT status,receipt FROM development_admissions").fetchone() == (
        "approved",
        None,
    )
    assert ledger.verify_audit() == 1
    ledger.close()


def signed_approval(task_id: str, task: GovernorTask, approval_id: str, parameters_hash: str):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    body = {
        "version": 1,
        "action": "approve_development",
        "key_id": key_id,
        "request_id": approval_id,
        "task_id": task_id,
        "source_sha": task.source_sha,
        "parameters_sha256": parameters_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": task.max_calls,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": task.deadline_seconds,
        "expires_at": int(time.time()) + 60,
        "guardian_generation": str(uuid.uuid4()),
    }
    body["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(body), ec.ECDSA(hashes.SHA256()))
    ).decode()
    return body, {key_id: public}


def make_task() -> GovernorTask:
    return GovernorTask(
        objective="Improve a bounded diagnostic.",
        source_sha=SHA,
        allowed_paths=("tools/example.py",),
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=0.09,
        deadline_seconds=120,
        owner_id="owner",
        request_ref="approval-1",
    )


@pytest.mark.parametrize("model", ["", " model", "model\n", "../model", "x" * 129])
def test_signed_approval_rejects_noncanonical_model_before_recording(tmp_path, model):
    body, keys = signed_approval("task", replace(make_task(), model=model), "approval", "a" * 64)
    ledger = Ledger(tmp_path / "model.sqlite3", os.getuid())
    broker = DevelopmentAdmissionBroker(
        ledger,
        relay_uid=1000,
        worker_uid=1001,
        public_keys=keys,
        guardian=lambda: {"state": "STOPPED", "trusted": True},
    )
    try:
        with pytest.raises(Denied, match="development_approval_limits"):
            broker.approve(canonical(body), 1000)
        assert ledger.db.execute("SELECT COUNT(*) FROM development_admissions").fetchone()[0] == 0
        assert ledger.verify_audit() == 0
    finally:
        ledger.close()


def test_signed_approval_accepts_exact_configurable_model_without_activation(tmp_path):
    task = replace(make_task(), model="approved-model_1:bounded")
    body, keys = signed_approval("task", task, "approval", "a" * 64)
    ledger = Ledger(tmp_path / "model.sqlite3", os.getuid())
    broker = DevelopmentAdmissionBroker(
        ledger,
        relay_uid=1000,
        worker_uid=1001,
        public_keys=keys,
        guardian=lambda: {"state": "STOPPED", "trusted": True},
    )
    try:
        assert broker.approve(canonical(body), 1000)["status"] == "approved"
        stored, status, receipt = ledger.db.execute(
            "SELECT payload,status,receipt FROM development_admissions"
        ).fetchone()
        assert json.loads(stored)["model"] == task.model
        assert status == "approved"
        assert receipt is None
    finally:
        ledger.close()


def make_broker(tmp_path, guardian):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    ledger = Ledger(root / "broker.sqlite3", os.getuid())
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    broker = DevelopmentAdmissionBroker(
        ledger,
        relay_uid=1000,
        worker_uid=1001,
        public_keys={key_id: public},
        guardian=guardian,
    )
    return broker, ledger, key, key_id, public


def test_signed_owner_approval_admits_exact_task(tmp_path):
    generation = str(uuid.uuid4())
    active_generation = str(uuid.uuid4())
    guardian = lambda: {
        "state": "ACTIVE",
        "trusted": True,
        "generation": active_generation,
        "previous_generation": generation,
    }
    broker, ledger, key, key_id, public = make_broker(tmp_path, guardian)
    task = make_task()
    task_id = str(uuid.uuid4())
    params_hash = hashlib.sha256(
        canonical(
            {
                "base_sha": task.source_sha,
                "allowed_paths": sorted(task.allowed_paths),
                "provider": task.provider,
                "model": task.model,
                "max_calls": task.max_calls,
                "max_cost_usd": task.max_cost_usd,
                "deadline_seconds": task.deadline_seconds,
                "goal": task.objective,
            }
        )
    ).hexdigest()
    approval, _ = signed_approval(task_id, task, "approval-1", params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = generation
    signed = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(signed), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.public_keys = {key_id: public}
    broker.approve(canonical(approval), 1000)
    request = {
        "task_id": task_id,
        "approval_id": "approval-1",
        "source_sha": task.source_sha,
        "parameters_sha256": params_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": task.max_calls,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": task.deadline_seconds,
    }
    receipt = broker.admit(request, 1001)
    assert receipt["authority"] == "jarvis-autonomy-broker"
    assert receipt["guardian_generation"] == active_generation
    assert broker.admit(request, 1001) == receipt
    assert ledger.verify_audit() == 2


def test_signed_approval_from_prior_guardian_cycle_is_rejected(tmp_path):
    signed_generation = str(uuid.uuid4())
    active_generation = str(uuid.uuid4())
    status = {
        "state": "ACTIVE",
        "trusted": True,
        "generation": active_generation,
        "previous_generation": signed_generation,
    }
    broker, ledger, key, key_id, public = make_broker(tmp_path, lambda: status)
    task = make_task()
    task_id = str(uuid.uuid4())
    params_hash = hashlib.sha256(
        canonical(
            {
                "base_sha": task.source_sha,
                "allowed_paths": sorted(task.allowed_paths),
                "provider": task.provider,
                "model": task.model,
                "max_calls": task.max_calls,
                "max_cost_usd": task.max_cost_usd,
                "deadline_seconds": task.deadline_seconds,
                "goal": task.objective,
            }
        )
    ).hexdigest()
    approval, _ = signed_approval(task_id, task, "approval-stale", params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = signed_generation
    unsigned = {key: value for key, value in approval.items() if key != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(unsigned), ec.ECDSA(hashes.SHA256()))
    ).decode()
    assert broker.approve(canonical(approval), 1000)["status"] == "approved"
    request = {
        "task_id": task_id,
        "approval_id": "approval-stale",
        "source_sha": task.source_sha,
        "parameters_sha256": params_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": task.max_calls,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": task.deadline_seconds,
    }
    assert broker.admit(request, 1001)["guardian_generation"] == active_generation

    # A later STOP -> ACTIVE cycle must not accept the old signed generation.
    status.update(
        generation=str(uuid.uuid4()),
        previous_generation=str(uuid.uuid4()),
    )
    # A fresh request signed against the old generation must fail after a
    # later STOP -> ACTIVE cycle; replaying the completed request remains
    # idempotent but cannot authorize a new admission.
    later = dict(approval)
    later["request_id"] = "approval-stale-replayed"
    unsigned = {key: value for key, value in later.items() if key != "signature"}
    later["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(unsigned), ec.ECDSA(hashes.SHA256()))
    ).decode()
    assert broker.approve(canonical(later), 1000)["status"] == "approved"
    request["approval_id"] = "approval-stale-replayed"
    with pytest.raises(Denied, match="guardian_not_active"):
        broker.admit(request, 1001)
    ledger.close()


def test_stop_denies_admission_and_invalid_signature_is_rejected(tmp_path):
    generation = str(uuid.uuid4())
    guardian_state = {"state": "STOPPED", "trusted": True, "generation": generation}
    broker, ledger, key, key_id, public = make_broker(tmp_path, lambda: dict(guardian_state))
    task = make_task()
    task_id = str(uuid.uuid4())
    params_hash = "b" * 64
    approval, _ = signed_approval(task_id, task, "approval-2", params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = generation
    signed = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(signed), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.public_keys = {key_id: public}
    broker.approve(canonical(approval), 1000)
    request = {
        "task_id": task_id,
        "approval_id": "approval-2",
        "source_sha": SHA,
        "parameters_sha256": params_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": 1,
        "max_cost_usd": 0.09,
        "deadline_seconds": 120,
    }
    with pytest.raises(Denied, match="guardian_not_active"):
        broker.admit(request, 1001)
    approval["signature"] = base64.b64encode(b"bad").decode()
    with pytest.raises(Denied, match="development_approval_signature"):
        broker.approve(canonical(approval), 1000)


def test_guardian_observation_failure_denies_without_admitted_transition(tmp_path):
    def unavailable_guardian():
        raise RuntimeError("guardian unavailable")

    broker, ledger, key, key_id, public = make_broker(tmp_path, unavailable_guardian)
    task = make_task()
    task_id = str(uuid.uuid4())
    params_hash = "b" * 64
    approval, _ = signed_approval(task_id, task, "approval-guardian-error", params_hash)
    approval["key_id"] = key_id
    unsigned = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(unsigned), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.approve(canonical(approval), 1000)
    request = {
        "task_id": task_id,
        "approval_id": "approval-guardian-error",
        "source_sha": task.source_sha,
        "parameters_sha256": params_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": task.max_calls,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": task.deadline_seconds,
    }
    with pytest.raises(Denied, match="guardian_not_active"):
        broker.admit(request, 1001)
    row = ledger.db.execute(
        "SELECT status,receipt FROM development_admissions WHERE approval_id=?",
        ("approval-guardian-error",),
    ).fetchone()
    assert row == ("approved", None)
    ledger.close()


def test_expired_owner_approval_is_not_admitted(tmp_path):
    generation = str(uuid.uuid4())
    broker, _ledger, key, key_id, public = make_broker(
        tmp_path, lambda: {"state": "ACTIVE", "trusted": True, "generation": generation}
    )
    task = make_task()
    task_id = str(uuid.uuid4())
    params_hash = hashlib.sha256(
        canonical(
            {
                "base_sha": task.source_sha,
                "allowed_paths": sorted(task.allowed_paths),
                "provider": task.provider,
                "model": task.model,
                "max_calls": task.max_calls,
                "max_cost_usd": task.max_cost_usd,
                "deadline_seconds": task.deadline_seconds,
                "goal": task.objective,
            }
        )
    ).hexdigest()
    approval, _ = signed_approval(task_id, task, "approval-expired", params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = generation
    approval["expires_at"] = int(time.time()) + 1
    signed = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(signed), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.public_keys = {key_id: public}
    broker.approve(canonical(approval), 1000)
    time.sleep(1.1)
    request = {
        "task_id": task_id,
        "approval_id": "approval-expired",
        "source_sha": task.source_sha,
        "parameters_sha256": params_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": 1,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": task.deadline_seconds,
    }
    with pytest.raises(Denied, match="development_approval_expired"):
        broker.admit(request, 1001)


def test_pipeline_uses_signed_broker_admission_and_reaches_review(tmp_path, monkeypatch):
    generation = str(uuid.uuid4())
    broker, ledger, key, key_id, public = make_broker(
        tmp_path, lambda: {"state": "ACTIVE", "trusted": True, "generation": generation}
    )
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task = make_task()

    class Worker:
        def run(self, selected, *, call_ledger):
            assert selected.objective == task.objective
            call_ledger.reserve("fixture-provider-call", 0.02)
            call_ledger.completed("fixture-provider-call", 0.01)
            return {
                "proposal": selected.objective,
                "diff_sha256": "c" * 64,
                "tests": {"passed": 1},
                "security": {"passed": True},
            }

    class Reviewer:
        def review(self, selected, evidence):
            return {"verified": True, "checks": ["diff", "tests", "security"]}

    pipeline = SupervisedDevelopmentPipeline(controller, Worker(), Reviewer())
    task_id = pipeline.submit(task)
    params_hash = pipeline._parameters_hash(task)
    approval, _ = signed_approval(task_id, task, task.request_ref, params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = generation
    signed = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(signed), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.approve(canonical(approval), 1000)
    monkeypatch.setattr(
        controller_module,
        "_request_protected_admission",
        lambda request: broker.admit(request, 1001),
    )
    result = pipeline.execute(task_id, task, current_base_sha=SHA)
    assert result["state"] == "REVIEW_REQUIRED"
    assert result["evidence"]["independent_review"]["verified"] is True


def test_complete_governor_broker_worker_review_vertical_slice(tmp_path, monkeypatch):
    from tools.supervised_development_runtime import RuntimeReview
    from tools.supervised_development_worker import BoundedDevelopmentWorker

    workspace = tmp_path / "source"
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
    target = workspace / "diagnostic.py"
    target.write_text("VALUE = 1\n")
    (workspace / "test_diagnostic.py").write_text(
        "from diagnostic import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n"
    )
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "base"], check=True)
    source_sha = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True
    ).strip()
    generation = str(uuid.uuid4())
    broker, ledger, key, key_id, public = make_broker(
        tmp_path, lambda: {"state": "ACTIVE", "trusted": True, "generation": generation}
    )
    task = GovernorAdapter.from_candidate(
        {"candidate_id": 1, "status": "queued", "summary": "Improve the diagnostic."},
        owner_id="owner",
        request_ref="approval-e2e",
        source_sha=source_sha,
        allowed_paths=("diagnostic.py",),
    )

    def provider(prompt, model, timeout):
        return (
            json.dumps(
                {
                    "protocol_version": 1,
                    "explanation": "Fix the recorded diagnostic.",
                    "edits": [
                        {
                            "path": "diagnostic.py",
                            "old_text": "VALUE = 1\n",
                            "new_text": "VALUE = 2\n",
                        }
                    ],
                }
            ),
            {"input_tokens": 100, "output_tokens": 40},
        )

    worker = BoundedDevelopmentWorker(
        workspace,
        worktree_root=tmp_path / "worktrees",
        provider_call=provider,
        input_price_per_million=10,
        output_price_per_million=10,
    )
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    pipeline = SupervisedDevelopmentPipeline(controller, worker, RuntimeReview())
    task_id = pipeline.submit(task)
    params_hash = pipeline._parameters_hash(task)
    approval, _ = signed_approval(task_id, task, task.request_ref, params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = generation
    signed = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(signed), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.approve(canonical(approval), 1000)
    monkeypatch.setattr(
        controller_module,
        "_request_protected_admission",
        lambda request: broker.admit(request, 1001),
    )
    result = pipeline.execute(task_id, task, current_base_sha=source_sha)
    assert result["state"] == "REVIEW_REQUIRED"
    assert result["evidence"]["independent_review"]["verified"] is True
    assert worker.actual_provider_calls == 1
    assert result["evidence"]["verification_receipt_sha256"]
    assert target.read_text() == "VALUE = 1\n"


def test_socket_protocol_uses_kernel_peer_credentials(tmp_path):
    generation = str(uuid.uuid4())
    broker, _ledger, key, key_id, public = make_broker(
        tmp_path, lambda: {"state": "ACTIVE", "trusted": True, "generation": generation}
    )
    broker.relay_uid = 9999
    broker.worker_uid = os.getuid()
    broker.ledger.db.close()
    broker.ledger.db = sqlite3.connect(
        tmp_path / "ledger" / "broker.sqlite3", check_same_thread=False
    )
    task = make_task()
    task_id = str(uuid.uuid4())
    params_hash = hashlib.sha256(
        canonical(
            {
                "base_sha": task.source_sha,
                "allowed_paths": sorted(task.allowed_paths),
                "provider": task.provider,
                "model": task.model,
                "max_calls": task.max_calls,
                "max_cost_usd": task.max_cost_usd,
                "deadline_seconds": task.deadline_seconds,
                "goal": task.objective,
            }
        )
    ).hexdigest()
    approval, _ = signed_approval(task_id, task, "approval-socket", params_hash)
    approval["key_id"] = key_id
    approval["guardian_generation"] = generation
    signed = {k: v for k, v in approval.items() if k != "signature"}
    approval["signature"] = base64.b64encode(
        key.sign(DOMAIN + canonical(signed), ec.ECDSA(hashes.SHA256()))
    ).decode()
    broker.public_keys = {key_id: public}
    broker.approve(canonical(approval), 9999)
    request = {
        "task_id": task_id,
        "approval_id": "approval-socket",
        "source_sha": task.source_sha,
        "parameters_sha256": params_hash,
        "allowed_paths": sorted(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": 1,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": 120,
    }
    client, server = socket.socketpair()
    _pid, peer_uid, _gid = struct.unpack(
        "3i", server.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    assert peer_uid == os.getuid()
    thread = threading.Thread(target=serve_admission_connection, args=(server, broker))
    thread.start()
    raw = canonical(request)
    client.sendall(struct.pack("!I", len(raw)) + raw)
    size = struct.unpack("!I", client.recv(4))[0]
    response = json.loads(client.recv(size))
    thread.join(timeout=2)
    client.close()
    server.close()
    assert response["authority"] == "jarvis-autonomy-broker"


def test_root_worker_identity_is_rejected(tmp_path):
    broker, _ledger, _key, _key_id, _public = make_broker(
        tmp_path, lambda: {"state": "ACTIVE", "trusted": True, "generation": str(uuid.uuid4())}
    )
    with pytest.raises(Denied, match="development_admission_peer"):
        broker.admit({}, 0)
