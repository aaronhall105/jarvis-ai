import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import autonomy_broker_install as installer
from tools import autonomy_guardian as g
from tools.autonomy_broker import Denied, canonical

INSTALLED_SOURCE = "8140346f5e5c05c34445b0c7ea3137f39c7f35ee"
ADDED_MODULES = {
    "supervised_development_environment.py",
    "supervised_development_environment.lock.json",
}


def _reviewed_source_payload(files):
    """Reconstruct ALL old payload bytes, never relabel the new bundle as old."""
    previous = {}
    for path in installer.current_payload_paths():
        mode = 0o644
        if path == installer.LIB / "tools/__init__.py":
            data = b"# Protected broker package.\n"
        else:
            if path == installer.DROPIN:
                source = "systemd/autonomy/broker-worker-access.conf"
            elif path.parent == installer.UNITS:
                source = "systemd/autonomy/" + path.name
            elif path == installer.LIB / "entry.py":
                source = "tools/autonomy_broker_entry.py"
            elif path.name == "supervised_development_scope.py":
                source = "bridge/app/development_scope.py"
            else:
                source = "tools/" + path.name
            data = subprocess.check_output(
                ["git", "-C", str(installer.ROOT), "show", INSTALLED_SOURCE + ":" + source]
            )
        previous[path] = (data, mode)
    return previous


@pytest.fixture
def rehearsal(tmp_path, monkeypatch):
    """Real disposable files; simulated root/systemd. Never invokes sudo/Docker."""
    for directory in ("libexec", "etc", "var", "units"):
        (tmp_path / directory).mkdir(mode=0o755)
    guardian_base = tmp_path / "var/guardian"
    guardian_base.mkdir(mode=0o755)
    (guardian_base / "audit").mkdir(mode=0o700)
    for name in ("control.lock", "audit/events.jsonl"):
        (guardian_base / name).touch(mode=0o600)
    guardian_policy = tmp_path / "etc/guardian.json"
    guardian_policy.write_bytes(
        canonical(
            {
                "version": 1,
                "operator_uid": 1000,
                "autonomy_account": "jarvis-autonomy",
                "privileged_operations": [],
            }
        )
    )
    guardian_policy.chmod(0o644)
    marker = guardian_base / "installed.json"
    marker.write_bytes(
        canonical(
            {
                "version": 1,
                "bundle": "original-guardian-bundle",
                "files": {
                    str(guardian_policy): hashlib.sha256(guardian_policy.read_bytes()).hexdigest()
                },
            }
        )
    )
    marker.chmod(0o644)
    controller = g.Guardian(guardian_base, guardian_policy, os.getuid(), False)
    controller.write_state("STOPPED")
    stop = controller.stop
    controller.stop = lambda actor: stop(actor, Mock())
    original_manifest = marker.read_bytes()
    locations = {
        "LIB": tmp_path / "libexec/broker",
        "BASE": tmp_path / "var/broker",
        "JOBS": tmp_path / "var/jobs",
        "POLICY": tmp_path / "etc/broker.json",
        "UNITS": tmp_path / "units",
        "DROPIN": tmp_path / "units/jarvis-autonomy.service.d/broker.conf",
    }
    for name, location in locations.items():
        monkeypatch.setattr(installer, name, location)
    real_trusted = g.trusted

    def trusted(path, uid=0, *, directory=False):
        # tmp_path is the emulated host '/', not its real /tmp ancestors.
        if Path(path) in tmp_path.parents:
            return None
        actual_uid = os.getuid() if Path(path).is_relative_to(tmp_path) else uid
        return real_trusted(path, actual_uid, directory=directory)

    monkeypatch.setattr(g, "trusted", trusted)
    monkeypatch.setattr(g, "Guardian", lambda: controller)
    monkeypatch.setattr(g, "identity", lambda: 995)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "aaron")
    monkeypatch.setenv("SUDO_UID", "1000")
    commands = Mock()
    monkeypatch.setattr(installer, "command", commands)
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[:2] == ["/usr/bin/systemctl", "show"]:
            active = "active" if argv[-1].endswith(".socket") else "inactive"
            return subprocess.CompletedProcess(
                argv, 0, f"LoadState=loaded\nUnitFileState=enabled\nActiveState={active}\n", ""
            )
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    files = installer.payload()
    policy = {"operator_uid": 1000, "autonomy_uid": 995}
    return files, policy, controller, commands, original_manifest, locations


def test_guardian_only_upgrade_and_repeat_are_safe(rehearsal):
    files, policy, controller, commands, original, paths = rehearsal
    assert installer.checked_existing(files, policy)[0] == "UPGRADE_GUARDIAN_ONLY"
    installer.install(files, policy)
    assert controller.status()["state"] == "STOPPED"
    controller.integrity()
    original_files = json.loads(original)["files"]
    manifest = json.loads((controller.base / "installed.json").read_bytes())
    assert all(manifest["files"][name] == digest for name, digest in original_files.items())
    for path, (data, mode) in files.items():
        assert path.read_bytes() == data
        assert path.stat().st_mode & 0o777 == mode
    assert (paths["BASE"] / "records").stat().st_mode & 0o777 == 0o700
    before = {p: p.read_bytes() for p in files}
    installer.install(files, policy)
    assert {p: p.read_bytes() for p in files} == before
    assert controller.status()["state"] == "STOPPED"
    assert all(
        "usermod" not in str(call) and "useradd" not in str(call) and "docker" not in str(call)
        for call in commands.call_args_list
    )
    assert all(
        "jarvis-developer.service" not in str(call) and "jarvis-core" not in str(call)
        for call in commands.call_args_list
    )
    assert not list(paths["BASE"].rglob("*credential*"))


def test_payload_contains_development_admission_endpoint(rehearsal):
    files, _policy, _controller, _commands, _original, _paths = rehearsal
    assert any(path.name == "supervised_development_broker.py" for path in files)
    assert any(path.name == "jarvis-autonomy-broker-development-admission.socket" for path in files)
    assert any(
        path.name == "jarvis-autonomy-broker-development-admission.service" for path in files
    )
    assert any(path.name == "supervised_development_publication.py" for path in files)
    assert any(
        path.name == "jarvis-autonomy-broker-development-publication.socket" for path in files
    )
    assert any(
        path.name == "jarvis-autonomy-broker-development-publication.service" for path in files
    )


def test_controller_unit_uses_pinned_database_group_without_broad_dac_access(rehearsal):
    files, _policy, _controller, _commands, _original, _paths = rehearsal
    unit = next(
        data.decode("utf-8")
        for path, (data, _mode) in files.items()
        if path.name == "jarvis-autonomy-broker-development-controller.service"
    )
    assert "User=root" in unit
    assert "Group=root" in unit
    assert "SupplementaryGroups=jarvis-autonomy" in unit
    assert "CAP_DAC_READ_SEARCH" in unit
    assert "CAP_DAC_OVERRIDE" not in unit
    assert "ReadWritePaths=/var/lib/jarvis-autonomy-broker/work" in unit


def test_modified_protected_payload_refused(rehearsal):
    files, policy, controller, _, _, _ = rehearsal
    installer.install(files, policy)
    target = next(iter(files))
    target.write_bytes(target.read_bytes() + b"# tampered\n")
    with pytest.raises(Denied, match="modified_protected_payload"):
        installer.install(files, policy)
    assert target.read_bytes().endswith(b"# tampered\n")
    assert controller.status()["state"] == "STOPPED"


def test_partial_or_unknown_install_is_never_overwritten(rehearsal):
    files, policy, _, _, _, paths = rehearsal
    paths["BASE"].mkdir()
    evidence = paths["BASE"] / "unknown"
    evidence.write_bytes(b"preserve")
    with pytest.raises(Denied, match="unknown_or_partial"):
        installer.install(files, policy)
    assert evidence.read_bytes() == b"preserve"


def test_failed_upgrade_restores_original_guardian_only(rehearsal, monkeypatch):
    files, policy, controller, _, original, paths = rehearsal

    def fail_enable(argv):
        if "enable" in argv:
            raise OSError("synthetic activation failure")

    monkeypatch.setattr(installer, "command", fail_enable)
    with pytest.raises(OSError):
        installer.install(files, policy)
    assert (controller.base / "installed.json").read_bytes() == original
    controller.integrity()
    assert controller.status()["state"] == "STOPPED"
    assert not paths["BASE"].exists()
    assert not paths["LIB"].exists()
    assert not any(path.exists() for path in files)
    assert (controller.base / "audit/events.jsonl").stat().st_size > 0


def test_different_reviewed_bundle_requires_review_not_overwrite(rehearsal):
    files, policy, _, _, _, _ = rehearsal
    installer.install(files, policy)
    changed = dict(files)
    target = next(iter(changed))
    changed[target] = (b"different bundle", 0o644)
    with pytest.raises(Denied, match="different_reviewed_broker_bundle"):
        installer.install(changed, policy)


def test_install_refuses_non_operator(rehearsal, monkeypatch):
    files, policy, _, _, _, _ = rehearsal
    monkeypatch.setenv("SUDO_USER", "jarvis-autonomy")
    with pytest.raises(Denied, match="local_operator_sudo_required"):
        installer.install(files, policy)


def test_check_has_no_install_or_host_mutation(rehearsal, monkeypatch, capsys):
    _, policy, _, commands, _, _ = rehearsal
    monkeypatch.setattr(installer, "host_policy", lambda: policy)
    policy.update(tool_image="fake", core_base_image="fake")
    monkeypatch.setattr(installer, "install", lambda *_: pytest.fail("must not install"))
    assert installer.main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["host_modified"] is False
    commands.assert_not_called()


def test_unknown_privileged_arguments_rejected():
    assert installer.main(["bash", "-c", "id"]) == 2


def test_public_guardian_status_does_not_require_private_broker_reads(rehearsal, monkeypatch):
    files, policy, controller, _, _, paths = rehearsal
    installer.install(files, policy)
    private = paths["BASE"] / "docker-client/config.json"
    secure = controller.secure

    def public_secure(path, **kwargs):
        if path == private:
            raise PermissionError("private Docker configuration is correctly unreadable")
        return secure(path, **kwargs)

    monkeypatch.setattr(controller, "secure", public_secure)
    assert controller.status()["trusted"] is True
    # The private hash must still be checked by the privileged broker itself.
    manifest = json.loads((paths["BASE"] / "installed.json").read_bytes())
    assert manifest["files"][str(private)] == hashlib.sha256(private.read_bytes()).hexdigest()
    assert private.stat().st_mode & 0o777 == 0o600


def _make_legacy_install(rehearsal, monkeypatch):
    files, policy, controller, commands, original, paths = rehearsal
    installer.install(files, policy)
    marker = paths["BASE"] / "installed.json"
    current = json.loads(marker.read_bytes())
    development = {
        str(path)
        for path in files
        if "development-publication" in str(path)
        or "development-controller" in str(path)
        or path.name
        in {
            "supervised_development_controller.py",
            "supervised_development_edits.py",
            "supervised_development_environment.py",
            "supervised_development_environment.lock.json",
            "supervised_development_pipeline.py",
            "supervised_development_publication.py",
            "supervised_development_review.py",
            "supervised_development_runtime.py",
            "supervised_development_scope.py",
            "supervised_development_worker.py",
        }
    }
    for path in development:
        Path(path).unlink()
        current["files"].pop(path, None)
    current["bundle"] = installer.LEGACY_BUNDLE
    marker.write_bytes(canonical(current))
    guardian_marker = controller.base / "installed.json"
    guardian = json.loads(guardian_marker.read_bytes())
    for path in development:
        guardian["files"].pop(path, None)
    guardian["files"][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
    guardian_marker.write_bytes(canonical(guardian))
    assert len(current["files"]) == 24
    monkeypatch.setattr(installer, "UPGRADE_BUNDLE", installer.fingerprint(files))
    return files, policy, controller, commands, paths


def test_reviewed_legacy_upgrade_adds_development_endpoint_atomically(rehearsal, monkeypatch):
    files, policy, controller, commands, paths = _make_legacy_install(rehearsal, monkeypatch)
    installer.upgrade(files, policy)
    assert controller.status()["state"] == "STOPPED"
    assert all(path.exists() for path in files)
    upgraded = json.loads((paths["BASE"] / "installed.json").read_bytes())
    assert upgraded["bundle"] == installer.UPGRADE_BUNDLE
    assert any("development-admission" in name for name in upgraded["files"])
    assert all("docker-client/config.json" not in str(call) for call in commands.call_args_list)


def test_reviewed_bundle_ids_match_current_payload_and_installed_source():
    assert (
        installer.LEGACY_BUNDLE
        == "b7c72340c0d19b320928fc50fc07513389374f22584dfd3801d22e655cdde3eb"
    )
    assert installer.fingerprint(installer.payload()) == installer.UPGRADE_BUNDLE
    assert installer.CURRENT_BUNDLE == (
        "73e84548db287fb02daa86d9da8634ae62fec39f615322979e9e74dfd1f93489"
    )
    assert installer.UPGRADE_BUNDLE == installer.fingerprint(installer.payload())
    baseline_files = _reviewed_source_payload(installer.payload())
    assert installer.fingerprint(baseline_files) == installer.CURRENT_BUNDLE


def test_actual_installed_payload_identity_is_reconstructed_completely():
    baseline = _reviewed_source_payload(installer.payload())
    assert len(baseline) == 33  # Plus retained private configuration and policy = 35.
    assert installer.fingerprint(baseline) == installer.CURRENT_BUNDLE


def test_installed_edit_scope_is_dependency_free_not_mutable_core_import(tmp_path):
    files = installer.payload()
    target = installer.LIB / "tools/supervised_development_scope.py"
    assert files[target][0] == (installer.ROOT / "bridge/app/development_scope.py").read_bytes()
    assert b"from bridge" not in files[target][0]
    assert all(path.name != "supervised_development_integration.py" for path in files)
    standalone = tmp_path / "installed_scope.py"
    standalone.write_bytes(files[target][0])
    script = (
        "import importlib.util,sys; "
        "s=importlib.util.spec_from_file_location('installed_scope',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "assert m.editable_path('bridge/app/ai_engine.py'); "
        "assert not m.editable_path('bridge/app/main.py')"
    )
    subprocess.run(
        ["/usr/bin/python3", "-I", "-c", script, str(standalone)],
        cwd=tmp_path,
        check=True,
        timeout=10,
    )


def test_reviewed_current_bundle_migration_accepts_existing_upgraded_layout(rehearsal, monkeypatch):
    files, policy, controller, commands, _original, paths = rehearsal
    monkeypatch.setattr(installer, "UPGRADE_BUNDLE", installer.fingerprint(files))
    installer.install(_reviewed_source_payload(files), policy)
    marker = paths["BASE"] / "installed.json"
    value = json.loads(marker.read_bytes())
    value["bundle"] = installer.CURRENT_BUNDLE
    marker.write_bytes(canonical(value))
    guardian_marker = controller.base / "installed.json"
    guardian = json.loads(guardian_marker.read_bytes())
    guardian["files"][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
    guardian_marker.write_bytes(canonical(guardian))
    installer.upgrade(files, policy)
    assert json.loads(marker.read_bytes())["bundle"] == installer.UPGRADE_BUNDLE
    assert controller.status()["state"] == "STOPPED"
    assert commands.called


def _current_workspace_fixture(rehearsal, monkeypatch):
    files, policy, guardian, commands, _original, paths = rehearsal
    installer.install(_reviewed_source_payload(files), policy)
    marker = paths["BASE"] / "installed.json"
    value = json.loads(marker.read_bytes())
    value["bundle"] = installer.CURRENT_BUNDLE
    marker.write_bytes(canonical(value))
    guardian_marker = guardian.base / "installed.json"
    manifest = json.loads(guardian_marker.read_bytes())
    manifest["files"][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
    guardian_marker.write_bytes(canonical(manifest))
    monkeypatch.setattr(installer, "UPGRADE_BUNDLE", installer.fingerprint(files))
    work = paths["BASE"] / "work"
    work.mkdir(mode=0o770)
    work.chmod(0o770)
    worktrees = paths["BASE"] / "worktrees"
    worktrees.mkdir(mode=0o770)
    worktrees.chmod(0o770)
    commands.reset_mock()
    return files, policy, guardian, commands, paths


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_current_upgrade_rejects_untrusted_database_inode_without_changing_target(
    rehearsal, monkeypatch, kind
):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    victim = paths["BASE"].parent / "unrelated-protected-fixture"
    victim.write_bytes(b"unchanged unrelated contents")
    victim.chmod(0o640)
    before = victim.stat()
    database = paths["BASE"] / "work/supervised-development.sqlite3"
    if kind == "symlink":
        database.symlink_to(victim)
    elif kind == "hardlink":
        os.link(victim, database)
    else:
        os.mkfifo(database)
    before_guardian = (guardian.base / "installed.json").read_bytes()
    with pytest.raises(Denied, match="upgrade_workspace_metadata_untrusted"):
        installer.upgrade(files, policy)
    assert victim.read_bytes() == b"unchanged unrelated contents"
    assert (victim.stat().st_mode, victim.stat().st_uid, victim.stat().st_gid) == (
        before.st_mode,
        before.st_uid,
        before.st_gid,
    )
    assert (guardian.base / "installed.json").read_bytes() == before_guardian
    commands.assert_not_called()


def test_database_swap_during_metadata_change_never_chmods_symlink_target(rehearsal, monkeypatch):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    database = paths["BASE"] / "work/supervised-development.sqlite3"
    database.write_bytes(b"preserve original database")
    database.chmod(0o640)
    original_inode = database.stat().st_ino
    retained = database.with_name("retained-database")
    victim = paths["BASE"].parent / "unrelated-protected-fixture"
    victim.write_bytes(b"untouched")
    victim.chmod(0o600)
    original_chmod = os.fchmod
    swapped = False

    def race(descriptor, mode):
        nonlocal swapped
        original_chmod(descriptor, mode)
        if not swapped and os.fstat(descriptor).st_ino == original_inode:
            swapped = True
            database.rename(retained)
            database.symlink_to(victim)

    monkeypatch.setattr(installer.os, "fchmod", race)
    before_marker = (paths["BASE"] / "installed.json").read_bytes()
    with pytest.raises(Denied, match="upgrade_workspace_reconciliation_required"):
        installer.upgrade(files, policy)
    assert swapped
    assert victim.read_bytes() == b"untouched"
    assert victim.stat().st_mode & 0o777 == 0o600
    assert retained.read_bytes() == b"preserve original database"
    assert retained.stat().st_mode & 0o777 == 0o640
    assert (paths["BASE"] / "installed.json").read_bytes() == before_marker
    assert guardian.status()["state"] == "STOPPED"
    commands.assert_not_called()


def test_correct_existing_workspace_permissions_need_no_metadata_changes(rehearsal, monkeypatch):
    _files, policy, _guardian, _commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    database = paths["BASE"] / "work/supervised-development.sqlite3"
    database.write_bytes(b"unchanged")
    database.chmod(0o660)
    chmod = Mock(wraps=os.fchmod)
    chown = Mock(wraps=os.fchown)
    monkeypatch.setattr(installer.os, "fchmod", chmod)
    monkeypatch.setattr(installer.os, "fchown", chown)
    metadata = {}
    try:
        installer._prepare_development_workspace(policy, metadata)
    finally:
        for descriptor, _info in metadata.values():
            os.close(descriptor)
    chmod.assert_not_called()
    chown.assert_not_called()


def test_upgrade_rejects_unknown_legacy_bundle_without_changes(rehearsal, monkeypatch):
    files, policy, controller, commands, paths = _make_legacy_install(rehearsal, monkeypatch)
    marker = paths["BASE"] / "installed.json"
    value = json.loads(marker.read_bytes())
    value["bundle"] = "unreviewed"
    marker.write_bytes(canonical(value))
    before = marker.read_bytes()
    commands.reset_mock()
    with pytest.raises(Denied, match="upgrade_source_bundle_not_reviewed"):
        installer.upgrade(files, policy)
    assert marker.read_bytes() == before
    assert controller.status()["state"] == "STOPPED"
    commands.assert_not_called()


@pytest.mark.parametrize(
    "failure", [None, "activation", "modified", "unknown", "missing", "extra", "permission"]
)
def test_installed_pr135_payload_to_worker_runtime_preserves_history(
    rehearsal, monkeypatch, failure
):
    files, policy, guardian, commands, _original, paths = rehearsal
    old_files = _reviewed_source_payload(files)
    target = next(p for p in files if p.name == "supervised_development_runtime.py")
    old_data = old_files[target][0]
    installer.install(old_files, policy)
    marker = paths["BASE"] / "installed.json"
    previous = json.loads(marker.read_bytes())
    assert len(previous["files"]) == 35
    previous["bundle"] = installer.CURRENT_BUNDLE
    if failure == "unknown":
        previous["bundle"] = "unreviewed"
    if failure == "extra":
        extra = paths["BASE"] / "unreviewed.py"
        extra.write_bytes(b"unreviewed fixture")
        extra.chmod(0o644)
        previous["files"][str(extra)] = hashlib.sha256(extra.read_bytes()).hexdigest()
    marker.write_bytes(canonical(previous))
    guardian_marker = guardian.base / "installed.json"
    manifest = json.loads(guardian_marker.read_bytes())
    manifest["files"][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
    guardian_marker.write_bytes(canonical(manifest))
    # Preserve opaque fixture data; no production databases or keys are opened.
    data = {}
    for relative in (
        "work/supervised-development.sqlite3",
        "operator/operations.sqlite3",
        "development-admission/operations.sqlite3",
        "records/retained.json",
    ):
        path = paths["BASE"] / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"preserved fixture history")
        path.chmod(0o660 if relative.startswith("work/") else 0o600)
        data[path] = (path.read_bytes(), path.stat().st_mode)
    before_files = {p: p.read_bytes() for p in old_files}
    before_marker = marker.read_bytes()
    before_guardian = guardian_marker.read_bytes()
    before_policy = paths["POLICY"].read_bytes()
    generation = guardian.state()["generation"]
    monkeypatch.setattr(installer, "UPGRADE_BUNDLE", installer.fingerprint(files))
    commands.reset_mock()
    if failure == "modified":
        target.write_bytes(old_data + b"# tampered\n")
    if failure == "missing":
        target.unlink()
    if failure == "permission":
        target.chmod(0o666)
    if failure == "activation":
        failed = False

        def activate(argv):
            nonlocal failed
            if not failed and "enable" in argv:
                failed = True
                raise OSError("fixture activation failed")

        commands.side_effect = activate
    if failure:
        expected = (
            OSError
            if failure == "activation"
            else g.UnsafeState
            if failure == "permission"
            else Denied
        )
        with pytest.raises(expected):
            installer.upgrade(files, policy)
        assert marker.read_bytes() == before_marker
        assert guardian_marker.read_bytes() == before_guardian
        if failure == "activation":
            assert {p: p.read_bytes() for p in old_files} == before_files
            assert all(not p.exists() for p in files if p.name in ADDED_MODULES)
        else:
            commands.assert_not_called()
    else:
        installer.upgrade(files, policy)
        assert target.read_bytes() == files[target][0]
        assert json.loads(marker.read_bytes())["bundle"] == installer.UPGRADE_BUNDLE
        assert len(json.loads(marker.read_bytes())["files"]) == len(files) + 2
    assert paths["POLICY"].read_bytes() == before_policy
    assert guardian.state()["state"] == "STOPPED"
    assert guardian.state()["generation"] == generation
    for path, (contents, mode) in data.items():
        assert path.read_bytes() == contents
        assert path.stat().st_mode == mode


def test_current_source_fingerprint_rejects_self_consistent_tampered_manifest(
    rehearsal, monkeypatch
):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    target = paths["LIB"] / "tools/supervised_development_worker.py"
    target.write_bytes(target.read_bytes() + b"\n# unreviewed bytes\n")
    marker = paths["BASE"] / "installed.json"
    value = json.loads(marker.read_bytes())
    value["files"][str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
    marker.write_bytes(canonical(value))
    before = marker.read_bytes()
    with pytest.raises(Denied, match="upgrade_source_bundle_not_reviewed"):
        installer.upgrade(files, policy)
    assert marker.read_bytes() == before
    assert guardian.status()["state"] == "STOPPED"
    commands.assert_not_called()


@pytest.mark.parametrize(
    "unit_state,reason",
    [
        (
            "LoadState=loaded\nUnitFileState=disabled\nActiveState=active\n",
            "socket_not_enabled_active",
        ),
        (
            "LoadState=loaded\nUnitFileState=enabled\nActiveState=inactive\n",
            "socket_not_enabled_active",
        ),
        ("LoadState=not-found\n", "service_state_unverified"),
    ],
)
def test_upgrade_requires_verified_source_socket_state(rehearsal, monkeypatch, unit_state, reason):
    files, policy, _guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    before = (paths["BASE"] / "installed.json").read_bytes()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, unit_state, ""),
    )
    with pytest.raises(Denied, match=reason):
        installer.upgrade(files, policy)
    assert (paths["BASE"] / "installed.json").read_bytes() == before
    assert not (paths["BASE"] / "upgrade-transaction.json").exists()
    commands.assert_not_called()


def test_upgrade_never_terminates_an_active_controller_as_a_precondition(rehearsal, monkeypatch):
    files, policy, _guardian, commands, _paths = _current_workspace_fixture(rehearsal, monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\n", ""
        ),
    )
    with pytest.raises(Denied, match="upgrade_controller_must_be_inactive"):
        installer.upgrade(files, policy)
    commands.assert_not_called()


def test_uncertain_rollback_preserves_backups_and_blocks_retry(rehearsal, monkeypatch):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    old_marker = (paths["BASE"] / "installed.json").read_bytes()
    activation_failed = False

    def fail(argv):
        nonlocal activation_failed
        if "enable" in argv:
            activation_failed = True
            raise OSError("activation uncertain")
        if activation_failed and "stop" in argv:
            raise OSError("cannot confirm service stopped")

    commands.side_effect = fail
    with pytest.raises(Denied, match="upgrade_service_reconciliation_required"):
        installer.upgrade(files, policy)
    transaction_path = paths["BASE"] / "upgrade-transaction.json"
    transaction = json.loads(transaction_path.read_bytes())
    assert transaction["source_bundle"] == installer.CURRENT_BUNDLE
    assert transaction["target_bundle"] == installer.UPGRADE_BUNDLE
    assert transaction_path.stat().st_mode & 0o777 == 0o600
    assert all(Path(path).is_file() for path in transaction["backups"].values())
    marker_backup = Path(transaction["backups"][str(paths["BASE"] / "installed.json")])
    assert marker_backup.read_bytes() == old_marker
    # No rollback executable replacement while quiescence is uncertain.
    assert all(path.read_bytes() == contents for path, (contents, _mode) in files.items())
    commands.reset_mock()
    with pytest.raises(Denied, match="upgrade_reconciliation_required"):
        installer.upgrade(files, policy)
    commands.assert_not_called()
    assert guardian.status()["state"] == "STOPPED"


def test_successful_upgrade_clears_only_its_own_transaction_artifacts(rehearsal, monkeypatch):
    files, policy, guardian, _commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    retained = paths["BASE"] / "owner-audit-history"
    retained.write_bytes(b"immutable fixture audit evidence")
    installer.upgrade(files, policy)
    assert retained.read_bytes() == b"immutable fixture audit evidence"
    assert not (paths["BASE"] / "upgrade-transaction.json").exists()
    assert not list(paths["BASE"].rglob(".upgrade-stage-*"))
    assert guardian.status()["state"] == "STOPPED"


@pytest.mark.parametrize("fail_activation", [False, True])
def test_initialized_controller_sqlite_and_policy_bytes_survive_migration(
    rehearsal, monkeypatch, fail_activation
):
    from tools.supervised_development_controller import SupervisedDevelopmentController

    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    database = paths["BASE"] / "work/supervised-development.sqlite3"
    controller = SupervisedDevelopmentController(database, database_mode=0o660)
    controller.db.close()
    before_database = database.read_bytes()
    before_info = database.stat()
    policy["operator_public_keys"] = {"fixture-owner": "retained fixture public identity"}
    policy["reviewer_ids"] = ["retained-reviewer"]
    policy_bytes = json.dumps(policy, indent=2).encode() + b"\n"
    paths["POLICY"].write_bytes(policy_bytes)
    marker = paths["BASE"] / "installed.json"
    value = json.loads(marker.read_bytes())
    value["files"][str(paths["POLICY"])] = hashlib.sha256(policy_bytes).hexdigest()
    marker.write_bytes(canonical(value))
    guardian_marker = guardian.base / "installed.json"
    manifest = json.loads(guardian_marker.read_bytes())
    manifest["files"][str(paths["POLICY"])] = hashlib.sha256(policy_bytes).hexdigest()
    manifest["files"][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
    guardian_marker.write_bytes(canonical(manifest))
    failed = False

    def command(argv):
        nonlocal failed
        if fail_activation and not failed and "enable" in argv:
            failed = True
            raise OSError("fixture activation failure")

    commands.side_effect = command
    if fail_activation:
        with pytest.raises(OSError, match="fixture activation failure"):
            installer.upgrade(files, policy)
    else:
        installer.upgrade(files, policy)
    assert paths["POLICY"].read_bytes() == policy_bytes
    assert database.read_bytes() == before_database
    after_info = database.stat()
    assert (after_info.st_ino, after_info.st_uid, after_info.st_gid, after_info.st_mode) == (
        before_info.st_ino,
        before_info.st_uid,
        before_info.st_gid,
        before_info.st_mode,
    )
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as check:
        assert check.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert check.execute("SELECT count(*) FROM tasks").fetchone() == (0,)
    assert guardian.status()["state"] == "STOPPED"


@pytest.mark.parametrize("failure", ["manifest", "interrupted"])
def test_interrupted_or_manifest_failed_upgrade_restores_exact_protected_bundle(
    rehearsal, monkeypatch, failure
):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    old_files = {path: path.read_bytes() for path in installer.current_payload_paths()}
    old_marker = (paths["BASE"] / "installed.json").read_bytes()
    old_guardian = (guardian.base / "installed.json").read_bytes()
    replace_manifest = installer.replace_manifest
    failed = False

    def fail_manifest(path, raw):
        nonlocal failed
        if failure == "manifest" and not failed:
            failed = True
            raise OSError("fixture manifest write failed")
        replace_manifest(path, raw)

    def fail_command(argv):
        nonlocal failed
        if failure == "interrupted" and not failed and "daemon-reload" in argv:
            failed = True
            raise KeyboardInterrupt

    monkeypatch.setattr(installer, "replace_manifest", fail_manifest)
    commands.side_effect = fail_command
    with pytest.raises(OSError if failure == "manifest" else KeyboardInterrupt):
        installer.upgrade(files, policy)
    assert failed
    assert all(path.read_bytes() == data for path, data in old_files.items())
    assert all(not p.exists() for p in files if p.name in ADDED_MODULES)
    assert (paths["BASE"] / "installed.json").read_bytes() == old_marker
    assert (guardian.base / "installed.json").read_bytes() == old_guardian
    assert not (paths["BASE"] / "upgrade-transaction.json").exists()
    assert guardian.status()["state"] == "STOPPED"


def test_unknown_interrupted_transaction_prevents_all_automatic_retry(rehearsal, monkeypatch):
    files, policy, _guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    record = paths["BASE"] / "upgrade-transaction.json"
    record.write_bytes(b"interrupted owner reconciliation evidence")
    before = record.read_bytes()
    with pytest.raises(Denied, match="upgrade_reconciliation_required"):
        installer.upgrade(files, policy)
    assert record.read_bytes() == before
    commands.assert_not_called()


def test_concurrent_transaction_record_is_never_removed_by_losing_installer(rehearsal, monkeypatch):
    files, policy, _guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    transaction = paths["BASE"] / "upgrade-transaction.json"
    foreign = b'{"owner":"concurrent-reviewed-upgrade"}\n'

    def create(path, _data, _mode):
        assert path == transaction
        path.write_bytes(foreign)
        raise FileExistsError("another upgrade reserved the transaction")

    monkeypatch.setattr(installer, "exclusive", create)
    with pytest.raises(FileExistsError):
        installer.upgrade(files, policy)
    assert transaction.read_bytes() == foreign
    commands.assert_not_called()


def test_worker_environment_is_reviewed_payload_not_root_interpreter_or_auto_provision(rehearsal):
    files, _policy, _guardian, _commands, _original, paths = rehearsal
    assert paths["LIB"] / "tools/supervised_development_environment.py" in files
    assert paths["LIB"] / "tools/supervised_development_environment.lock.json" in files
    for path, (contents, _mode) in files.items():
        if path.suffix == ".service":
            assert b"ExecStart=/usr/bin/python3 -I " in contents
            assert b"ExecStart=/opt/" not in contents
    assert len(installer.current_payload_paths()) == 33
    assert len(files) == 35


def test_upgrade_rejects_missing_real_legacy_path(rehearsal, monkeypatch):
    files, policy, _controller, _commands, paths = _make_legacy_install(rehearsal, monkeypatch)
    missing = paths["UNITS"] / "jarvis-autonomy-broker-development-admission.socket"
    missing.unlink()
    with pytest.raises(Denied, match="upgrade_source_payload_missing"):
        installer.upgrade(files, policy)


def test_upgrade_rejects_extra_real_legacy_path(rehearsal, monkeypatch):
    files, policy, _controller, _commands, paths = _make_legacy_install(rehearsal, monkeypatch)
    marker = paths["BASE"] / "installed.json"
    value = json.loads(marker.read_bytes())
    extra = paths["BASE"] / "unexpected-legacy-payload"
    extra.write_bytes(b"unexpected")
    value["files"][str(extra)] = hashlib.sha256(extra.read_bytes()).hexdigest()
    marker.write_bytes(canonical(value))
    with pytest.raises(Denied, match="upgrade_source_manifest_invalid"):
        installer.upgrade(files, policy)


def test_upgrade_rejects_modified_real_legacy_path(rehearsal, monkeypatch):
    files, policy, _controller, _commands, paths = _make_legacy_install(rehearsal, monkeypatch)
    target = paths["UNITS"] / "jarvis-autonomy-broker-development-admission.service"
    target.write_bytes(target.read_bytes() + b"\n# modified\n")
    with pytest.raises(Denied, match="upgrade_source_payload_modified"):
        installer.upgrade(files, policy)


def test_upgrade_activation_failure_restores_payload_and_manifest(rehearsal, monkeypatch):
    files, policy, controller, _, paths = _make_legacy_install(rehearsal, monkeypatch)
    marker = paths["BASE"] / "installed.json"
    before_marker = marker.read_bytes()
    before_guardian = (controller.base / "installed.json").read_bytes()
    calls = []

    def fail_activation(argv):
        calls.append(argv)
        if "enable" in argv and "development-admission.socket" in " ".join(argv):
            raise OSError("synthetic activation failure")

    monkeypatch.setattr(installer, "command", fail_activation)
    with pytest.raises(OSError, match="synthetic activation failure"):
        installer.upgrade(files, policy)
    assert marker.read_bytes() == before_marker
    assert (controller.base / "installed.json").read_bytes() == before_guardian
    assert controller.status()["state"] == "STOPPED"
    assert not any(
        path.exists()
        for path in files
        if "development-publication" in str(path)
        or "development-controller" in str(path)
        or path.name
        in {
            "supervised_development_controller.py",
            "supervised_development_pipeline.py",
            "supervised_development_publication.py",
            "supervised_development_review.py",
            "supervised_development_runtime.py",
            "supervised_development_worker.py",
        }
    )
    assert any(path.exists() for path in files if "development-admission" in str(path))
    assert calls


@pytest.fixture
def legacy_private_manifest(rehearsal, monkeypatch):
    from tools.autonomy_broker_store import Records

    files, policy, controller, commands, _, paths = rehearsal
    installer.install(files, policy)
    private = paths["BASE"] / "docker-client/config.json"
    marker = controller.base / "installed.json"
    manifest = json.loads(marker.read_bytes())
    manifest["files"][str(private)] = hashlib.sha256(private.read_bytes()).hexdigest()
    installer.replace_manifest(marker, canonical(manifest))
    monkeypatch.setattr(installer, "Records", lambda path: Records(path, os.getuid(), False))
    commands.reset_mock()
    return files, policy, controller, commands, paths, private


def test_narrow_manifest_repair_preserves_private_integrity_and_stop(legacy_private_manifest):
    files, _, controller, commands, paths, private = legacy_private_manifest
    broker_marker = paths["BASE"] / "installed.json"
    before = broker_marker.read_bytes()
    state = (controller.base / "state.json").read_bytes()
    old_guardian = json.loads((controller.base / "installed.json").read_bytes())
    installer.repair_public_manifest(files)
    repaired = json.loads((controller.base / "installed.json").read_bytes())
    del old_guardian["files"][str(private)]
    assert repaired == old_guardian  # Exactly one entry removed, nothing else.
    assert broker_marker.read_bytes() == before
    assert (controller.base / "state.json").read_bytes() == state
    assert private.read_bytes() == b"{}\n" and private.stat().st_mode & 0o777 == 0o600
    commands.assert_not_called()  # No service restart, reinstall or chmod.
    audit = paths["BASE"] / "records/audit.jsonl"
    assert len(audit.read_bytes().splitlines()) == 2
    original_audit = audit.read_bytes()
    installer.repair_public_manifest(files)
    assert audit.read_bytes() == original_audit


def test_manifest_repair_refuses_active_guardian(legacy_private_manifest):
    files, _, controller, _, _, _ = legacy_private_manifest
    controller.write_state("ACTIVE")
    with pytest.raises(Denied, match="guardian_must_be_stopped"):
        installer.repair_public_manifest(files)


def test_manifest_repair_refuses_changed_private_payload(legacy_private_manifest):
    files, _, _, _, _, private = legacy_private_manifest
    private.write_bytes(b"changed")
    with pytest.raises(Denied, match="modified_protected_payload"):
        installer.repair_public_manifest(files)


def test_manifest_repair_requires_local_operator(rehearsal, monkeypatch):
    files, _, _, _, _, _ = rehearsal
    monkeypatch.setenv("SUDO_USER", "jarvis-autonomy")
    with pytest.raises(Denied, match="local_operator_sudo_required"):
        installer.repair_public_manifest(files)


def test_manifest_repair_rejects_unprivileged_caller(rehearsal, monkeypatch):
    files, _, _, _, _, _ = rehearsal
    monkeypatch.setattr(installer.os, "geteuid", lambda: 995)
    with pytest.raises(Denied, match="local_operator_sudo_required"):
        installer.repair_public_manifest(files)


def test_manifest_repair_restores_original_on_audit_failure(legacy_private_manifest, monkeypatch):
    files, _, controller, _, paths, _ = legacy_private_manifest
    marker = controller.base / "installed.json"
    original = marker.read_bytes()
    records = installer.Records(paths["BASE"] / "records")
    audit = records.audit

    def fail_completed(kind, key, digest):
        if kind == "public_manifest_repair_completed":
            raise Denied("synthetic_audit_failure")
        audit(kind, key, digest)

    monkeypatch.setattr(records, "audit", fail_completed)
    monkeypatch.setattr(installer, "Records", lambda path: records)
    with pytest.raises(Denied, match="synthetic_audit_failure"):
        installer.repair_public_manifest(files)
    assert marker.read_bytes() == original
    assert controller.state()["state"] == "STOPPED"


def test_repeat_keeps_pinned_policy_without_reprovisioning(rehearsal, monkeypatch):
    files, policy, _, _, _, _ = rehearsal
    installer.install(files, policy)
    monkeypatch.setattr(installer, "host_policy", lambda: pytest.fail("must not reprovision"))
    assert installer.reviewed_policy(files) == policy


def test_corrupt_guardian_audit_refuses_upgrade(rehearsal):
    files, policy, controller, commands, _, paths = rehearsal
    (controller.base / "audit/events.jsonl").write_bytes(b"corrupt")
    with pytest.raises(Denied, match="audit_reconciliation"):
        installer.install(files, policy)
    commands.assert_not_called()
    assert not paths["LIB"].exists()
    assert controller.status()["state"] == "STOPPED"


def test_activation_failure_stops_only_broker_services(rehearsal, monkeypatch):
    files, policy, _, _, _, _ = rehearsal
    calls = []

    def fail(argv):
        calls.append(argv)
        if "enable" in argv:
            raise OSError("activation failure")

    monkeypatch.setattr(installer, "command", fail)
    with pytest.raises(OSError):
        installer.install(files, policy)
    assert ["/usr/bin/systemctl", "stop", *installer.SERVICES] in calls
    assert all(
        "jarvis-developer.service" not in call and "docker.service" not in call for call in calls
    )
