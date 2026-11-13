"""Privileged coordinator enforces the signed execution wall-clock ceiling."""

import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import supervised_development_runtime as runtime_module
from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_runtime import SupervisedDevelopmentRuntime

GENERATION = "10000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def pinned_test_environment(monkeypatch):
    # These tests isolate the post-preflight process supervisor. Readiness
    # denial and exact receipt binding have separate product regressions.
    monkeypatch.setattr(
        SupervisedDevelopmentRuntime, "_verify_execution_readiness", lambda *_: None
    )
    monkeypatch.setattr(
        runtime_module, "verify_worker_environment", lambda: Path("/opt/fixture-runtime/bin/python")
    )


def runtime_fixture():
    runtime = object.__new__(SupervisedDevelopmentRuntime)
    runtime.guardian = lambda: {"state": "ACTIVE", "trusted": True, "generation": GENERATION}
    return runtime


def task_fixture():
    return SimpleNamespace(deadline_seconds=120, guardian_generation=GENERATION)


def test_worker_watchdog_never_adds_cleanup_grace_to_signed_deadline(monkeypatch):
    waits = []
    killed = []
    spawn = []
    clock = [0.0]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock[0])

    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                clock[0] = 120.0
                raise subprocess.TimeoutExpired("disposable-worker", timeout)
            return "", ""

    def launch(command, **kwargs):
        spawn.append((command, kwargs))
        return Process()

    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})
    monkeypatch.setattr(runtime_module.subprocess, "Popen", launch)
    monkeypatch.setattr(runtime_module.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    runtime = runtime_fixture()
    with pytest.raises(LifecycleError, match="development_worker_timeout"):
        runtime._run_worker_subprocess("disposable-task", task_fixture())
    assert waits == [runtime_module.WORKER_FENCE_SECONDS, 5]
    assert killed == [(987654, signal.SIGKILL)]
    assert spawn[0][0] == [
        "/opt/fixture-runtime/bin/python",
        "-I",
        "-B",
        "-X",
        "pycache_prefix=/opt/fixture-runtime/bytecode-disabled",
        runtime_module.WORKER_ENTRY,
        "development-worker",
        "disposable-task",
    ]
    assert spawn[0][1]["start_new_session"] is True


@pytest.mark.parametrize(
    "interruption",
    [KeyboardInterrupt(), SystemExit(1), LifecycleError("termination"), RuntimeError()],
)
def test_worker_is_killed_and_reaped_before_propagating_cancellation(monkeypatch, interruption):
    events = []

    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            events.append(("wait", timeout))
            if len(events) == 1:
                raise interruption
            return "", ""

    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(
        runtime_module.os, "killpg", lambda pid, sig: events.append(("kill", pid, sig))
    )
    runtime = runtime_fixture()
    with pytest.raises(type(interruption)):
        runtime._run_worker_subprocess("disposable-task", task_fixture())
    assert events == [
        ("wait", runtime_module.WORKER_FENCE_SECONDS),
        ("kill", 987654, signal.SIGKILL),
        ("wait", 5),
    ]


def test_worker_cleanup_uncertainty_is_not_reported_as_safe_failure(monkeypatch):
    clock = iter([0.0, 120.0])
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: next(clock))

    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("disposable-worker", timeout)

    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(runtime_module.os, "killpg", lambda *args: None)
    runtime = runtime_fixture()
    with pytest.raises(LifecycleError, match="development_worker_cleanup_unconfirmed"):
        runtime._run_worker_subprocess("disposable-task", task_fixture())


@pytest.mark.parametrize("helper_fails", [False, True])
def test_restricted_root_uses_exact_worker_identity_to_cancel_child(monkeypatch, helper_fails):
    waits, helpers, identity = [], [], []

    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                raise KeyboardInterrupt()
            return "", ""

    monkeypatch.setattr(
        runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345, "autonomy_gid": 12346}
    )
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: Process())

    def denied(*args):
        raise PermissionError("coordinator has no CAP_KILL")

    def helper(command, **kwargs):
        helpers.append((command, kwargs))
        kwargs["preexec_fn"]()
        if helper_fails:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(runtime_module.os, "killpg", denied)
    monkeypatch.setattr(runtime_module.subprocess, "run", helper)
    monkeypatch.setattr(runtime_module.os, "setgroups", lambda v: identity.append(("groups", v)))
    monkeypatch.setattr(runtime_module.os, "setgid", lambda v: identity.append(("gid", v)))
    monkeypatch.setattr(runtime_module.os, "setuid", lambda v: identity.append(("uid", v)))
    runtime = runtime_fixture()
    expected = LifecycleError if helper_fails else KeyboardInterrupt
    with pytest.raises(expected):
        runtime._run_worker_subprocess("disposable-task", task_fixture())
    assert identity == [("groups", []), ("gid", 12346), ("uid", 12345)]
    assert helpers[0][0][-1] == "987654"
    assert helpers[0][1]["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
    assert helpers[0][1]["timeout"] == 5
    first_wait = runtime_module.WORKER_FENCE_SECONDS
    assert waits == ([first_wait] if helper_fails else [first_wait, 5])


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"state": "STOPPED", "trusted": True, "generation": GENERATION},
        {"state": "ACTIVE", "trusted": False, "generation": GENERATION},
        {"state": "ACTIVE", "trusted": True, "generation": "malformed"},
        {"state": "ACTIVE", "trusted": True, "generation": "20000000-0000-4000-8000-000000000001"},
    ],
)
def test_denied_guardian_never_starts_worker_or_validates_sdk(monkeypatch, state):
    runtime = runtime_fixture()
    runtime.guardian = lambda: state
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawn"))
    monkeypatch.setattr(runtime_module, "verify_worker_environment", lambda: pytest.fail("SDK"))
    with pytest.raises(LifecycleError, match="development_worker_guardian_denied"):
        runtime._run_worker_subprocess("disposable-task", task_fixture())


@pytest.mark.parametrize("observation", ["stopped", "untrusted", "throws", "new-cycle"])
def test_stop_or_uncertain_guardian_interrupts_inflight_worker(monkeypatch, observation):
    runtime = runtime_fixture()
    state = {"state": "ACTIVE", "trusted": True, "generation": GENERATION}
    events = []

    def guardian():
        if state.get("throws"):
            raise OSError("fixture observation unavailable")
        return dict(state)

    runtime.guardian = guardian

    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            events.append(("wait", timeout))
            if len(events) == 1:
                if observation == "stopped":
                    state["state"] = "STOPPED"
                elif observation == "untrusted":
                    state["trusted"] = False
                elif observation == "throws":
                    state["throws"] = True
                else:
                    state["generation"] = "20000000-0000-4000-8000-000000000001"
                    state["previous_generation"] = GENERATION
                raise subprocess.TimeoutExpired("fixture-inflight", timeout)
            return "", ""

    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(runtime_module.os, "killpg", lambda *a: events.append(("kill", *a)))
    with pytest.raises(
        LifecycleError, match="development_worker_(guardian_denied|generation_changed)"
    ):
        runtime._run_worker_subprocess("disposable-task", task_fixture())
    assert events == [
        ("wait", runtime_module.WORKER_FENCE_SECONDS),
        ("kill", 987654, signal.SIGKILL),
        ("wait", 5),
    ]


def test_environment_integrity_failure_cannot_spawn_worker(monkeypatch):
    runtime = runtime_fixture()
    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})

    def invalid():
        raise LifecycleError("worker_environment_changed")

    monkeypatch.setattr(runtime_module, "verify_worker_environment", invalid)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawn"))
    with pytest.raises(LifecycleError, match="worker_environment_changed"):
        runtime._run_worker_subprocess("disposable-task", task_fixture())
