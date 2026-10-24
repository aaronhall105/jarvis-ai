"""Root-free adapter tests. No Docker daemon, host release or real API is used."""

import copy
import hashlib
import io
import json
import sys
import tarfile
import time
import uuid

import pytest

from tools.autonomy_broker import Denied, Request
from tools.autonomy_broker_host import (
    CoreRelease,
    Docker,
    GitHub,
    bounded_process,
    core_context,
    git_blob,
    validate_core_container,
    verify_tree,
)

OLD = "a" * 64
NEW = "b" * 64
OLD_IMAGE = "sha256:" + "c" * 64
NEW_IMAGE = "sha256:" + "d" * 64
OLD_SHA = "e" * 40
NEW_SHA = "f" * 40


def test_exact_tree_verification_and_public_template_omission(monkeypatch):
    github = GitHub()
    monkeypatch.setattr(
        github,
        "get",
        lambda *a, **k: {
            "truncated": False,
            "tree": [
                {"path": "bridge", "type": "tree", "mode": "040000", "sha": "a" * 40},
                {
                    "path": "bridge/app.py",
                    "type": "blob",
                    "mode": "100644",
                    "sha": git_blob(b"safe"),
                },
                {"path": ".env.example", "type": "blob", "mode": "100644", "sha": "b" * 40},
            ],
        },
    )
    tree = github.tree(NEW_SHA)
    assert tree == {"bridge/app.py": git_blob(b"safe")}
    verify_tree({"bridge/app.py": b"safe"}, tree)
    with pytest.raises(Denied, match="exact_commit"):
        verify_tree({"bridge/app.py": b"changed"}, tree)
    with pytest.raises(Denied, match="exact_commit"):
        verify_tree({"bridge/app.py": b"safe", "extra": b"extra"}, tree)


@pytest.mark.parametrize(
    "entry",
    [
        {"path": "linked", "type": "blob", "mode": "120000", "sha": "a" * 40},
        {"path": "module", "type": "commit", "mode": "160000", "sha": "a" * 40},
        {"path": "../outside", "type": "blob", "mode": "100644", "sha": "a" * 40},
        {"path": ".env", "type": "blob", "mode": "100644", "sha": "a" * 40},
    ],
)
def test_untrusted_tree_entries_rejected(monkeypatch, entry):
    github = GitHub()
    monkeypatch.setattr(github, "get", lambda *a, **k: {"truncated": False, "tree": [entry]})
    with pytest.raises(Denied):
        github.tree(NEW_SHA)


def test_process_output_is_drained_but_never_returned():
    result = bounded_process(
        [sys.executable, "-I", "-c", "print('synthetic private output')"], lambda: None
    )
    assert result["outcome"] == "passed"
    assert result["output_sha256"] == hashlib.sha256(b"synthetic private output\n").hexdigest()
    assert "private" not in json.dumps(result)


def test_process_has_no_inherited_credentials(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST_SECRET", "synthetic-test-only")
    result = bounded_process(
        [sys.executable, "-I", "-c", "import os; assert 'JARVIS_TEST_SECRET' not in os.environ"],
        lambda: None,
    )
    assert result["exit_code"] == 0


def test_process_timeout_is_bounded_and_cancels():
    cancelled = []
    started = time.monotonic()
    with pytest.raises(Denied, match="execution_timeout"):
        bounded_process(
            [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
            lambda: None,
            timeout=0.02,
            cancel=lambda: cancelled.append(True),
        )
    assert time.monotonic() - started < 3
    assert cancelled == [True]


def test_process_stop_cancels_and_does_not_start_next_phase():
    calls, cancelled = [], []

    def fence():
        calls.append(True)
        if len(calls) > 1:
            raise Denied("guardian_not_active")

    with pytest.raises(Denied, match="guardian_not_active"):
        bounded_process(
            [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
            fence,
            cancel=lambda: cancelled.append(True),
        )
    assert cancelled == [True]


def test_process_output_limit():
    with pytest.raises(Denied, match="execution_output_limit"):
        bounded_process([sys.executable, "-I", "-c", "print('x'*1100000)"], lambda: None)


def test_context_is_fixed_offline_recipe_not_repository_dockerfile():
    files = {
        "bridge/requirements.txt": b"pinned",
        "bridge/Dockerfile": b"untrusted command",
        "bridge/app/main.py": b"safe",
        "unrelated.txt": b"outside context",
    }
    policy = {
        "core_base_image": OLD_IMAGE,
        "dependency_hashes": {"bridge/requirements.txt": hashlib.sha256(b"pinned").hexdigest()},
    }
    blob = core_context(files, policy, NEW_SHA)
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        assert archive.getnames() == ["Dockerfile", "app/main.py"]
        recipe = archive.extractfile("Dockerfile").read()
        assert b"untrusted" not in recipe and b"pip" not in recipe and b"apt" not in recipe
        assert OLD_IMAGE.encode() in recipe and NEW_SHA.encode() in recipe
        assert all(
            item.isfile() and item.uid == 0 and item.mode == 0o444 for item in archive.getmembers()
        )
    files["bridge/requirements.txt"] = b"changed"
    with pytest.raises(Denied, match="dependency_change"):
        core_context(files, policy, NEW_SHA)


def test_context_rejects_archive_traversal():
    with pytest.raises(Denied):
        core_context(
            {"bridge/app/../../escape": b"bad"},
            {"core_base_image": OLD_IMAGE, "dependency_hashes": {}},
            NEW_SHA,
        )


@pytest.fixture
def release_setup():
    policy = {
        "core_network": "fixed-network",
        "core_mounts": {"/app/data": "/fixed/data"},
        "core_ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]},
    }
    old = {
        "Name": "/jarvis-core",
        "Id": OLD,
        "Image": OLD_IMAGE,
        "Config": {
            "Labels": {"org.opencontainers.image.revision": OLD_SHA},
            "Env": ["SYNTHETIC_TEST_ONLY=never-output", "JARVIS_SPECULATIVE_READS_ENABLED=false"],
        },
        "State": {"Running": True},
        "Mounts": [{"Type": "bind", "Destination": "/app/data", "Source": "/fixed/data"}],
        "HostConfig": {
            "NetworkMode": "fixed-network",
            "IpcMode": "private",
            "PortBindings": policy["core_ports"],
        },
    }
    request = Request(
        {
            "request_id": str(uuid.uuid4()),
            "source_sha": NEW_SHA,
            "parameters": {"artifact": NEW_IMAGE},
        }
    )

    class FakeDocker:
        def __init__(self):
            self.calls = []
            self.created = None
            self.old = copy.deepcopy(old)

        def inspect(self, name):
            if name in {"jarvis-core", OLD}:
                return copy.deepcopy(self.old)
            assert name == NEW
            return {"Config": {"Labels": {"io.jarvis.autonomy.request": request.request_id}}}

        def call(self, method, path, body=None):
            self.calls.append((method, path, copy.deepcopy(body)))
            if method == "GET":
                return {
                    "Id": NEW_IMAGE,
                    "Config": {"Labels": {"org.opencontainers.image.revision": NEW_SHA}},
                }
            if path == f"/containers/{OLD}/stop?t=10":
                self.old["State"]["Running"] = False
            if path.startswith(f"/containers/{OLD}/rename?"):
                self.old["Name"] = "/" + path.split("name=", 1)[1]
            if path == f"/containers/{OLD}/start":
                self.old["State"]["Running"] = True
            if path == "/containers/create?name=jarvis-core":
                self.created = body
                return {"Id": NEW}

    return policy, old, request, FakeDocker()


@pytest.mark.parametrize(
    "field,value",
    [
        ("Privileged", True),
        ("CapAdd", ["SYS_ADMIN"]),
        ("Devices", [{}]),
        ("DeviceRequests", [{}]),
        ("PidMode", "host"),
        ("IpcMode", "host"),
        ("NetworkMode", "host"),
        ("VolumesFrom", ["another"]),
    ],
)
def test_existing_runtime_escalation_denied(release_setup, field, value):
    policy, old, _, _ = release_setup
    old["HostConfig"][field] = value
    with pytest.raises(Denied):
        validate_core_container(old, policy)


def test_duplicate_mount_and_changed_port_denied(release_setup):
    policy, old, _, _ = release_setup
    old["Mounts"].append(copy.deepcopy(old["Mounts"][0]))
    with pytest.raises(Denied, match="unexpected_core_mounts"):
        validate_core_container(old, policy)
    old["Mounts"].pop()
    old["HostConfig"]["PortBindings"] = {}
    with pytest.raises(Denied, match="unexpected_core_ports"):
        validate_core_container(old, policy)


def test_no_release_without_independent_data_safety_admission(release_setup):
    policy, _, request, docker = release_setup
    records = []
    release = CoreRelease(docker, policy, records.append, lambda _: True, lambda _: False)
    with pytest.raises(Denied, match="data_rollback_not_proven"):
        release.install(request, lambda: None)
    assert docker.calls == [] and records == []


def test_release_harness_records_before_stop_and_keeps_configuration_private(release_setup):
    policy, _, request, docker = release_setup
    records = []

    def record(value):
        if value["stage"] == "prepared":
            assert not any(method != "GET" for method, _, _ in docker.calls)
        records.append(copy.deepcopy(value))

    result = CoreRelease(docker, policy, record, lambda _: True, lambda _: True).install(
        request, lambda: None
    )
    assert result["outcome"] == "installed"
    assert result["rollback_artifact"] == OLD_IMAGE
    assert [r["stage"] for r in records] == ["prepared", "created", "installed"]
    assert "never-output" not in json.dumps(records + [result])
    assert docker.created["Env"].count("JARVIS_SPECULATIVE_READS_ENABLED=false") == 1
    assert docker.created["HostConfig"]["Privileged"] is False


def test_stop_after_old_container_stops_only_restores_old_container(release_setup):
    policy, _, request, docker = release_setup
    records = []

    def fence():
        if any(path.endswith("/stop?t=10") for _, path, _ in docker.calls):
            raise Denied("guardian_not_active")

    release = CoreRelease(
        docker, policy, lambda v: records.append(copy.deepcopy(v)), lambda _: True, lambda _: True
    )
    with pytest.raises(Denied, match="guardian_not_active"):
        release.install(request, fence)
    assert not any("/create?" in path for _, path, _ in docker.calls)
    assert docker.old["State"]["Running"]
    assert records[-1]["stage"] == "container_restored"


def test_failed_candidate_is_not_reported_as_success(release_setup, monkeypatch):
    policy, _, request, docker = release_setup
    records = []
    ticks = iter([0, 100, 101, 102])
    monkeypatch.setattr("tools.autonomy_broker_host.time.monotonic", lambda: next(ticks))
    release = CoreRelease(
        docker,
        policy,
        lambda v: records.append(copy.deepcopy(v)),
        lambda sha: sha == OLD_SHA,
        lambda _: True,
    )
    with pytest.raises(Denied, match="candidate_core_unhealthy"):
        release.install(request, lambda: None)
    assert records[-1]["stage"] == "container_restored"
    assert any(method == "DELETE" and NEW in path for method, path, _ in docker.calls)


def test_uncertain_compensation_is_recorded(release_setup, monkeypatch):
    policy, _, request, docker = release_setup
    records = []
    release = CoreRelease(
        docker, policy, lambda v: records.append(copy.deepcopy(v)), lambda _: True, lambda _: True
    )
    original = docker.call

    def broken(method, path, body=None):
        if method != "GET":
            raise Denied("docker_operation_uncertain")
        return original(method, path, body)

    monkeypatch.setattr(docker, "call", broken)
    monkeypatch.setattr(release, "restore", lambda *_: (_ for _ in ()).throw(Denied("uncertain")))
    with pytest.raises(Denied):
        release.install(request, lambda: None)
    assert records[-1]["stage"] == "reconciliation_required"


def test_cancel_does_not_hide_unknown_container_cleanup(monkeypatch):
    docker = Docker()
    monkeypatch.setattr(
        docker, "call", lambda *_: (_ for _ in ()).throw(Denied("uncertain_cleanup"))
    )
    with pytest.raises(Denied, match="uncertain_cleanup"):
        docker.cancel_sandbox(str(uuid.uuid4()))


def test_cleanup_404_is_safe_only_for_fixed_sandbox_name(monkeypatch):
    from unittest.mock import Mock
    from tools import autonomy_broker_host as module

    response = Mock(status=404)
    response.read.return_value = b""
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(module, "UnixHTTP", lambda *args, **kwargs: connection)
    Docker().cancel_sandbox(str(uuid.uuid4()))
    with pytest.raises(Denied, match="docker_operation_failed"):
        Docker().call("DELETE", "/containers/jarvis-core?force=true&v=true")
