"""Direct worker entry rejects before opening durable state or provider code.

Identities and observations here are disposable test doubles. These tests never
change process credentials, access the installed controller DB, or invoke the
production worker under its protected UID.
"""

from pathlib import Path

import pytest

from tools import supervised_development_runtime as runtime
from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_environment import EnvironmentError


GENERATION = "10000000-0000-4000-8000-000000000001"
PYTHON = Path("/opt/disposable-verified-worker/bin/python")
DISPOSABLE_UID = 65534


@pytest.fixture
def guarded_entry(monkeypatch):
    touched = []

    def forbidden(name):
        def reject(*args, **kwargs):
            touched.append(name)
            pytest.fail(f"entry must reject before {name}")

        return reject

    monkeypatch.setattr(runtime.os, "getuid", lambda: DISPOSABLE_UID)
    monkeypatch.setattr(runtime, "worker_identity", lambda: DISPOSABLE_UID)
    monkeypatch.setattr(runtime.sys, "executable", str(PYTHON))
    monkeypatch.setattr(runtime.sys, "dont_write_bytecode", True)
    monkeypatch.setattr(
        runtime.sys, "pycache_prefix", str(PYTHON.parent.parent / "bytecode-disabled")
    )
    monkeypatch.setattr(
        runtime,
        "guardian_status",
        lambda: {"state": "ACTIVE", "trusted": True, "generation": GENERATION},
    )
    monkeypatch.setattr(runtime, "verify_worker_environment", lambda: PYTHON)
    for name in (
        "SupervisedDevelopmentController",
        "GovernorCandidateSource",
        "ExistingSelfDevelopmentWorker",
        "SupervisedDevelopmentPipeline",
    ):
        monkeypatch.setattr(runtime, name, forbidden(name))
    monkeypatch.setattr(runtime.subprocess, "Popen", forbidden("provider/process spawn"))
    return touched


@pytest.mark.parametrize("uid", [0, 65533, 1000])
def test_direct_entry_wrong_identity_cannot_observe_or_open_state(guarded_entry, monkeypatch, uid):
    monkeypatch.setattr(runtime.os, "getuid", lambda: uid)
    monkeypatch.setattr(runtime, "guardian_status", lambda: pytest.fail("unexpected observation"))
    assert runtime.worker_main("disposable-task") == 1
    assert guarded_entry == []


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"state": "STOPPED", "trusted": True, "generation": GENERATION},
        {"state": "STOPPED", "trusted": False, "generation": GENERATION},
        {"state": "ACTIVE", "trusted": False, "generation": GENERATION},
        {"state": "ACTIVE", "trusted": 1, "generation": GENERATION},
        {"state": "UNKNOWN", "trusted": True, "generation": GENERATION},
        {"state": "ACTIVE", "trusted": True},
        {"state": "ACTIVE", "trusted": True, "generation": "malformed"},
        {"state": "ACTIVE", "trusted": True, "generation": 1},
    ],
)
def test_direct_entry_stopped_or_untrusted_never_opens_state(guarded_entry, monkeypatch, status):
    monkeypatch.setattr(runtime, "guardian_status", lambda: status)
    monkeypatch.setattr(runtime, "verify_worker_environment", lambda: pytest.fail("environment"))
    assert runtime.worker_main("disposable-task") == 1
    assert guarded_entry == []


def test_direct_entry_unavailable_guardian_never_opens_state(guarded_entry, monkeypatch):
    def unavailable():
        raise OSError("disposable observation failure")

    monkeypatch.setattr(runtime, "guardian_status", unavailable)
    assert runtime.worker_main("disposable-task") == 1
    assert guarded_entry == []


def test_direct_entry_wrong_interpreter_never_opens_state(guarded_entry, monkeypatch):
    monkeypatch.setattr(runtime.sys, "executable", "/usr/bin/python3")
    assert runtime.worker_main("disposable-task") == 1
    assert guarded_entry == []


@pytest.mark.parametrize("cache,write", [(None, True), ("/tmp/untrusted", True), ("fixed", False)])
def test_direct_entry_cannot_use_unpinned_bytecode_cache(guarded_entry, monkeypatch, cache, write):
    monkeypatch.setattr(runtime.sys, "dont_write_bytecode", write)
    monkeypatch.setattr(runtime.sys, "pycache_prefix", cache)
    assert runtime.worker_main("disposable-task") == 1
    assert guarded_entry == []


@pytest.mark.parametrize(
    "reason",
    ["worker_environment_missing", "worker_environment_changed", "worker_environment_untrusted"],
)
def test_direct_entry_unverified_environment_never_opens_state(guarded_entry, monkeypatch, reason):
    def unavailable():
        raise EnvironmentError(reason)

    monkeypatch.setattr(runtime, "verify_worker_environment", unavailable)
    assert runtime.worker_main("disposable-task") == 1
    assert guarded_entry == []


def test_verified_entry_reaches_only_fixed_database_constructor(guarded_entry, monkeypatch):
    observed = []

    def boundary(path, **kwargs):
        observed.append((path, kwargs))
        raise LifecycleError("fixture stops before database access")

    monkeypatch.setattr(runtime, "SupervisedDevelopmentController", boundary)
    with pytest.raises(LifecycleError, match="fixture stops before database access"):
        runtime.worker_main("disposable-task")
    assert observed == [(runtime.CONTROLLER_DATABASE, {"database_mode": 0o660})]
    assert guarded_entry == []


def test_privileged_broker_services_keep_system_python_and_controller_hardening():
    root = Path(__file__).resolve().parents[2]
    units = root / "systemd/autonomy"
    controller = (units / "jarvis-autonomy-broker-development-controller.service").read_text()
    for name in (
        "operator",
        "worker",
        "development-controller",
        "development-admission",
        "development-publication",
    ):
        text = (units / f"jarvis-autonomy-broker-{name}.service").read_text()
        assert (
            "ExecStart=/usr/bin/python3 -I /usr/local/libexec/jarvis-autonomy-broker/entry.py "
        ) in text
    for setting in (
        "User=root",
        "Group=root",
        "SupplementaryGroups=jarvis-autonomy",
        "CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SETUID CAP_SETGID",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "PrivateDevices=true",
        "ProtectKernelTunables=true",
        "ProtectControlGroups=true",
        "RestrictNamespaces=user mnt pid net ipc uts",
        "TasksMax=32",
        "MemoryMax=512M",
        "KillMode=control-group",
    ):
        assert setting in controller
    assert "CAP_SYS_ADMIN" not in controller.split("CapabilityBoundingSet=", 1)[1].splitlines()[0]
