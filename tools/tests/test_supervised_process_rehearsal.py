"""Opt-in real controller/worker process rehearsal with disposable root fixtures.

Requires the existing host ``runc`` AppArmor profile and test virtualenv. Only
the disposable outer container permits nested user/network namespaces and an
unmasked proc mount; the actual worker still uses its fail-closed bubblewrap
jail. This exercises production entrypoints, identities and fixed IPC, not the
host's systemd service manager. It never changes host profiles or sysctls.
"""

import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest


@pytest.mark.skipif(
    os.environ.get("JARVIS_PROCESS_DOCKER_TEST") != "1",
    reason="explicit disposable Docker/root-identity rehearsal opt-in required",
)
@pytest.mark.parametrize("outcome", ["success", "disconnect", "sandbox-denied", "terminated"])
def test_actual_controller_process_signed_admission_iterative_worker_review_stop(outcome, tmp_path):
    root = Path(__file__).resolve().parents[2]
    name = "jarvis-development-rehearsal-" + uuid.uuid4().hex[:12]
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--env",
        "JARVIS_FIXTURE_OUTCOME=" + outcome,
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--cap-add",
        "CHOWN",
        "--security-opt",
        "seccomp=unconfined",
        "--security-opt",
        "apparmor=runc",
        *(["--security-opt", "systempaths=unconfined"] if outcome != "sandbox-denied" else []),
        "--mount",
        "type=bind,src=/usr,dst=/usr,readonly",
        "--tmpfs",
        "/usr/local/libexec/jarvis-autonomy-broker:rw,nosuid,nodev,mode=755",
        "--mount",
        "type=bind,src=/home/aaron/.venvs/jarvis-phase2,dst=/home/aaron/.venvs/jarvis-phase2,readonly",
        "--mount",
        f"type=bind,src={root},dst=/opt/jarvis-source,readonly",
        "--entrypoint",
        "/usr/libexec/docker/docker-init",
        "python:3.12-slim",
        "--",
        "/home/aaron/.venvs/jarvis-phase2/bin/python",
        "/opt/jarvis-source/tools/tests/fixtures/supervised_process_rehearsal.py",
        "fixture-run",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=190)
        assert result.returncode == 0, result.stderr[-5000:]
        evidence = json.loads(result.stdout)
        assert evidence["guardian"]["state"] == "STOPPED"
        assert evidence["guardian"]["trusted"] is True
        assert evidence["gateway_uid"] == 1000
        assert evidence["worker_uid"] == 600
        if outcome == "success":
            assert evidence["state"] == "REVIEW_REQUIRED", json.dumps(evidence, sort_keys=True)
            assert evidence["provider_requests"] == 2
            assert len(evidence["calls"]) == 2
            assert all(call["outcome"] == "completed" for call in evidence["calls"])
            assert evidence["restart"]["status"] == "no_eligible_candidate"
        else:
            assert evidence["state"] == "RECONCILIATION_REQUIRED", evidence
            assert evidence["restart"]["status"].upper() == "RECONCILIATION_REQUIRED"
            if outcome == "disconnect":
                assert evidence["provider_requests"] == 1
                assert len(evidence["calls"]) == 1
                assert evidence["calls"][0]["outcome"] == "uncertain"
            elif outcome == "sandbox-denied":
                assert evidence["provider_requests"] == 0
                assert not evidence["calls"]
                assert "verification_sandbox_unavailable" in evidence["worker_error"]
            else:
                assert evidence["provider_requests"] == 1
                assert len(evidence["calls"]) == 1
                assert evidence["calls"][0]["outcome"] == "reserved"
                assert evidence["controller_returncode"] != 0
        # Keep only safe receipt metadata, never fixture signatures or keys.
        receipt = {
            "scenario": outcome,
            "state": evidence["state"],
            "provider_requests": evidence["provider_requests"],
            "call_outcomes": [call["outcome"] for call in evidence["calls"]],
            "gateway_uid": evidence["gateway_uid"],
            "worker_uid": evidence["worker_uid"],
            "guardian": {key: evidence["guardian"][key] for key in ("state", "trusted")},
            "restart": evidence["restart"]["status"],
            "controller_returncode": evidence["controller_returncode"],
        }
        receipt_path = tmp_path / "rehearsal-evidence.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        receipt_path.chmod(0o600)
    finally:
        subprocess.run(
            ["docker", "stop", "--time=1", name], capture_output=True, timeout=10, check=False
        )
