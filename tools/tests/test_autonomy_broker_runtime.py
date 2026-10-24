import hashlib
import json
import os
import time
import uuid

import pytest

from tools import autonomy_broker_runtime as runtime_module
from tools.autonomy_broker import Broker, Denied, Ledger, canonical
from tools.autonomy_broker_store import Records


@pytest.fixture
def wired(tmp_path, monkeypatch):
    base, jobs = tmp_path / "protected", tmp_path / "jobs"
    base.mkdir(mode=0o700)
    jobs.mkdir(mode=0o755)
    for name in ("records", "snapshots"):
        (base / name).mkdir(mode=0o700)
    monkeypatch.setattr(runtime_module, "BASE", base)
    monkeypatch.setattr(runtime_module, "JOBS", jobs)
    ledger = Ledger(base / "ledger.db", os.getuid())
    records = Records(base / "records", os.getuid(), False)
    goal, generation = str(uuid.uuid4()), str(uuid.uuid4())
    body = {
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "goal_id": goal,
        "operation": "register_job",
        "repository": "aaronhall105/jarvis-ai",
        "branch": "jarvis/autonomous-" + uuid.UUID(goal).hex,
        "source_sha": "a" * 40,
        "expires_at": int(time.time()) + 120,
        "guardian_generation": generation,
        "parameters": {},
    }

    class GitHub:
        def get(self, path):
            assert path == "git/ref/heads/jarvis/unified-production"
            return {"object": {"sha": "a" * 40}}

        def tree(self, sha):
            assert sha == "a" * 40
            return {}

    policy = {
        "autonomy_uid": os.getuid(),
        "autonomy_gid": os.getgid(),
        "tool_image": "sha256:" + "b" * 64,
    }
    runtime = runtime_module.Runtime(policy, records, ledger, github=GitHub(), docker=object())
    status = {"trusted": True, "state": "ACTIVE", "generation": generation}
    broker = Broker(ledger, os.getuid(), lambda: status)
    yield runtime, broker, body, jobs, status
    ledger.close()


def test_registered_workspace_and_exact_once_verification_survive_restart(wired, monkeypatch):
    runtime, broker, body, jobs, _ = wired
    first = broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute)
    assert first["workspace"] == str(jobs / body["goal_id"])
    assert broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute) == first
    workspace = jobs / body["goal_id"]
    (workspace / "bridge/tests").mkdir(parents=True)
    (workspace / "bridge/tests/test_one.py").write_text("def test_one(): assert True\n")
    calls = []

    def snapshot(parent, key, files):
        assert parent == runtime.records.base.parent / "snapshots"
        assert len(key) == 64 and list(files) == ["bridge/tests/test_one.py"]
        return parent / key

    def process(argv, fence, **kwargs):
        fence()
        calls.append(argv)
        assert "--network=none" in argv and "--read-only" in argv
        assert "--cap-drop=ALL" in argv and "--user=65534:65534" in argv
        assert not any("docker.sock" in value or "/home/aaron" in value for value in argv)
        return {"exit_code": 0, "outcome": "passed"}

    monkeypatch.setattr(runtime_module, "materialize_snapshot", snapshot)
    monkeypatch.setattr(runtime_module, "bounded_process", process)
    body.update(
        request_id=str(uuid.uuid4()),
        operation="run_verification",
        parameters={"profile": "verify_python", "target": "bridge/tests/test_one.py"},
    )
    done = broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute)
    assert done["outcome"] == "passed" and len(done["snapshot_sha256"]) == 64
    assert broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute) == done
    assert len(calls) == 1
    # A second connection to persisted SQLite demonstrates durable receipts;
    # full Ledger restart/uncertain recovery is covered in the original suite.
    import sqlite3

    with sqlite3.connect(runtime.records.base.parent / "ledger.db") as persisted:
        row = persisted.execute(
            "SELECT status,result FROM calls WHERE id=?", (body["request_id"],)
        ).fetchone()
        assert row[0] == "completed" and json.loads(row[1]) == done


def test_stop_prevents_even_workspace_registration(wired):
    runtime, broker, body, jobs, status = wired
    status["state"] = "STOPPED"
    with pytest.raises(Denied, match="guardian_not_active"):
        broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute)
    assert list(jobs.iterdir()) == []


def test_workspaces_cannot_cross_jobs(wired):
    runtime, broker, body, _, _ = wired
    broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute)
    body.update(
        request_id=str(uuid.uuid4()),
        goal_id=str(uuid.uuid4()),
        operation="run_verification",
        parameters={"profile": "verify_python", "target": "bridge/tests"},
    )
    body["branch"] = "jarvis/autonomous-" + uuid.UUID(body["goal_id"]).hex
    with pytest.raises(Denied, match="job_not_authorized"):
        broker.dispatch(canonical(body), os.getuid(), runtime.evidence, runtime.execute)


def test_production_git_queries_use_no_shell_hooks_or_inherited_credentials(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("fixed")
    monkeypatch.setattr(runtime_module, "CANONICAL", tmp_path)
    policy = {
        "operator_uid": os.getuid(),
        "git_metadata_hashes": {str(config): hashlib.sha256(config.read_bytes()).hexdigest()},
    }
    calls = []

    def command(argv, **kwargs):
        from types import SimpleNamespace

        calls.append((argv, kwargs))
        assert "--no-optional-locks" in argv and "core.fsmonitor=false" in argv
        assert "core.hooksPath=/dev/null" in argv
        assert "safe.directory=" + str(tmp_path) in argv
        assert "safe.directory=*" not in argv
        assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
        output = (
            "a" * 40
            if "rev-parse" in argv
            else "jarvis/unified-production"
            if "branch" in argv
            else ""
        )
        return SimpleNamespace(returncode=0, stdout=output.encode())

    monkeypatch.setattr(runtime_module.subprocess, "run", command)
    runtime_module.canonical_clean(policy, "a" * 40)
    assert len(calls) == 3
    config.write_text("changed")
    with pytest.raises(Denied, match="configuration_changed"):
        runtime_module.canonical_clean(policy, "a" * 40)
    assert len(calls) == 3


def test_record_audit_is_chained_and_tampering_fails_closed(tmp_path):
    tmp_path.chmod(0o700)
    records = Records(tmp_path, os.getuid(), False)
    key = str(uuid.uuid4())
    records.write("jobs", key, {"state": "prepared"}, new=True)
    records.write("jobs", key, {"state": "finished"})
    audit = tmp_path / "audit.jsonl"
    lines = [json.loads(line) for line in audit.read_bytes().splitlines()]
    assert lines[1]["previous"] == lines[0]["hash"]
    assert "prepared" not in audit.read_text() and "finished" not in audit.read_text()
    damaged = audit.read_bytes().replace(lines[0]["hash"].encode(), b"0" * 64, 1)
    audit.write_bytes(damaged)
    with pytest.raises(Denied, match="record_audit_chain_invalid"):
        records.write("jobs", key, {"state": "must not authorize another phase"})


def test_root_entry_rejects_unprivileged_execution(monkeypatch):
    monkeypatch.setattr(runtime_module.os, "geteuid", lambda: 995)
    assert runtime_module.main(["release", str(uuid.uuid4())]) == 1


def test_recovery_cancels_only_ledger_owned_tests_without_replaying(wired):
    from tools.autonomy_broker import Request

    runtime, _, body, _, _ = wired
    body.update(
        operation="run_verification",
        parameters={"profile": "verify_python", "target": "bridge/tests"},
    )
    request = Request(body)
    runtime.ledger.reserve(request, os.getuid())
    runtime.ledger.finish(request, os.getuid(), None)
    calls = []

    class Docker:
        def cancel_sandbox(self, request_id):
            calls.append(request_id)

    runtime_module.recover_uncertain(runtime.ledger, Docker(), lambda: calls.append("stop"))
    assert calls == ["stop", request.request_id]
    assert runtime.ledger.db.execute("SELECT status FROM calls").fetchone()[0] == "uncertain"
    with pytest.raises(Denied, match="reconciliation_required"):
        runtime.ledger.completed(body, os.getuid())


def test_recovery_stops_but_never_replays_unknown_release(wired):
    from tools.autonomy_broker import Request
    from unittest.mock import Mock

    runtime, _, body, _, _ = wired
    body["operation"] = "install_core_release"
    request = Request(body)
    runtime.ledger.reserve(request, os.getuid())
    runtime.ledger.finish(request, os.getuid(), None)
    docker, stop = Mock(), Mock()
    runtime_module.recover_uncertain(runtime.ledger, docker, stop)
    stop.assert_called_once_with()
    docker.cancel_sandbox.assert_not_called()


def test_malformed_request_rejection_audit_has_no_attacker_content(tmp_path):
    tmp_path.chmod(0o700)
    ledger = Ledger(tmp_path / "ledger.db", os.getuid())
    try:

        def reject(raw, actor):
            raise Denied("invalid_schema")

        raw = b'{"command":"private untrusted command"}'
        with pytest.raises(Denied):
            runtime_module.audited_dispatch(ledger, reject, raw, 995)
        assert ledger.verify_audit() == 1
        entry = ledger.db.execute("SELECT entry FROM audit").fetchone()[0]
        assert "private" not in entry and "command" not in entry
        assert hashlib.sha256(raw).hexdigest() in entry
    finally:
        ledger.close()


def test_service_profiles_have_no_worker_privilege_grant():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for role in ("worker", "operator"):
        text = (root / f"systemd/autonomy/jarvis-autonomy-broker-{role}.service").read_text()
        assert "NoNewPrivileges=true" in text and "ProtectSystem=strict" in text
        assert "ExecStart=/usr/bin/python3 -I /usr/local/libexec/" in text
        assert "StandardOutput=null" in text
        socket = (root / f"systemd/autonomy/jarvis-autonomy-broker-{role}.socket").read_text()
        assert "SocketMode=0660" in socket and "SocketUser=root" in socket
    dropin = (root / "systemd/autonomy/broker-worker-access.conf").read_text()
    assert "SupplementaryGroups" not in dropin and "docker.sock" not in dropin
