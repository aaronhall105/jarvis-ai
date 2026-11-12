"""Privileged coordinator enforces the signed execution wall-clock ceiling."""

import signal
import subprocess
from types import SimpleNamespace

import pytest

from tools import supervised_development_runtime as runtime_module
from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_runtime import SupervisedDevelopmentRuntime


def test_worker_watchdog_never_adds_cleanup_grace_to_signed_deadline(monkeypatch):
    waits = []
    killed = []
    spawn = []

    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired("disposable-worker", timeout)
            return "", ""

    def launch(command, **kwargs):
        spawn.append((command, kwargs))
        return Process()

    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})
    monkeypatch.setattr(runtime_module.subprocess, "Popen", launch)
    monkeypatch.setattr(runtime_module.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    runtime = object.__new__(SupervisedDevelopmentRuntime)
    with pytest.raises(LifecycleError, match="development_worker_timeout"):
        runtime._run_worker_subprocess("disposable-task", SimpleNamespace(deadline_seconds=120))
    assert waits == [120, 5]
    assert killed == [(987654, signal.SIGKILL)]
    assert spawn[0][0] == [
        "/usr/bin/python3",
        "-I",
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
    runtime = object.__new__(SupervisedDevelopmentRuntime)
    with pytest.raises(type(interruption)):
        runtime._run_worker_subprocess("disposable-task", SimpleNamespace(deadline_seconds=120))
    assert events == [("wait", 120), ("kill", 987654, signal.SIGKILL), ("wait", 5)]


def test_worker_cleanup_uncertainty_is_not_reported_as_safe_failure(monkeypatch):
    class Process:
        pid = 987654

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("disposable-worker", timeout)

    monkeypatch.setattr(runtime_module, "installed_policy", lambda: {"autonomy_uid": 12345})
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(runtime_module.os, "killpg", lambda *args: None)
    runtime = object.__new__(SupervisedDevelopmentRuntime)
    with pytest.raises(LifecycleError, match="development_worker_cleanup_unconfirmed"):
        runtime._run_worker_subprocess("disposable-task", SimpleNamespace(deadline_seconds=120))


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
    runtime = object.__new__(SupervisedDevelopmentRuntime)
    expected = LifecycleError if helper_fails else KeyboardInterrupt
    with pytest.raises(expected):
        runtime._run_worker_subprocess("disposable-task", SimpleNamespace(deadline_seconds=120))
    assert identity == [("groups", []), ("gid", 12346), ("uid", 12345)]
    assert helpers[0][0][-1] == "987654"
    assert helpers[0][1]["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
    assert helpers[0][1]["timeout"] == 5
    assert waits == ([120] if helper_fails else [120, 5])
