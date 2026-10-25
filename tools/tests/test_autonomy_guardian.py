"""Unprivileged Guardian certification: never install, invoke sudo, or stop host units."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import autonomy_guardian as g
from tools import autonomy_guardian_install as installer


@pytest.fixture
def guardian(tmp_path):
    base = tmp_path / "state"
    base.mkdir(mode=0o700)
    (base / "audit").mkdir(mode=0o700)
    for path in [base / "control.lock", base / "audit/events.jsonl"]:
        path.touch(mode=0o600)
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "version": 1,
                "operator_uid": 1000,
                "autonomy_account": g.ACCOUNT,
                "privileged_operations": [],
            }
        )
    )
    policy.chmod(0o600)
    (base / "installed.json").write_text(
        json.dumps({"files": {str(policy): hashlib.sha256(policy.read_bytes()).hexdigest()}})
    )
    controller = g.Guardian(base, policy, os.getuid(), check_parents=False)
    (base / "installed.json").chmod(0o600)
    controller.write_state("STOPPED")
    assert controller.status()["trusted"] is True
    return controller


def test_stop_persists_after_reload_and_revokes_generation(guardian):
    guardian.write_state("ACTIVE")
    generation = guardian.state()["generation"]
    terminate = Mock()
    guardian.stop(1000, terminate)
    terminate.assert_called_once_with()
    restored = g.Guardian(guardian.base, guardian.policy_path, os.getuid(), False)
    assert restored.status()["state"] == "STOPPED"
    assert restored.state()["generation"] != generation


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "[]",
        "null",
        "broken",
        '{"state":"ACTIVE"}',
        '{"version":1,"state":"ACTIVE","generation":"bad"}',
    ],
)
def test_unknown_corrupt_state_fails_closed(guardian, payload):
    (guardian.base / "state.json").write_text(payload)
    assert guardian.status()["state"] == "STOPPED"
    assert guardian.status()["trusted"] is False


@pytest.mark.parametrize("name", ["state.json", "installed.json", "control.lock"])
def test_missing_state_or_lock_cannot_activate(guardian, name):
    (guardian.base / name).unlink()
    if name == "control.lock":
        with pytest.raises(OSError):
            guardian.resume(0, 1000, lambda: True, Mock())
    else:
        assert guardian.status()["trusted"] is False


def test_policy_modified_or_writable_fails_closed(guardian):
    guardian.policy_path.write_text(guardian.policy_path.read_text() + " ")
    assert guardian.status()["trusted"] is False
    guardian.policy_path.chmod(0o666)
    assert guardian.status()["state"] == "STOPPED"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "group_write", "wrong_owner"])
def test_trust_rejects_unsafe_files(tmp_path, kind):
    source = tmp_path / "source"
    source.write_text("safe")
    path = tmp_path / "target"
    if kind == "symlink":
        path.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_text("safe")
        if kind == "group_write":
            path.chmod(0o664)
    with pytest.raises(g.UnsafeState):
        g.trusted(path, os.getuid() + 1 if kind == "wrong_owner" else os.getuid())


@pytest.mark.parametrize(
    "groups",
    [{g.ACCOUNT, "docker"}, {g.ACCOUNT, "sudo"}, {g.ACCOUNT, "aaron"}, {g.ACCOUNT, "lxd"}, set()],
)
def test_identity_rejects_extra_groups(groups):
    user = SimpleNamespace(
        pw_name=g.ACCOUNT,
        pw_uid=998,
        pw_dir="/var/lib/jarvis-autonomy",
        pw_shell="/usr/sbin/nologin",
    )
    with pytest.raises(g.UnsafeState):
        g.validate_identity(user, groups)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pw_uid", 0),
        ("pw_uid", 1000),
        ("pw_shell", "/bin/bash"),
        ("pw_dir", "/home/aaron"),
        ("pw_name", "aaron"),
    ],
)
def test_identity_rejects_privileged_or_shared_account(field, value):
    user = SimpleNamespace(
        pw_name=g.ACCOUNT,
        pw_uid=998,
        pw_dir="/var/lib/jarvis-autonomy",
        pw_shell="/usr/sbin/nologin",
    )
    setattr(user, field, value)
    with pytest.raises(g.UnsafeState):
        g.validate_identity(user, {g.ACCOUNT})


@pytest.mark.parametrize("euid,actor", [(998, 998), (998, 1000), (0, 998), (1000, 1000)])
def test_autonomous_or_unprivileged_actor_cannot_resume(guardian, euid, actor):
    start = Mock()
    with pytest.raises(g.UnsafeState):
        guardian.resume(euid, actor, lambda: True, start)
    start.assert_not_called()
    assert guardian.status()["state"] == "STOPPED"


def test_operator_can_resume_only_ready_runtime(guardian):
    start = Mock(return_value=True)
    with pytest.raises(g.UnsafeState):
        guardian.resume(0, 1000, lambda: False, start)
    start.assert_not_called()
    guardian.resume(0, 1000, lambda: True, start)
    start.assert_called_once_with()
    assert guardian.status()["state"] == "ACTIVE"


def test_signed_resume_epoch_checked_under_guardian_lock(guardian):
    expected = guardian.state()["generation"]
    guardian.stop(1000, Mock())
    start = Mock(return_value=True)
    with pytest.raises(g.UnsafeState, match="stale"):
        guardian.resume(0, 1000, lambda: True, start, Mock(), expected_generation=expected)
    start.assert_not_called()
    assert guardian.status()["state"] == "STOPPED"
    guardian.resume(
        0, 1000, lambda: True, start, Mock(), expected_generation=guardian.state()["generation"]
    )
    assert guardian.status()["state"] == "ACTIVE"


@pytest.mark.parametrize(
    "failure", [False, subprocess.TimeoutExpired("fixed-start", 8), OSError("test")]
)
def test_failed_start_restores_stop_and_terminates(guardian, failure):
    start = Mock(return_value=False) if failure is False else Mock(side_effect=failure)
    terminate = Mock()
    with pytest.raises((g.UnsafeState, subprocess.TimeoutExpired, OSError)):
        guardian.resume(0, 1000, lambda: True, start, terminate)
    terminate.assert_called_once_with()
    assert guardian.status()["state"] == "STOPPED"


@pytest.mark.parametrize("broken", ["audit/events.jsonl", "control.lock"])
def test_stop_kills_even_when_audit_or_lock_broken(guardian, broken):
    (guardian.base / broken).unlink()
    terminate = Mock()
    with pytest.raises(OSError):
        guardian.stop(1000, terminate)
    terminate.assert_called_once_with()


def test_audit_append_is_content_free_and_chained(guardian):
    guardian.audit("installed", 1000)
    path = guardian.base / "audit/events.jsonl"
    before = path.read_bytes()
    guardian.stop(1000, Mock())
    assert path.read_bytes().startswith(before)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[1]["previous"] == records[0]["hash"]
    assert set(records[1]) == {"event", "actor_uid", "at", "previous", "hash"}
    with pytest.raises(g.UnsafeState):
        guardian.audit("arbitrary private content", 1000)


def test_stop_targets_only_fixed_autonomy_unit_and_identity(monkeypatch):
    systemctl = Mock(return_value=True)
    run = Mock()
    monkeypatch.setattr(g, "identity", lambda: 998)
    monkeypatch.setattr(g, "fixed_systemctl", systemctl)
    monkeypatch.setattr(g.subprocess, "run", run)
    g.terminate_autonomy()
    assert all(call.args[-1] == g.UNIT for call in systemctl.call_args_list)
    assert run.call_args.args[0] == ["/usr/bin/pkill", "-KILL", "-u", "998"]
    assert run.call_args.kwargs.get("shell", False) is False


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["resume", "anything"],
        ["shell"],
        ["deploy"],
        ["sudo", "bash"],
        ["stop; reboot"],
        ["--policy", "/tmp/policy"],
    ],
)
def test_cli_rejects_arbitrary_privileged_arguments(arguments):
    assert g.main(arguments) == 2


def test_nonroot_cannot_use_environment_to_resume(monkeypatch):
    monkeypatch.setattr(g.os, "geteuid", lambda: 998)
    monkeypatch.setenv("SUDO_UID", "1000")
    assert g.main(["resume"]) == 1


def test_installer_check_has_no_host_side_effects(monkeypatch, capsys):
    install = Mock(side_effect=AssertionError("must not install"))
    monkeypatch.setattr(installer, "install", install)
    assert installer.main(["--check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["host_modified"] is False
    assert report["autonomy_on_install"] == "STOPPED"
    install.assert_not_called()


def test_installer_requires_local_operator_sudo(monkeypatch):
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    with pytest.raises(RuntimeError, match="sudo as Aaron"):
        installer.install()


def test_sudoers_exact_controls_only(tmp_path):
    policy = installer.sudo_policy("aaron")
    grants = [line for line in policy.splitlines() if line.startswith("aaron ")]
    assert len(grants) == 1
    assert grants[0].split("NOPASSWD: ")[1].split(", ") == [
        "/usr/local/libexec/jarvis-autonomy/control stop",
        "/usr/local/libexec/jarvis-autonomy/control resume",
    ]
    assert "*" not in policy
    with pytest.raises(RuntimeError):
        installer.sudo_policy(g.ACCOUNT)
    path = tmp_path / "sudoers"
    path.write_text(policy)
    result = subprocess.run(["/usr/sbin/visudo", "-cf", str(path)], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()


def test_fixed_units_isolate_worker_and_do_not_touch_normal_services():
    root = Path(__file__).resolve().parents[2]
    unit = (root / "systemd/autonomy/jarvis-autonomy.service").read_text()
    for required in [
        "User=jarvis-autonomy",
        "Group=jarvis-autonomy",
        "NoNewPrivileges=true",
        "CapabilityBoundingSet=\n",
        "ProtectHome=true",
        "ProtectSystem=strict",
        "BindsTo=jarvis-autonomy-guardian.service",
        "KillMode=control-group",
        "InaccessiblePaths=-/run/docker.sock",
        "check-active",
    ]:
        assert required in unit
    assert "[Install]" not in unit
    assert "SupplementaryGroups=" not in unit
    assert "jarvis-core" not in unit and "jarvis-developer" not in unit
    guardian_unit = (root / "systemd/autonomy/jarvis-autonomy-guardian.service").read_text()
    assert "User=root" in guardian_unit
    assert "PrivateNetwork=true" in guardian_unit
    assert "ReadWritePaths=/var/lib/jarvis-autonomy-guardian" in guardian_unit


def test_installer_exclusive_write_refuses_symlink_or_overwrite(tmp_path):
    existing = tmp_path / "existing"
    existing.write_bytes(b"preserve")
    linked = tmp_path / "linked"
    linked.symlink_to(existing)
    for path in [existing, linked]:
        with pytest.raises(FileExistsError):
            installer.write(path, b"replace", 0o600)
    assert existing.read_bytes() == b"preserve"


def test_policy_cannot_enable_privileged_commands(guardian):
    value = json.loads(guardian.policy_path.read_text())
    value["privileged_operations"] = ["docker", "deploy", "shell"]
    guardian.policy_path.write_text(json.dumps(value))
    assert guardian.status()["state"] == "STOPPED"
    with pytest.raises(g.UnsafeState):
        guardian.policy()


@pytest.fixture
def host_rehearsal(tmp_path, monkeypatch):
    """Real temp files, simulated root/account/systemd; no privileged execution."""
    owner = os.getuid()
    for name in ["libexec", "config", "state", "units", "bin", "sudoers", "homes"]:
        (tmp_path / name).mkdir(mode=0o700)
    lib = tmp_path / "libexec/jarvis-autonomy"
    etc = tmp_path / "config/jarvis-autonomy"
    base = tmp_path / "state/jarvis-autonomy-guardian"
    home = tmp_path / "homes/jarvis-autonomy"
    sudoers = tmp_path / "sudoers/jarvis-autonomy"
    destinations = {}
    for source, (destination, mode) in installer.FILES.items():
        if destination.parent == installer.LIB:
            target = lib / destination.name
        elif destination.suffix == ".service":
            target = tmp_path / "units" / destination.name
        else:
            target = tmp_path / "bin" / destination.name
        destinations[source] = (target, mode)
    for name, value in {
        "LIB": lib,
        "ETC": etc,
        "BASE": base,
        "AUTONOMY_HOME": home,
        "SUDOERS": sudoers,
        "FILES": destinations,
    }.items():
        monkeypatch.setattr(installer, name, value)
    account_created = []

    def get_user(name):
        if name == "aaron":
            return SimpleNamespace(pw_name="aaron", pw_uid=1000)
        if not account_created:
            raise KeyError(name)
        return SimpleNamespace(pw_gid=os.getgid())

    def command(argv):
        if argv[0] == "/usr/sbin/useradd":
            account_created.append(True)

    run = Mock(side_effect=command)
    terminate = Mock()
    controller = g.Guardian(base, etc / "guardian.json", owner, False)
    real_stop = controller.stop
    controller.stop = lambda uid: real_stop(uid, terminate)
    fake_guardian = SimpleNamespace(
        ACCOUNT=g.ACCOUNT,
        GUARDIAN_UNIT=g.GUARDIAN_UNIT,
        trusted=lambda path, **kwargs: g.trusted(path, owner, **kwargs),
        identity=lambda: owner,
        Guardian=lambda: controller,
    )

    # Parent checks above the temporary root are covered separately by trust tests.
    def trust_test_path(path, **kwargs):
        if path == tmp_path or tmp_path in path.parents:
            g.trusted(path, owner, **kwargs)

    fake_guardian.trusted = trust_test_path
    monkeypatch.setattr(installer, "load_guardian", lambda _: fake_guardian)
    monkeypatch.setattr(installer, "run", run)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(installer.os, "chown", Mock())
    monkeypatch.setattr(installer.pwd, "getpwnam", get_user)
    monkeypatch.setenv("SUDO_USER", "aaron")
    monkeypatch.setenv("SUDO_UID", "1000")
    original_tempdir = installer.tempfile.TemporaryDirectory
    monkeypatch.setattr(
        installer.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: original_tempdir(prefix=kwargs["prefix"], dir=tmp_path),
    )
    return SimpleNamespace(
        controller=controller,
        run=run,
        terminate=terminate,
        created=account_created,
        root=tmp_path,
        sudoers=sudoers,
    )


def test_installation_rehearsal_stopped_idempotent_and_private(host_rehearsal):
    host = host_rehearsal
    installer.install()
    assert host.controller.status()["state"] == "STOPPED"
    assert host.controller.status()["trusted"] is True
    assert host.sudoers.stat().st_mode & 0o777 == 0o440
    assert (host.controller.base / "audit/events.jsonl").stat().st_mode & 0o777 == 0o600
    calls = [call.args[0] for call in host.run.call_args_list]
    assert ["/usr/bin/systemctl", "enable", "--now", g.GUARDIAN_UNIT] in calls
    assert not any(g.UNIT in args for args in calls)
    assert not any("docker" in args or "usermod" in args for args in calls)
    audit = (host.controller.base / "audit/events.jsonl").read_bytes()
    installer.install()
    assert len(host.created) == 1
    assert host.controller.status()["state"] == "STOPPED"
    assert (host.controller.base / "audit/events.jsonl").read_bytes().startswith(audit)


def test_installer_refuses_conflicting_paths_without_creating_user(host_rehearsal):
    installer.LIB.mkdir()
    with pytest.raises(RuntimeError, match="Conflicting"):
        installer.install()
    assert not host_rehearsal.created
    host_rehearsal.run.assert_not_called()


def test_reinstall_refuses_modified_root_payload(host_rehearsal):
    installer.install()
    target = next(iter(installer.FILES.values()))[0]
    target.write_text("unexpected alteration")
    host_rehearsal.run.reset_mock()
    with pytest.raises(g.UnsafeState):
        installer.install()
    host_rehearsal.run.assert_not_called()


def test_installer_refuses_different_bundle_without_overwrite(host_rehearsal, monkeypatch):
    installer.install()
    marker = host_rehearsal.controller.base / "installed.json"
    before = marker.read_bytes()
    monkeypatch.setattr(installer, "bundle_hash", lambda: "0" * 64)
    with pytest.raises(RuntimeError, match="Different installed bundle"):
        installer.install()
    assert marker.read_bytes() == before


@pytest.mark.parametrize(
    "unsafe", ["none", "policy_write", "docker_write", "audit_read", "sudo_grant"]
)
def test_installed_identity_check_detects_authority_leaks(monkeypatch, guardian, unsafe):
    monkeypatch.setattr(g, "identity", lambda: 998)
    monkeypatch.setattr(g.os, "geteuid", lambda: 998)

    def access(path, mode):
        return (
            (unsafe == "policy_write" and path == g.POLICY)
            or (unsafe == "docker_write" and path == Path("/run/docker.sock"))
            or (unsafe == "audit_read" and path == g.BASE / "audit/events.jsonl")
        )

    monkeypatch.setattr(g.os, "access", access)
    run = Mock(return_value=SimpleNamespace(returncode=0 if unsafe == "sudo_grant" else 1))
    monkeypatch.setattr(g.subprocess, "run", run)
    assert g.verify_isolation(guardian) is (unsafe == "none")
    if run.called:
        assert run.call_args.args[0] == ["/usr/bin/sudo", "-n", "-l"]


def test_guardian_restart_forces_stopped_without_replaying_work(monkeypatch, guardian):
    guardian.write_state("ACTIVE")
    terminate = Mock()
    original_stop = guardian.stop
    guardian.stop = lambda uid: original_stop(uid, terminate)
    monkeypatch.setattr(g, "terminate_autonomy", terminate)
    monkeypatch.setattr(g, "identity", lambda: 998)
    monkeypatch.setattr(g.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        g.watch(guardian)
    assert guardian.status()["state"] == "STOPPED"
    assert terminate.call_count >= 2
