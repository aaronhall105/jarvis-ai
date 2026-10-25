import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import autonomy_broker_install as installer
from tools import autonomy_guardian as g
from tools.autonomy_broker import Denied, canonical


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
