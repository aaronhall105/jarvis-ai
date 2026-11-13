from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sqlite3
import struct
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import Denied, Ledger, canonical
from tools.supervised_development_broker import DOMAIN, DevelopmentAdmissionBroker
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_pipeline import GovernorAdapter, SupervisedDevelopmentPipeline
from tools.supervised_development_product import (
    DOMAINS,
    ProductOperator,
    activation_request,
    approved_task,
    candidate_request,
    queued_preparation,
    trial_fenced,
    verify_request,
)
from tools.supervised_development_runtime import (
    GovernorCandidateSource,
    SupervisedDevelopmentRuntime,
)
from tools.tests.test_supervised_development_runtime import _source, _governor_db, _bind_review


@pytest.fixture
def fixture(tmp_path):
    root, sha = _source(tmp_path)
    governor = tmp_path / "governor.sqlite3"
    _governor_db(governor)
    _bind_review(governor, root)
    source = GovernorCandidateSource(governor, root)
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    keys = {hashlib.sha256(public).hexdigest(): public}
    state = {"state": "STOPPED", "trusted": True, "generation": str(uuid.uuid4())}
    yield root, sha, source, controller, private, keys, state
    controller.close()
    source.close()


def sign(body, private, keys, domain=None):
    value = {**body, "key_id": next(iter(keys))}
    value["signature"] = base64.b64encode(
        private.sign(
            (domain or DOMAINS[body["action"]]) + canonical(value), ec.ECDSA(hashes.SHA256())
        )
    ).decode()
    return value


def ledger(tmp_path, name):
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    return Ledger(directory / "operations.sqlite3", owner_uid=os.getuid())


def test_real_candidate_preparation_signature_and_at_most_once_dispatch(fixture, tmp_path):
    _, _, source, controller, private, keys, state = fixture
    body = sign(candidate_request(source, 1, state), private, keys)
    calls = []
    audit = ledger(tmp_path, "operator")
    try:
        operator = ProductOperator(
            audit,
            keys,
            lambda: state,
            lambda b: (
                calls.append(b)
                or {"status": "preparation_requested", "request_id": b["request_id"]}
            ),
            lambda *_: pytest.fail("must not activate"),
        )
        first = operator.dispatch(body, 1000)
        assert operator.dispatch(body, 1000) == first
        assert len(calls) == 1
        assert queued_preparation(controller, audit.db) == body
        assert controller.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert state["state"] == "STOPPED"
        altered = {**body, "candidate_id": 2}
        with pytest.raises(Denied, match="identity_conflict"):
            operator.dispatch(altered, 1000)
    finally:
        audit.close()


@pytest.mark.parametrize(
    "change",
    ["missing_key", "signature", "scope", "expired", "generation", "wrong_domain", "source"],
)
def test_preparation_rejects_unsigned_or_altered_parameters(fixture, change):
    _, _, source, _, private, keys, state = fixture
    body = sign(candidate_request(source, 1, state), private, keys)
    if change == "missing_key":
        body["key_id"] = "0" * 64
    elif change == "signature":
        body["signature"] = "YWJj"
    elif change == "scope":
        body["allowed_paths"] = ["tools/autonomy_guardian.py"]
    elif change == "expired":
        body["expires_at"] = int(time.time()) - 1
    elif change == "generation":
        body["guardian_generation"] = str(uuid.uuid4())
    elif change == "source":
        body["source_sha"] = "a" * 40
    else:
        body = sign(
            {k: v for k, v in body.items() if k not in {"key_id", "signature"}},
            private,
            keys,
            DOMAIN,
        )
    with pytest.raises(Denied):
        verify_request(body, keys, state["generation"])


def test_no_unsigned_production_selection_and_signed_candidate_is_exact(fixture):
    root, _, source, controller, _, _, state = fixture
    runtime = SupervisedDevelopmentRuntime(
        source, controller, source_root=root, guardian=lambda: state
    )
    assert runtime.run_once(require_preparation=True) == {"status": "owner_preparation_required"}
    assert controller.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    body = candidate_request(source, 1, state)
    body["goal"] = "Different owner goal"
    with pytest.raises(Exception, match="preparation_changed"):
        runtime.run_once(preparation=body, require_preparation=True)
    assert controller.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize(
    "missing", ["credential", "pricing", "model", "budget", "environment", "prompt"]
)
def test_execution_preflight_denial_creates_no_task_or_provider_request(
    fixture, monkeypatch, missing
):
    from tools import supervised_development_runtime as runtime_module
    from tools import supervised_development_product as product

    root, _, source, controller, _, _, state = fixture
    for name in ("OPENAI_API_KEY", *runtime_module._PROVIDER_CONFIGURATION_KEYS):
        monkeypatch.delenv(name, raising=False)
    if missing != "credential":
        monkeypatch.setenv("OPENAI_API_KEY", "disposable-fixture-only")
    if missing in {"model", "budget", "environment", "prompt"}:
        monkeypatch.setenv(
            "JARVIS_SUPERVISED_PRICING_MODEL",
            "wrong-model" if missing == "model" else "gpt-5.3-codex",
        )
        rate = "100000" if missing == "budget" else "1"
        monkeypatch.setenv("JARVIS_SUPERVISED_INPUT_USD_PER_MILLION", rate)
        monkeypatch.setenv("JARVIS_SUPERVISED_OUTPUT_USD_PER_MILLION", rate)
    if missing == "prompt":
        monkeypatch.setenv("JARVIS_SUPERVISED_MAX_INPUT_TOKENS", "1")

    def unavailable_environment():
        from tools.supervised_development_environment import EnvironmentError

        if missing == "environment":
            raise EnvironmentError("worker_environment_unavailable")
        pytest.fail("preflight should deny before environment or SDK")

    monkeypatch.setattr(
        runtime_module,
        "verify_worker_environment",
        unavailable_environment,
    )
    records = []
    monkeypatch.setattr(product, "record_readiness", records.append)
    runtime = SupervisedDevelopmentRuntime(
        source, controller, source_root=root, guardian=lambda: state
    )
    body = candidate_request(source, 1, state)
    with pytest.raises(Exception, match="provider_|worker_environment_unavailable"):
        runtime.run_once(preparation=body, require_preparation=True)
    assert controller.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    assert records == [
        {
            "version": 1,
            "status": "blocked",
            "reason": "development_execution_preflight_failed",
            "request_id": body["request_id"],
            "candidate_id": 1,
            "source_sha": body["source_sha"],
            "expires_at": records[0]["expires_at"],
        }
    ]
    assert state["state"] == "STOPPED"


@pytest.mark.parametrize(
    "changed", ["task_id", "request_id", "parameters_sha256", "source_sha", "expires_at", "status"]
)
def test_activation_requires_exact_fresh_root_preflight(fixture, tmp_path, monkeypatch, changed):
    from tools import supervised_development_product as product

    _, _, _, controller, _, _, _ = fixture
    task_id, admission, _, _ = approved_fixture(fixture, tmp_path)
    snapshot = controller.snapshot(task_id)
    proof = {
        "version": 1,
        "status": "ready",
        "task_id": task_id,
        "request_id": snapshot["approval"]["request_ref"],
        "parameters_sha256": snapshot["approval"]["parameters_sha256"],
        "source_sha": snapshot["parameters"]["base_sha"],
        "expires_at": int(time.time()) + 60,
    }
    monkeypatch.setattr(product, "read_readiness", lambda: proof)
    try:
        assert product.require_readiness(controller, task_id) == proof
        proof[changed] = 0 if changed == "expires_at" else "changed"
        with pytest.raises(Denied, match="preflight_required"):
            product.require_readiness(controller, task_id)
    finally:
        admission.close()


@pytest.mark.parametrize(
    "attack", ["wrong-owner", "symlink", "permissions", "hardlink", "malformed"]
)
def test_readiness_receipt_rejects_untrusted_replacements(tmp_path, monkeypatch, attack):
    from types import SimpleNamespace
    from tools import supervised_development_product as product

    receipt = tmp_path / "development-readiness.json"
    receipt.write_text(
        "{}" if attack == "malformed" else '{"version":1,"status":"ready","expires_at":9999999999}'
    )
    receipt.chmod(0o600)
    if attack == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(receipt)
        receipt = link
    original_stat = os.fstat

    def metadata(fd):
        st = original_stat(fd)
        return SimpleNamespace(
            st_mode=st.st_mode | (0o020 if attack == "permissions" else 0),
            st_uid=1000 if attack == "wrong-owner" else 0,
            st_nlink=2 if attack == "hardlink" else 1,
            st_size=st.st_size,
        )

    monkeypatch.setattr(product, "READINESS_RECEIPT", receipt)
    monkeypatch.setattr(product.os, "fstat", metadata)
    with pytest.raises((Denied, OSError)):
        product.read_readiness()


def approved_fixture(fixture, tmp_path):
    _, sha, _, controller, private, keys, state = fixture
    task = GovernorAdapter.from_candidate(
        {"candidate_id": 1, "status": "queued", "summary": "Improve useful response delivery"},
        owner_id="owner",
        request_ref=str(uuid.uuid4()),
        source_sha=sha,
        allowed_paths=("tools/developer_session_diagnostics.py",),
        guardian_generation=state["generation"],
        expires_at=int(time.time()) + 90,
    )
    task_id = SupervisedDevelopmentPipeline(controller, None, None).submit(task)
    pending = controller.pending_owner_task()
    audit = ledger(tmp_path, "admission")
    broker = DevelopmentAdmissionBroker(
        audit, relay_uid=0, worker_uid=600, public_keys=keys, guardian=lambda: state
    )
    unsigned = {
        ("request_id" if key == "request_ref" else key): value
        for key, value in pending.items()
        if key != "goal"
    }
    signed = sign(unsigned, private, keys, DOMAIN)
    return task_id, audit, broker, signed


def test_activation_requires_real_signed_approval_and_separate_signature(fixture, tmp_path):
    _, _, _, controller, private, keys, state = fixture
    task_id, audit, broker, signed = approved_fixture(fixture, tmp_path)
    try:
        with pytest.raises(Denied, match="owner_approval_required"):
            activation_request(controller, task_id, state, audit.db)
        broker.approve(canonical(signed), 0)
        pending = approved_task(controller, task_id, audit.db)
        request = activation_request(controller, task_id, state, audit.db)
        assert request["approval_id"] == pending["request_ref"]
        activation = sign(request, private, keys)
        verify_request(activation, keys, state["generation"])
        wrong = sign(request, private, keys, DOMAIN)
        with pytest.raises(Denied, match="signature"):
            verify_request(wrong, keys, state["generation"])
        assert state["state"] == "STOPPED"
        assert (
            audit.db.execute("SELECT status FROM development_admissions").fetchone()[0]
            == "approved"
        )
        state["generation"] = str(uuid.uuid4())
        with pytest.raises(Denied, match="stale_generation"):
            activation_request(controller, task_id, state, audit.db)
    finally:
        audit.close()


def test_generation_fence_denies_unrelated_legacy_operations_after_activation(fixture, tmp_path):
    _, _, source, _, private, keys, state = fixture
    preparation = candidate_request(source, 1, state)
    body = {
        "version": 1,
        "action": "activate_development",
        "request_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "approval_id": str(uuid.uuid4()),
        "source_sha": preparation["source_sha"],
        "parameters_sha256": "a" * 64,
        "guardian_generation": state["generation"],
        "expires_at": int(time.time()) + 60,
    }
    signed = sign(body, private, keys)
    audit = ledger(tmp_path, "operator")

    def activation(_body, _actor):
        state.update(
            state="ACTIVE", previous_generation=state["generation"], generation=str(uuid.uuid4())
        )
        return {"status": "active"}

    try:
        operator = ProductOperator(audit, keys, lambda: state, lambda _: pytest.fail(), activation)
        operator.dispatch(signed, 1000)
        assert trial_fenced(audit.db, state)
        assert operator.dispatch(signed, 1000) == {"status": "active"}
        state.update(state="STOPPED", generation=str(uuid.uuid4()))
        assert not trial_fenced(audit.db, state)
    finally:
        audit.close()


def test_task_activation_cannot_admit_another_stored_approval(fixture, tmp_path, monkeypatch):
    from tools import supervised_development_product as product

    _, _, _, controller, private, keys, state = fixture
    task_id, admission, broker, signed = approved_fixture(fixture, tmp_path)
    broker.approve(canonical(signed), 0)
    activation = sign(activation_request(controller, task_id, state, admission.db), private, keys)
    audit = ledger(tmp_path, "operator")

    def activate(_body, _actor):
        state.update(
            state="ACTIVE", previous_generation=state["generation"], generation=str(uuid.uuid4())
        )
        return {"status": "active"}

    try:
        ProductOperator(audit, keys, lambda: state, lambda _: pytest.fail(), activate).dispatch(
            activation, 1000
        )
        monkeypatch.setattr(
            product,
            "readonly_ledger",
            lambda _: sqlite3.connect(
                audit.path if hasattr(audit, "path") else tmp_path / "operator/operations.sqlite3"
            ),
        )
        broker.activation_guard = product.admission_activation_fence
        request = {
            key: signed[key]
            for key in (
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
        request["approval_id"] = signed["request_id"]
        other = {k: v for k, v in signed.items() if k not in {"key_id", "signature"}}
        other.update(task_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
        broker.approve(canonical(sign(other, private, keys, DOMAIN)), 0)
        with pytest.raises(Denied, match="activation_mismatch"):
            broker.admit(
                {**request, "task_id": other["task_id"], "approval_id": other["request_id"]}, 600
            )
        assert admission.db.execute(
            "SELECT status,receipt FROM development_admissions WHERE approval_id=?",
            (other["request_id"],),
        ).fetchone() == ("approved", None)
        assert broker.admit(request, 600)["task_id"] == task_id
    finally:
        audit.close()
        admission.close()


def test_integration_service_real_peer_rejected_before_publishing(monkeypatch):
    from tools import supervised_development_integration_service as service

    left, right = socket.socketpair()
    monkeypatch.setattr(
        service, "publish", lambda *_: pytest.fail("untrusted peer must not publish")
    )
    try:
        service.serve_connection(left, gateway_uid=os.getuid() + 100)
        size = struct.unpack("!I", right.recv(4))[0]
        assert json.loads(right.recv(size)) == {"ok": False, "error": "integration_peer_denied"}
    finally:
        left.close()
        right.close()


def test_preparation_notification_failure_retries_only_notification(fixture, tmp_path):
    _, _, source, controller, private, keys, state = fixture
    body = sign(candidate_request(source, 1, state), private, keys)
    audit = ledger(tmp_path, "operator")
    effects, notifications = [], []

    def notify():
        notifications.append(True)
        assert queued_preparation(controller, audit.db) == body
        if len(notifications) == 1:
            raise Denied("development_controller_start_failed")

    try:
        operator = ProductOperator(
            audit,
            keys,
            lambda: state,
            lambda value: (
                effects.append(value)
                or {"status": "preparation_requested", "request_id": value["request_id"]}
            ),
            lambda *_: pytest.fail(),
            notify,
        )
        with pytest.raises(Denied, match="controller_start_failed"):
            operator.dispatch(body, 1000)
        assert operator.dispatch(body, 1000)["status"] == "preparation_requested"
        assert len(effects) == 1 and len(notifications) == 2
        assert state["state"] == "STOPPED"
    finally:
        audit.close()


def test_post_activation_observation_error_authoritatively_stops(fixture, tmp_path, monkeypatch):
    from tools import supervised_development_product as product
    from tools import autonomy_broker_host

    root, _, _, controller, private, keys, state = fixture
    task_id, admission, broker, signed = approved_fixture(fixture, tmp_path)
    broker.approve(canonical(signed), 0)
    body = sign(activation_request(controller, task_id, state, admission.db), private, keys)
    monkeypatch.setattr(product, "CONTROLLER_DATABASE", controller.database)
    monkeypatch.setattr(product, "require_readiness", lambda *_: {})
    admission_path = tmp_path / "admission/operations.sqlite3"
    monkeypatch.setattr(product, "readonly_ledger", lambda _: sqlite3.connect(admission_path))
    monkeypatch.setattr(autonomy_broker_host, "CANONICAL", root)
    operator_ledger = ledger(tmp_path, "operator")

    class Guardian:
        resumed = False
        stopped = False

        def status(self):
            if self.resumed and not self.stopped:
                raise OSError("fixture observation failure")
            return state

        def resume(self, *_args, **_kwargs):
            self.resumed = True
            state["state"] = "ACTIVE"

        def stop(self, _actor):
            self.stopped = True
            state["state"] = "STOPPED"

    guardian = Guardian()
    try:
        operator = product.production_operator(operator_ledger, keys, guardian)
        with pytest.raises(OSError, match="observation failure"):
            operator.dispatch(body, 1000)
        assert guardian.stopped and state["state"] == "STOPPED"
        assert operator_ledger.db.execute("SELECT status FROM calls").fetchone()[0] == "uncertain"
    finally:
        operator_ledger.close()
        admission.close()


def test_startup_failure_before_run_once_confirms_stop(monkeypatch):
    from tools import supervised_development_runtime as runtime

    state = {"state": "ACTIVE", "trusted": True}
    stopped = []
    monkeypatch.setattr(
        runtime, "_main", lambda: (_ for _ in ()).throw(OSError("fixture DB unavailable"))
    )
    monkeypatch.setattr(runtime, "guardian_status", lambda: state)
    monkeypatch.setattr(
        runtime, "emergency_stop", lambda: stopped.append(True) or state.update(state="STOPPED")
    )
    with pytest.raises(OSError, match="DB unavailable"):
        runtime.main()
    assert stopped == [True] and state["state"] == "STOPPED"


def test_owner_enhancement_uses_original_evidence_exact_blob_and_literal_anchor(fixture):
    root, sha, source, _, _, _, _ = fixture
    source.close()
    request_id = str(uuid.uuid4())
    evidence = {
        "source": {
            "kind": "owner_enhancement",
            "actor": "aaron",
            "request_id": request_id,
            "request": "Make the useful response arrive sooner",
        },
        "confirmed_defect": False,
    }
    path = "tools/tests/test_developer_session_diagnostics.py"
    scope = {
        "scope": "application",
        "kind": "owner_enhancement_proposal",
        "affected_files": [path],
        "source_sha": sha,
        "evidence_sha256": hashlib.sha256(canonical(evidence)).hexdigest(),
        "evidence_failure_id": 1,
        "owner_request_id": request_id,
        "acceptance_criteria": ["Preserve all regression checks and exact output"],
        "source_findings": {
            path: {
                "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                "anchor": "def test_value():",
            }
        },
        "proposed_by": "aaron",
        "confirmed_defect": False,
    }
    with sqlite3.connect(source.database) as writer:
        writer.execute("UPDATE improvement_failures SET evidence_json=?", (json.dumps(evidence),))
        writer.execute(
            "UPDATE improvement_candidates SET development_scope_json=?", (json.dumps(scope),)
        )
    reader = GovernorCandidateSource(source.database, root)
    try:
        assert reader.next()["candidate_id"] == 1
        before = reader.db.execute("SELECT evidence_json FROM improvement_failures").fetchone()[0]
        scope["source_findings"][path]["sha256"] = "0" * 64
        with sqlite3.connect(source.database) as writer:
            writer.execute(
                "UPDATE improvement_candidates SET development_scope_json=?", (json.dumps(scope),)
            )
        assert reader.next() is None
        assert (
            reader.db.execute("SELECT evidence_json FROM improvement_failures").fetchone()[0]
            == before
        )
    finally:
        reader.close()
