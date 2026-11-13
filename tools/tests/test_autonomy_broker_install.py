import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import autonomy_broker_install as installer
from tools import autonomy_guardian as g
from tools.autonomy_broker import Denied, canonical

INSTALLED_SOURCE = "23a24a70cff9971778f966a0e45137c7fe60e2ca"
ADDED_MODULES = {
    "supervised_development_product.py",
    "supervised_development_integration.py",
    "supervised_development_integration_service.py",
    "jarvis-development-integration.service",
    "jarvis-development-integration.socket",
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
            "supervised_development_product.py",
            "supervised_development_integration.py",
            "supervised_development_integration_service.py",
            "supervised_development_publication.py",
            "supervised_development_review.py",
            "supervised_development_runtime.py",
            "supervised_development_scope.py",
            "supervised_development_worker.py",
            "jarvis-development-integration.service",
            "jarvis-development-integration.socket",
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
        "8902197c9a7f1c44c6251fe765c6cafcec31952721009158984fd593bcea87f3"
    )
    assert installer.UPGRADE_BUNDLE == installer.fingerprint(installer.payload())
    baseline_files = _reviewed_source_payload(installer.payload())
    assert installer.fingerprint(baseline_files) == installer.CURRENT_BUNDLE


def test_actual_installed_payload_identity_is_reconstructed_completely():
    baseline = _reviewed_source_payload(installer.payload())
    assert len(baseline) == 35  # Plus retained private configuration and policy = 37.
    assert installer.fingerprint(baseline) == installer.CURRENT_BUNDLE


def test_installed_edit_scope_is_dependency_free_not_mutable_core_import(tmp_path):
    files = installer.payload()
    target = installer.LIB / "tools/supervised_development_scope.py"
    assert files[target][0] == (installer.ROOT / "bridge/app/development_scope.py").read_bytes()
    assert b"from bridge" not in files[target][0]
    # Root publication reuses proposal verification, but GitHub execution is a
    # separately provisioned, disabled, credential-isolated non-worker service.
    integration = files[installer.UNITS / "jarvis-development-integration.service"][0]
    assert b"User=jarvis-integration\n" in integration
    assert b"CapabilityBoundingSet=\n" in integration
    assert b"InaccessiblePaths=-/var/lib/jarvis-autonomy-broker" in integration
    assert all(path.name not in {"github-token", "policy.json"} for path in files)
    assert not any("integration" in name for name in installer.SOCKETS)
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


@pytest.mark.parametrize("source", ["legacy", "current"])
@pytest.mark.parametrize("previously_exposed", [False, True])
def test_upgrade_preserves_public_guardian_and_private_broker_integrity(
    rehearsal, monkeypatch, source, previously_exposed
):
    create = _make_legacy_install if source == "legacy" else _current_workspace_fixture
    files, policy, guardian, _commands, paths = create(rehearsal, monkeypatch)
    private = paths["BASE"] / "docker-client/config.json"
    marker = paths["BASE"] / "installed.json"
    guardian_marker = guardian.base / "installed.json"
    manifest = json.loads(guardian_marker.read_bytes())
    private_before = private.read_bytes()
    private_info = private.stat()
    private_digest = hashlib.sha256(private_before).hexdigest()
    if previously_exposed:
        manifest["files"][str(private)] = private_digest
        installer.replace_manifest(guardian_marker, canonical(manifest))
    state_before = (guardian.base / "state.json").read_bytes()
    audit_before = (guardian.base / "audit/events.jsonl").read_bytes()

    installer.upgrade(files, policy)

    public = json.loads(guardian_marker.read_bytes())["files"]
    broker = json.loads(marker.read_bytes())["files"]
    assert str(private) not in public
    assert broker[str(private)] == private_digest
    assert public[str(marker)] == hashlib.sha256(marker.read_bytes()).hexdigest()
    assert all(public[name] == digest for name, digest in broker.items() if name != str(private))
    assert private.read_bytes() == private_before
    assert (private.stat().st_ino, private.stat().st_mode) == (
        private_info.st_ino,
        private_info.st_mode,
    )
    assert (guardian.base / "state.json").read_bytes() == state_before
    assert (guardian.base / "audit/events.jsonl").read_bytes() == audit_before
    assert installer.checked_existing(files, policy)[0] == "REPEAT"

    # Reproduce the public reader's inability to enter the private directory.
    secure = guardian.secure

    def public_secure(path, **kwargs):
        if path == private:
            raise PermissionError("private configuration is not public")
        return secure(path, **kwargs)

    monkeypatch.setattr(guardian, "secure", public_secure)
    assert guardian.status()["trusted"] is True
    assert guardian.status()["state"] == "STOPPED"

    # Private tampering remains detectable by root's full broker verification.
    private.write_bytes(b'{"unreviewed":"fixture"}\n')
    broker_before_check = marker.read_bytes()
    guardian_before_check = guardian_marker.read_bytes()
    with pytest.raises(Denied, match="modified_protected_payload"):
        installer.checked_existing(files, policy)
    assert private.read_bytes() == b'{"unreviewed":"fixture"}\n'
    assert marker.read_bytes() == broker_before_check
    assert guardian_marker.read_bytes() == guardian_before_check
    private.write_bytes(private_before)
    # Even a self-consistent rewritten broker marker is pinned by Guardian.
    rewritten = json.loads(marker.read_bytes())
    rewritten["files"][str(private)] = "0" * 64
    marker.write_bytes(canonical(rewritten))
    assert guardian.status()["trusted"] is False
    assert marker.read_bytes() == canonical(rewritten)
    assert guardian_marker.read_bytes() == guardian_before_check
    assert (guardian.base / "state.json").read_bytes() == state_before
    assert (guardian.base / "audit/events.jsonl").read_bytes() == audit_before


def test_public_manifest_filter_only_removes_exact_verified_private_path(rehearsal):
    _files, _policy, _guardian, _commands, _original, paths = rehearsal
    private = str(paths["BASE"] / "docker-client/config.json")
    other = str(paths["BASE"] / "other/config.json")
    records = {private: "a" * 64, other: "b" * 64}
    assert installer._public_guardian_files(records, "a" * 64) == {other: "b" * 64}
    assert records == {private: "a" * 64, other: "b" * 64}
    with pytest.raises(Denied, match="unexpected_private_configuration"):
        installer._public_guardian_files(records, "c" * 64)


@pytest.mark.skipif(
    os.environ.get("JARVIS_INSTALLER_DOCKER_TEST") != "1",
    reason="explicit disposable root/unprivileged Guardian migration rehearsal required",
)
def test_real_root_and_unprivileged_guardian_reads_after_manifest_migration():
    """Actual DAC checks; no host writable mounts, services or protected records."""
    source = Path(__file__).resolve().parents[2]
    prefix = Path(sys.prefix).resolve()
    previous = _reviewed_source_payload(installer.payload())
    old_payload = [
        {"path": str(path), "data": data.hex(), "mode": mode}
        for path, (data, mode) in previous.items()
    ]
    script = r"""
import hashlib, json, os, pathlib, sys
sys.path.insert(0, '/source')
from tools import autonomy_broker_install as install
from tools import autonomy_guardian as guardian_module
from tools.autonomy_broker import canonical
Guardian = guardian_module.Guardian
original = {name: getattr(install, name) for name in ('BASE','LIB','POLICY','JOBS','UNITS','DROPIN')}
old_payload = json.load(sys.stdin)
os.environ.update(SUDO_USER='aaron', SUDO_UID='1000')
results = []

def public_read(guardian, private):
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            os.setgroups([])
            os.setgid(995)
            os.setuid(995)
            try:
                private.read_bytes()
                private_denied = False
            except PermissionError:
                private_denied = True
            output = {'uid':os.getuid(), 'private_denied':private_denied,
                      'status':guardian.status()}
            os.write(write_fd, json.dumps(output).encode())
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write_fd)
    output = os.read(read_fd, 4096)
    os.close(read_fd)
    assert os.waitpid(child, 0)[1] == 0
    result = json.loads(output)
    assert result['uid'] == 995 and result['private_denied']
    return result['status']

for exposed in (False, True):
    root = pathlib.Path('/fixture') / str(int(exposed))
    root.mkdir(mode=0o755)
    for name, relative in {'BASE':'broker','LIB':'lib','POLICY':'broker.json',
                          'JOBS':'jobs','UNITS':'units','DROPIN':'units/dropin/broker.conf'}.items():
        setattr(install, name, root / relative)
    install.UNITS.mkdir(mode=0o755)
    base = root / 'guardian'
    base.mkdir(mode=0o755)
    (base / 'audit').mkdir(mode=0o700)
    for name in ('control.lock','audit/events.jsonl'):
        (base / name).touch(mode=0o600)
    guardian_policy = root / 'guardian.json'
    guardian_policy.write_bytes(canonical({'version':1,'operator_uid':1000,
        'autonomy_account':'jarvis-autonomy','privileged_operations':[]}))
    guardian_policy.chmod(0o644)
    guardian_marker = base / 'installed.json'
    guardian_marker.write_bytes(canonical({'version':1,'bundle':'fixture-guardian',
        'files':{str(guardian_policy):hashlib.sha256(guardian_policy.read_bytes()).hexdigest()}}))
    guardian_marker.chmod(0o644)
    guardian = Guardian(base, guardian_policy)
    guardian.write_state('STOPPED')
    stop = guardian.stop
    guardian.stop = lambda actor, stop=stop: stop(actor, lambda: None)
    install.g.Guardian = lambda: guardian
    install.g.identity = lambda: 995
    install.command = lambda args: None
    install._upgrade_service_preconditions = lambda *args: None
    policy = {'operator_uid':1000,'autonomy_uid':995,'autonomy_gid':995}
    baseline = {}
    for item in old_payload:
        path = pathlib.Path(item['path'])
        if path == original['DROPIN']:
            target = install.DROPIN
        elif path.is_relative_to(original['LIB']):
            target = install.LIB / path.relative_to(original['LIB'])
        elif path.is_relative_to(original['UNITS']):
            target = install.UNITS / path.relative_to(original['UNITS'])
        else:
            raise AssertionError('unexpected source payload')
        baseline[target] = (bytes.fromhex(item['data']), item['mode'])
    install.install(baseline, policy)
    marker = install.BASE / 'installed.json'
    prior = json.loads(marker.read_bytes())
    prior['bundle'] = install.CURRENT_BUNDLE
    marker.write_bytes(canonical(prior))
    manifest = json.loads(guardian_marker.read_bytes())
    manifest['files'][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
    private = install.BASE / 'docker-client/config.json'
    private_before = private.read_bytes()
    if exposed:
        manifest['files'][str(private)] = hashlib.sha256(private_before).hexdigest()
    install.replace_manifest(guardian_marker, canonical(manifest))
    state_before = (base / 'state.json').read_bytes()
    assert guardian.status()['trusted']
    assert public_read(guardian, private)['trusted'] is (not exposed)
    files = install.payload()
    install.UPGRADE_BUNDLE = install.fingerprint(files)
    install.upgrade(files, policy)
    assert install.checked_existing(files, policy)[0] == 'REPEAT'
    assert guardian.status()['trusted']
    status = public_read(guardian, private)
    assert status['trusted'] and status['state'] == 'STOPPED'
    assert private.read_bytes() == private_before
    assert private.stat().st_uid == 0 and private.stat().st_mode & 0o777 == 0o600
    assert (base / 'state.json').read_bytes() == state_before
    manifest = json.loads(guardian_marker.read_bytes())
    assert str(private) not in manifest['files']
    assert manifest['files'][str(marker)] == hashlib.sha256(marker.read_bytes()).hexdigest()
    results.append({'previously_exposed':exposed,'root_trusted':True,'uid995_trusted':True,
                    'private_read_denied':True,'state_unchanged':True})
print(json.dumps({'verified':results}))
"""
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--read-only",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--cap-add",
        "CHOWN",
        "--security-opt",
        "no-new-privileges=true",
        "--pids-limit",
        "32",
        "--memory",
        "256m",
        "--tmpfs",
        "/fixture:rw,nosuid,nodev,mode=755,size=64m",
        "--mount",
        f"type=bind,src={source},dst=/source,readonly",
        "--mount",
        "type=bind,src=/usr,dst=/usr,readonly",
        "--mount",
        f"type=bind,src={prefix},dst={prefix},readonly",
        "-i",
        "python:3.12-slim",
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
    ]
    result = subprocess.run(
        command, input=json.dumps(old_payload), capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr[-5000:]
    evidence = json.loads(result.stdout.splitlines()[-1])["verified"]
    assert len(evidence) == 2
    assert all(
        item["root_trusted"]
        and item["uid995_trusted"]
        and item["private_read_denied"]
        and item["state_unchanged"]
        for item in evidence
    )


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
def test_installed_pr137_payload_to_native_product_preserves_history(
    rehearsal, monkeypatch, failure
):
    files, policy, guardian, commands, _original, paths = rehearsal
    old_files = _reviewed_source_payload(files)
    target = next(p for p in files if p.name == "supervised_development_runtime.py")
    old_data = old_files[target][0]
    installer.install(old_files, policy)
    marker = paths["BASE"] / "installed.json"
    previous = json.loads(marker.read_bytes())
    assert len(previous["files"]) == 37
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


@pytest.mark.parametrize("failure", ["manifest", "interrupted", "activation"])
@pytest.mark.parametrize("previously_exposed", [False, True])
def test_interrupted_or_manifest_failed_upgrade_restores_exact_protected_bundle(
    rehearsal, monkeypatch, failure, previously_exposed
):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    private = paths["BASE"] / "docker-client/config.json"
    private_before = private.read_bytes()
    if previously_exposed:
        guardian_marker = guardian.base / "installed.json"
        manifest = json.loads(guardian_marker.read_bytes())
        manifest["files"][str(private)] = hashlib.sha256(private_before).hexdigest()
        installer.replace_manifest(guardian_marker, canonical(manifest))
    state_before = (guardian.base / "state.json").read_bytes()
    audit_before = (guardian.base / "audit/events.jsonl").read_bytes()
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
        if failure == "activation" and not failed and "enable" in argv:
            failed = True
            raise OSError("fixture activation failed")

    monkeypatch.setattr(installer, "replace_manifest", fail_manifest)
    commands.side_effect = fail_command
    with pytest.raises(KeyboardInterrupt if failure == "interrupted" else OSError):
        installer.upgrade(files, policy)
    assert failed
    assert all(path.read_bytes() == data for path, data in old_files.items())
    assert all(not p.exists() for p in files if p.name in ADDED_MODULES)
    assert (paths["BASE"] / "installed.json").read_bytes() == old_marker
    assert (guardian.base / "installed.json").read_bytes() == old_guardian
    assert private.read_bytes() == private_before
    assert private.stat().st_mode & 0o777 == 0o600
    assert (guardian.base / "state.json").read_bytes() == state_before
    assert (guardian.base / "audit/events.jsonl").read_bytes() == audit_before
    assert not (paths["BASE"] / "upgrade-transaction.json").exists()
    assert guardian.status()["state"] == "STOPPED"


def test_unreviewed_existing_destination_is_not_overwritten(rehearsal, monkeypatch):
    files, policy, guardian, commands, paths = _current_workspace_fixture(rehearsal, monkeypatch)
    target = paths["LIB"] / "tools/supervised_development_product.py"
    assert not target.exists()
    target.write_bytes(b"unreviewed existing source; must be preserved")
    target.chmod(0o644)
    before = target.read_bytes()
    commands.reset_mock()
    with pytest.raises(Denied, match="upgrade_destination_unexpected"):
        installer.upgrade(files, policy)
    assert target.read_bytes() == before
    assert guardian.status()["state"] == "STOPPED"
    commands.assert_not_called()


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
    assert len(installer.current_payload_paths()) == 35
    assert len(files) == 40  # Existing sealed worker plus three adapters/two disabled PR units.


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


@pytest.mark.parametrize("stop_failure", [None, "disable", "stop"])
def test_legacy_partial_publication_activation_is_quiesced_before_payload_rollback(
    rehearsal, monkeypatch, stop_failure
):
    files, policy, guardian, commands, paths = _make_legacy_install(rehearsal, monkeypatch)
    marker = paths["BASE"] / "installed.json"
    guardian_marker = guardian.base / "installed.json"
    before_marker = marker.read_bytes()
    before_guardian = guardian_marker.read_bytes()
    before_files = {path: path.read_bytes() for path in files if path.exists()}
    state_before = (guardian.base / "state.json").read_bytes()
    commands.reset_mock()
    attempted = False
    publication_running = False
    restorations = []
    replace = installer.os.replace

    def command(argv):
        nonlocal attempted, publication_running
        if "enable" in argv and not attempted:
            assert argv == ["/usr/bin/systemctl", "enable", "--now", *installer.SOCKETS]
            attempted = True
            publication_running = True
            raise OSError("partial destination publication activation")
        if attempted and "disable" in argv:
            assert argv == ["/usr/bin/systemctl", "disable", "--now", *installer.SOCKETS]
            if stop_failure == "disable":
                raise OSError("publication socket shutdown uncertain")
        if attempted and "stop" in argv:
            assert argv == ["/usr/bin/systemctl", "stop", *installer.SERVICES]
            if stop_failure == "stop":
                raise OSError("publication service shutdown uncertain")
            publication_running = False

    def replace_only_when_quiescent(source, destination):
        if attempted:
            assert not publication_running, "must not restore under live destination code"
            restorations.append(Path(destination))
        return replace(source, destination)

    commands.side_effect = command
    monkeypatch.setattr(installer.os, "replace", replace_only_when_quiescent)
    if stop_failure:
        with pytest.raises(Denied, match="upgrade_service_reconciliation_required"):
            installer.upgrade(files, policy)
        assert publication_running and not restorations
        assert json.loads(marker.read_bytes())["bundle"] == installer.UPGRADE_BUNDLE
        assert all(path.read_bytes() == data for path, (data, _mode) in files.items())
        transaction = paths["BASE"] / "upgrade-transaction.json"
        retained = json.loads(transaction.read_bytes())
        assert retained["source_bundle"] == installer.LEGACY_BUNDLE
        assert all(Path(path).exists() for path in retained["backups"].values())
        assert Path(retained["backups"][str(marker)]).read_bytes() == before_marker
        assert Path(retained["backups"][str(guardian_marker)]).read_bytes() == before_guardian
        before_retry_calls = list(commands.call_args_list)
        with pytest.raises(Denied, match="upgrade_reconciliation_required"):
            installer.upgrade(files, policy)
        assert commands.call_args_list == before_retry_calls
    else:
        with pytest.raises(OSError, match="partial destination publication activation"):
            installer.upgrade(files, policy)
        assert attempted and not publication_running and restorations
        assert marker.read_bytes() == before_marker
        assert guardian_marker.read_bytes() == before_guardian
        assert all(path.read_bytes() == data for path, data in before_files.items())
        assert all(not path.exists() for path in files if path not in before_files)
        assert not (paths["BASE"] / "upgrade-transaction.json").exists()
        assert commands.call_args_list[-1].args[0] == [
            "/usr/bin/systemctl",
            "enable",
            "--now",
            *installer.SOCKETS[:3],
        ]
    assert (guardian.base / "state.json").read_bytes() == state_before
    assert guardian.status()["state"] == "STOPPED"
    assert guardian.status()["trusted"] is True


@pytest.mark.parametrize("source", ["legacy", "current"])
def test_failure_before_destination_activation_only_stops_source_units(
    rehearsal, monkeypatch, source
):
    create = _make_legacy_install if source == "legacy" else _current_workspace_fixture
    files, policy, _guardian, commands, _paths = create(rehearsal, monkeypatch)
    commands.reset_mock()

    def fail_validation(argv):
        if argv[0] == "/usr/bin/systemd-analyze":
            raise OSError("destination unit validation failed")

    commands.side_effect = fail_validation
    with pytest.raises(OSError, match="destination unit validation failed"):
        installer.upgrade(files, policy)
    source_sockets = installer.SOCKETS[:3] if source == "legacy" else installer.SOCKETS
    source_services = installer.SERVICES[:3] if source == "legacy" else installer.SERVICES
    assert [call.args[0] for call in commands.call_args_list if "disable" in call.args[0]] == [
        ["/usr/bin/systemctl", "disable", "--now", *source_sockets]
    ] * 2
    assert [call.args[0] for call in commands.call_args_list if "stop" in call.args[0]] == [
        ["/usr/bin/systemctl", "stop", *source_services]
    ] * 2
    assert commands.call_args_list[-1].args[0] == [
        "/usr/bin/systemctl",
        "enable",
        "--now",
        *source_sockets,
    ]


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
