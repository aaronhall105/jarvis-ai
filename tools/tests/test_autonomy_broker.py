import copy
import hashlib
import json
import os
import socket
import time
import uuid
from pathlib import Path

import pytest

from tools.autonomy_broker import (
    Broker,
    Denied,
    Ledger,
    MAX_REQUEST_BYTES,
    PRODUCTION_BRANCH,
    REPOSITORY,
    REQUIRED_CHECKS,
    canonical,
    decode_request,
    peer_uid,
    sandbox_argv,
    validate_release,
    validate_request,
)


@pytest.fixture
def body():
    goal = str(uuid.uuid4())
    return {
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "goal_id": goal,
        "operation": "run_verification",
        "repository": REPOSITORY,
        "branch": "jarvis/autonomous-" + uuid.UUID(goal).hex,
        "source_sha": "a" * 40,
        "expires_at": int(time.time()) + 60,
        "guardian_generation": str(uuid.uuid4()),
        "parameters": {"profile": "verify_python", "target": "bridge/tests/test_ai.py"},
    }


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    value = Ledger(root / "broker.sqlite3", os.getuid())
    yield value
    value.close()


@pytest.fixture
def broker(ledger, body):
    status = {"state": "ACTIVE", "trusted": True, "generation": body["guardian_generation"]}
    return Broker(ledger, 995, lambda: status)


def evidence(_):
    return {"workspace_verified": True}


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "shell"},
        {"command": "bash -c true"},
        {"argv": ["docker", "run"]},
        {"repository": "other/repository"},
        {"branch": PRODUCTION_BRANCH},
        {"source_sha": "HEAD"},
        {"source_sha": "a" * 40 + ";id"},
        {"expires_at": 0},
        {"expires_at": True},
        {"goal_id": "../outside"},
        {"version": True},
        {"caller_uid": 995},
    ],
)
def test_request_strictly_rejects_untrusted_fields(body, change):
    body.update(change)
    with pytest.raises(Denied):
        validate_request(body, time.time())


@pytest.mark.parametrize(
    "params",
    [
        {"profile": "arbitrary", "target": "bridge/tests"},
        {"profile": "verify_python", "target": "../bridge/tests"},
        {"profile": "verify_python", "target": "/etc/shadow"},
        {"profile": "verify_python", "target": "bridge/tests/../../.env"},
        {"profile": "verify_python", "target": "bridge/tests/.env.production"},
        {"profile": "verify_python", "target": "bridge/tests/--override"},
        {"profile": "verify_python", "target": "bridge/tests", "image": "evil"},
        {"profile": "verify_python", "target": "bridge/tests", "args": ["--privileged"]},
    ],
)
def test_profile_and_target_allowlists(body, params):
    body["parameters"] = params
    with pytest.raises(Denied):
        validate_request(body, time.time())


@pytest.mark.parametrize(
    "raw", [b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"x" * (MAX_REQUEST_BYTES + 1)]
)
def test_bounded_json_rejects_ambiguous_or_malformed_input(raw):
    with pytest.raises(Denied):
        decode_request(raw)


def test_peer_identity_is_from_kernel():
    left, right = socket.socketpair()
    try:
        assert peer_uid(left) == os.getuid()
        assert peer_uid(right) == os.getuid()
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("uid", [0, 1000, 996, -1])
def test_wrong_peer_never_executes(broker, body, uid):
    with pytest.raises(Denied, match="unauthorized_peer"):
        broker.dispatch(canonical(body), uid, evidence, lambda *_: pytest.fail("executed"))


@pytest.mark.parametrize(
    "state",
    [
        {"state": "STOPPED", "trusted": True},
        {},
        {"state": "ACTIVE", "trusted": False},
        {"state": "ACTIVE", "trusted": True, "generation": "old"},
    ],
)
def test_guardian_fail_closed(ledger, body, state):
    broker = Broker(ledger, 995, lambda: state)
    with pytest.raises(Denied):
        broker.dispatch(canonical(body), 995, evidence, lambda *_: pytest.fail("executed"))
    assert ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


def test_workspace_evidence_required(broker, body):
    with pytest.raises(Denied, match="workspace_unverified"):
        broker.dispatch(canonical(body), 995, lambda _: {}, lambda *_: pytest.fail("executed"))


def test_duplicate_request_returns_persisted_result_once(broker, body):
    executions = []

    def execute(request, fence):
        fence()
        executions.append(request.request_id)
        return {"outcome": "passed", "exit_code": 0}

    first = broker.dispatch(canonical(body), 995, evidence, execute)
    assert broker.dispatch(canonical(body), 995, evidence, execute) == first
    assert executions == [body["request_id"]]
    assert broker.ledger.verify_audit() == 2


def test_changed_request_id_binding_is_rejected(broker, body):
    broker.dispatch(canonical(body), 995, evidence, lambda *_: {"outcome": "passed"})
    body["source_sha"] = "b" * 40
    with pytest.raises(Denied, match="request_identity_conflict"):
        broker.dispatch(canonical(body), 995, evidence, lambda *_: pytest.fail("executed twice"))


def test_completed_replay_after_expiry_stop_and_lost_workspace(broker, body, monkeypatch):
    result = broker.dispatch(canonical(body), 995, evidence, lambda *_: {"outcome": "passed"})
    broker.guardian = lambda: pytest.fail("replay must not request new authority")
    monkeypatch.setattr("tools.autonomy_broker.time.time", lambda: body["expires_at"] + 600)
    assert (
        broker.dispatch(
            canonical(body),
            995,
            lambda _: pytest.fail("workspace may already be archived"),
            lambda *_: pytest.fail("must not repeat completed work"),
        )
        == result
    )


def test_persistence_error_is_not_masked_by_second_transition(broker, body, monkeypatch):
    transitions = []

    def failed_finish(*args):
        transitions.append(args[-1])
        raise OSError("simulated durable storage failure")

    monkeypatch.setattr(broker.ledger, "finish", failed_finish)
    with pytest.raises(OSError, match="durable storage failure"):
        broker.dispatch(canonical(body), 995, evidence, lambda *_: {"outcome": "passed"})
    assert transitions == [{"outcome": "passed"}]
    assert broker.ledger.db.execute("SELECT status FROM calls").fetchone()[0] == "running"


def test_unknown_side_effect_cannot_be_replayed(broker, body):
    def crash(*_):
        raise OSError("simulated loss of acknowledgement")

    with pytest.raises(OSError):
        broker.dispatch(canonical(body), 995, evidence, crash)
    with pytest.raises(Denied, match="reconciliation_required"):
        broker.dispatch(canonical(body), 995, evidence, lambda *_: pytest.fail("replayed"))


def test_restart_marks_inflight_uncertain_and_retains_completed(tmp_path, body):
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    ledger = Ledger(root / "state.db", os.getuid())
    request = validate_request(body, time.time())
    ledger.reserve(request, 995)
    completed = copy.deepcopy(body)
    completed["request_id"] = str(uuid.uuid4())
    done = validate_request(completed, time.time())
    ledger.reserve(done, 995)
    ledger.finish(done, 995, {"outcome": "passed"})
    ledger.close()
    restored = Ledger(root / "state.db", os.getuid())
    try:
        with pytest.raises(Denied, match="reconciliation_required"):
            restored.reserve(request, 995)
        assert restored.reserve(done, 995) == {"outcome": "passed"}
        assert restored.verify_audit() == 4
    finally:
        restored.close()


def test_stop_between_phases_forbids_continuation(broker, body):
    phases = []

    def execute(_, fence):
        fence()
        phases.append("started")
        broker.guardian = lambda: {"trusted": True, "state": "STOPPED"}
        fence()
        phases.append("must not run")
        return {"outcome": "passed"}

    with pytest.raises(Denied, match="guardian_not_active"):
        broker.dispatch(canonical(body), 995, evidence, execute)
    assert phases == ["started"]
    assert broker.ledger.db.execute("SELECT status FROM calls").fetchone()[0] == "uncertain"


def test_audit_failure_prevents_dispatch(broker, body, monkeypatch):
    def failed(*_, **__):
        raise OSError("disk unavailable")

    monkeypatch.setattr(broker.ledger, "event", failed)
    with pytest.raises(OSError):
        broker.dispatch(canonical(body), 995, evidence, lambda *_: pytest.fail("executed"))


def test_audit_tampering_detected(ledger, body):
    ledger.reserve(validate_request(body, time.time()), 995)
    ledger.db.execute("UPDATE audit SET hash=?", ("0" * 64,))
    ledger.db.commit()
    with pytest.raises(Denied, match="audit_chain_invalid"):
        ledger.verify_audit()


def test_sandbox_profile_has_no_caller_control_over_privilege(body):
    request = validate_request(body, time.time())
    argv = sandbox_argv(
        request, Path("/var/lib/jarvis-autonomy-broker/snapshots/fixed"), "sha256:" + "a" * 64
    )
    for flag in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user=65534:65534",
        "--memory=2g",
        "--memory-swap=2g",
        "--cpus=2",
        "--pids-limit=256",
        "--entrypoint=/usr/bin/timeout",
    ):
        assert flag in argv
    assert "600" in argv
    assert argv.count("--mount") == 1
    assert "readonly" in argv[argv.index("--mount") + 1]
    joined = " ".join(argv)
    assert "docker.sock" not in joined and "/home/aaron" not in joined
    assert "--privileged" not in argv and "--env-file" not in argv
    assert "API_KEY" not in joined and "TOKEN" not in joined
    with pytest.raises(Denied):
        sandbox_argv(request, Path("/fixed"), "untrusted:latest")


@pytest.fixture
def release(body):
    body["operation"] = "install_core_release"
    body["parameters"] = {"artifact": "sha256:" + "c" * 64, "pr_number": 123, "pr_head": "b" * 40}
    request = validate_request(body, time.time())
    observed = {
        "repository": REPOSITORY,
        "branch": PRODUCTION_BRANCH,
        "canonical_sha": body["source_sha"],
        "canonical_clean": True,
        "goal_id": body["goal_id"],
        "goal_completed": True,
        "pr_number": 123,
        "pr_head": "b" * 40,
        "pr_branch": body["branch"],
        "pr_base": PRODUCTION_BRANCH,
        "pr_merged": True,
        "merge_sha": body["source_sha"],
        "checks": [
            {
                "name": name,
                "head_sha": "b" * 40,
                "conclusion": "success",
                "status": "completed",
                "trusted_workflow": True,
            }
            for name in REQUIRED_CHECKS
        ],
        "artifact": body["parameters"]["artifact"],
        "artifact_source": body["source_sha"],
        "component": "core",
        "rollback_artifact": "sha256:" + "d" * 64,
        "policy_unchanged": True,
        "independent_review_passed": True,
    }
    return request, observed


def test_exact_release_evidence_accepted_in_harness(release):
    validate_release(*release)


@pytest.mark.parametrize(
    "key,value",
    [
        ("canonical_clean", False),
        ("goal_completed", False),
        ("pr_head", "e" * 40),
        ("pr_merged", False),
        ("component", "developer"),
        ("artifact_source", "f" * 40),
        ("rollback_artifact", ""),
        ("policy_unchanged", False),
        ("independent_review_passed", False),
        ("canonical_sha", "f" * 40),
    ],
)
def test_release_rejects_wrong_or_incomplete_evidence(release, key, value):
    request, observed = release
    observed[key] = value
    with pytest.raises(Denied):
        validate_release(request, observed)


@pytest.mark.parametrize(
    "change",
    [
        {"conclusion": "failure"},
        {"status": "in_progress"},
        {"trusted_workflow": False},
        {"head_sha": "e" * 40},
    ],
)
def test_failed_or_wrong_head_checks_prevent_release(release, change):
    request, observed = release
    observed["checks"][0].update(change)
    with pytest.raises(Denied, match="protected_checks_not_passed"):
        validate_release(request, observed)


def test_duplicate_check_name_does_not_override_failure(release):
    request, observed = release
    observed["checks"].append(dict(observed["checks"][0]))
    with pytest.raises(Denied):
        validate_release(request, observed)
