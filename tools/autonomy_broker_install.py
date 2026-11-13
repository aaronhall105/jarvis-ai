"""Reviewed Guardian-only -> broker upgrade. --check never modifies the host."""

from __future__ import annotations

import hashlib
import errno
import json
import os
import pwd
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import autonomy_guardian as g  # noqa: E402
from tools.autonomy_broker import Denied, canonical  # noqa: E402
from tools.autonomy_broker_data import compatible_code, persistence_surface  # noqa: E402
from tools.autonomy_broker_host import CANONICAL, Docker, GitHub, SAFE_ENV, validate_core_container  # noqa: E402
from tools.autonomy_broker_release import LIVE_ROOT  # noqa: E402
from tools.autonomy_broker_runtime import BASE, JOBS, LIB, POLICY  # noqa: E402
from tools.autonomy_broker_store import Records  # noqa: E402

UNITS = Path("/etc/systemd/system")
MODULES = (
    "autonomy_broker",
    "autonomy_broker_data",
    "autonomy_broker_evidence",
    "autonomy_broker_host",
    "autonomy_broker_ipc",
    "autonomy_broker_operator",
    "autonomy_broker_release",
    "autonomy_broker_runtime",
    "autonomy_broker_snapshot",
    "autonomy_broker_store",
    "autonomy_guardian",
    "supervised_development_broker",
    "supervised_development_controller",
    "supervised_development_edits",
    "supervised_development_environment",
    "supervised_development_pipeline",
    "supervised_development_product",
    "supervised_development_integration",
    "supervised_development_integration_service",
    "supervised_development_publication",
    "supervised_development_review",
    "supervised_development_runtime",
    "supervised_development_scope",
    "supervised_development_worker",
)
UNIT_NAMES = tuple(
    "jarvis-autonomy-broker-" + role + suffix
    for role in ("worker", "operator", "development-admission", "development-publication")
    for suffix in (".service", ".socket")
)
CONTROLLER_UNIT = "jarvis-autonomy-broker-development-controller.service"
UNIT_NAMES = UNIT_NAMES + (CONTROLLER_UNIT,)
# Installed disabled. A separately provisioned non-worker identity and owner
# integration policy/credential are required before this socket can be enabled.
INTEGRATION_UNITS = (
    "jarvis-development-integration.service",
    "jarvis-development-integration.socket",
)
UNIT_NAMES += INTEGRATION_UNITS
DROPIN = UNITS / "jarvis-autonomy.service.d/broker.conf"
SOCKETS = (
    "jarvis-autonomy-broker-operator.socket",
    "jarvis-autonomy-broker-worker.socket",
    "jarvis-autonomy-broker-development-admission.socket",
    "jarvis-autonomy-broker-development-publication.socket",
)
SERVICES = (
    "jarvis-autonomy-broker-operator.service",
    "jarvis-autonomy-broker-worker.service",
    "jarvis-autonomy-broker-development-admission.service",
    "jarvis-autonomy-broker-development-publication.service",
    CONTROLLER_UNIT,
)

# The only legacy installation accepted by the narrowly-scoped upgrade path.
# These values are reviewed bundle identities, not user-selectable versions.
LEGACY_BUNDLE = "b7c72340c0d19b320928fc50fc07513389374f22584dfd3801d22e655cdde3eb"
# Reviewed installed PR #136/#137 bundle, verified against canonical Git objects.
# Retain the exact-version migration and all existing integrity/rollback checks.
CURRENT_BUNDLE = "8902197c9a7f1c44c6251fe765c6cafcec31952721009158984fd593bcea87f3"
# Complete reviewed destination payload, including phone product boundaries.
UPGRADE_BUNDLE = "973dd051e3cc9aab4ac4ec9293b397e33a5180abaf535578454299d51299af41"

# Exact installed source inventory, independent of future destination additions.
CURRENT_MODULES = (
    "autonomy_broker",
    "autonomy_broker_data",
    "autonomy_broker_evidence",
    "autonomy_broker_host",
    "autonomy_broker_ipc",
    "autonomy_broker_operator",
    "autonomy_broker_release",
    "autonomy_broker_runtime",
    "autonomy_broker_snapshot",
    "autonomy_broker_store",
    "autonomy_guardian",
    "supervised_development_broker",
    "supervised_development_controller",
    "supervised_development_edits",
    "supervised_development_environment",
    "supervised_development_pipeline",
    "supervised_development_publication",
    "supervised_development_review",
    "supervised_development_runtime",
    "supervised_development_scope",
    "supervised_development_worker",
)
CURRENT_UNIT_NAMES = tuple(
    "jarvis-autonomy-broker-" + role + suffix
    for role in ("worker", "operator", "development-admission", "development-publication")
    for suffix in (".service", ".socket")
) + ("jarvis-autonomy-broker-development-controller.service",)


def current_payload_paths() -> dict[Path, Path]:
    """Map exact installed payload paths to their fixed fingerprint identities."""
    fixed_lib = Path("/usr/local/libexec/jarvis-autonomy-broker")
    fixed_units = Path("/etc/systemd/system")
    result = {
        LIB / "tools" / (name + ".py"): fixed_lib / "tools" / (name + ".py")
        for name in CURRENT_MODULES
    }
    result.update(
        {
            LIB / name: fixed_lib / name
            for name in ("tools/__init__.py", "entry.py", "deploy_unified_core.sh")
        }
    )
    lock_name = "tools/supervised_development_environment.lock.json"
    result[LIB / lock_name] = fixed_lib / lock_name
    result.update({UNITS / name: fixed_units / name for name in CURRENT_UNIT_NAMES})
    result[DROPIN] = fixed_units / "jarvis-autonomy.service.d/broker.conf"
    return result


def payload() -> dict[Path, tuple[bytes, int]]:
    files = {
        LIB / "tools" / (name + ".py"): ((ROOT / "tools" / (name + ".py")).read_bytes(), 0o644)
        for name in MODULES
    }
    # The installed policy is dependency-free reviewed code, not the repository
    # wrapper which imports mutable Core source. The separate GitHub integration
    # identity/credential is deliberately NOT part of the root broker bundle.
    files[LIB / "tools/supervised_development_scope.py"] = (
        (ROOT / "bridge/app/development_scope.py").read_bytes(),
        0o644,
    )
    files[LIB / "tools/supervised_development_environment.lock.json"] = (
        (ROOT / "tools/supervised_development_environment.lock.json").read_bytes(),
        0o644,
    )
    files[LIB / "tools/__init__.py"] = (b"# Protected broker package.\n", 0o644)
    files[LIB / "entry.py"] = ((ROOT / "tools/autonomy_broker_entry.py").read_bytes(), 0o644)
    files[LIB / "deploy_unified_core.sh"] = (
        (ROOT / "tools/deploy_unified_core.sh").read_bytes(),
        0o644,
    )
    for name in UNIT_NAMES:
        files[UNITS / name] = ((ROOT / "systemd/autonomy" / name).read_bytes(), 0o644)
    files[DROPIN] = ((ROOT / "systemd/autonomy/broker-worker-access.conf").read_bytes(), 0o644)
    return files


def fingerprint(files: dict) -> str:
    return hashlib.sha256(
        canonical(
            {
                str(path): {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode}
                for path, (data, mode) in sorted(files.items())
            }
        )
    ).hexdigest()


def command(argv: list[str]) -> None:
    subprocess.run(
        argv,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=True,
    )


def clean_git() -> dict[str, str]:
    common = Path("/home/aaron/jarvis/.git")
    pointer = CANONICAL / ".git"
    if pointer.is_symlink() or not pointer.is_file():
        raise Denied("unexpected_canonical_git_layout")
    text = pointer.read_text().strip()
    expected = common / "worktrees" / CANONICAL.name
    if text != "gitdir: " + str(expected):
        raise Denied("unexpected_canonical_git_layout")
    config = common / "config"
    content = config.read_text().lower()
    if any(
        token in content
        for token in ("[include", "[filter", "[credential", "fsmonitor", "hookspath", "sshcommand")
    ):
        raise Denied("unsupported_git_configuration")
    if (common / "config.worktree").exists() or (expected / "config.worktree").exists():
        raise Denied("unsupported_git_configuration")
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (pointer, config)}


def host_policy() -> dict:
    status = g.Guardian().status()
    if status != {"state": "STOPPED", "generation": status.get("generation"), "trusted": True}:
        raise Denied("guardian_must_be_trusted_stopped")
    uid = g.identity()
    account, operator = pwd.getpwnam(g.ACCOUNT), pwd.getpwnam("aaron")
    for executable in (
        "/usr/bin/python3",
        "/usr/bin/git",
        "/usr/bin/docker",
        "/usr/bin/systemctl",
        "/usr/bin/systemd-analyze",
        "/bin/bash",
    ):
        target = Path(executable).resolve(strict=True)
        g.trusted(target)
        g.trusted_parents(target)
        if not os.access(target, os.X_OK):
            raise Denied("required_host_executable_unavailable")
    command(["/usr/bin/docker", "compose", "version", "--short"])
    command(["/usr/bin/python3", "-I", "-c", "import cryptography; import sqlite3"])
    docker = Docker()
    old = docker.inspect("jarvis-core")  # Never print/copy its environment.
    github = GitHub()
    current = github.get("git/ref/heads/jarvis/unified-production")["object"]["sha"]
    previous = old["Config"]["Labels"]["org.opencontainers.image.revision"]
    tree = github.tree(current)
    persistence = persistence_surface(tree)
    compatible_code(github.tree(previous), tree, persistence)
    tool = docker.call("GET", f"/images/jarvis-developer-tools:{current}/json")
    base = docker.call("GET", f"/images/{old['Image']}/json")
    if base["Config"].get("OnBuild") or tool["Config"].get("OnBuild"):
        raise Denied("unsupported_image_trigger")
    for image in (base, tool):
        keys = {entry.partition("=")[0].upper() for entry in image["Config"].get("Env", [])}
        if any(
            any(fragment in key for fragment in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
            for key in keys
        ):
            raise Denied("credential_bearing_image")
    for mount_name in ("config", "data", "logs"):
        path = LIVE_ROOT / mount_name
        if path.is_symlink() or not path.is_dir():
            raise Denied("unexpected_runtime_mount")
    policy = {
        "version": 1,
        "autonomy_uid": uid,
        "autonomy_gid": account.pw_gid,
        "operator_uid": operator.pw_uid,
        "operator_public_keys": {},
        "reviewer_ids": [],
        "github_actions_app_id": 15368,
        "core_base_image": base["Id"],
        "tool_image": tool["Id"],
        "core_network": "jarvis_default",
        "core_ports": old["HostConfig"]["PortBindings"],
        "core_mounts": {
            "/app/" + name: str(LIVE_ROOT / name) for name in ("config", "data", "logs")
        },
        "runtime_hashes": {
            str(LIVE_ROOT / name): hashlib.sha256((LIVE_ROOT / name).read_bytes()).hexdigest()
            for name in ("docker-compose.yml", "docker-compose.override.yml", ".env")
        },
        "git_metadata_hashes": clean_git(),
        "persistence_blobs": persistence,
        "dependency_hashes": {
            name: hashlib.sha256((CANONICAL / name).read_bytes()).hexdigest()
            for name in ("bridge/requirements.txt", "bridge/Dockerfile")
        },
        "protected_prefixes": [".github", "tools/verify_product_baseline.py"],
        "protected_blobs": {
            name: digest
            for name, digest in tree.items()
            if name.startswith(".github/") or name == "tools/verify_product_baseline.py"
        },
    }
    validate_core_container(old, policy)
    from tools.autonomy_broker_runtime import canonical_clean

    canonical_clean(policy, current)
    return policy


def reviewed_policy(files: dict) -> dict:
    # Repeating an install verifies the original pinned policy, never silently
    # refreshes it from a newer runtime/image or changes operator enrollment.
    if not (BASE / "installed.json").exists():
        return host_policy()
    checked_existing(files, {})
    g.Guardian().integrity()
    if g.Guardian().status().get("state") != "STOPPED":
        raise Denied("guardian_must_be_trusted_stopped")
    policy = json.loads(POLICY.read_text())
    if policy["autonomy_uid"] != g.identity():
        raise Denied("broker_identity_changed")
    return policy


def checked_existing(files: dict, policy: dict) -> tuple[str, dict]:
    marker = BASE / "installed.json"
    if marker.exists():
        g.trusted(marker)
        previous = json.loads(marker.read_text())
        if previous["bundle"] != fingerprint(files):
            raise Denied("different_reviewed_broker_bundle")
        for name, digest in previous["files"].items():
            path = Path(name)
            g.trusted(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise Denied("modified_protected_payload")
        return "REPEAT", previous
    for path in [LIB, BASE, JOBS, POLICY, DROPIN, *files]:
        if path.exists() or path.is_symlink():
            raise Denied("unknown_or_partial_broker_installation")
        for parent in path.parents:
            if parent.exists():
                g.trusted(parent, directory=True)
    return "UPGRADE_GUARDIAN_ONLY", {}


def _verified_legacy_install(files: dict) -> dict:
    """Verify the one known installed broker before an executable upgrade."""
    marker = BASE / "installed.json"
    if marker.is_symlink() or not marker.is_file():
        raise Denied("upgrade_source_marker_missing")
    g.trusted(marker)
    try:
        previous = json.loads(marker.read_text())
    except (OSError, ValueError) as error:
        raise Denied("upgrade_source_marker_invalid") from error
    if previous.get("version") != 1 or previous.get("bundle") != LEGACY_BUNDLE:
        raise Denied("upgrade_source_bundle_not_reviewed")
    recorded = previous.get("files")
    if not isinstance(recorded, dict) or not recorded:
        raise Denied("upgrade_source_manifest_invalid")
    private = BASE / "docker-client/config.json"
    legacy_paths = {
        str(POLICY),
        str(private),
        str(DROPIN),
        str(LIB / "entry.py"),
        str(LIB / "deploy_unified_core.sh"),
        str(LIB / "tools/__init__.py"),
        # The installed legacy bundle already contains the original
        # development-admission broker and its socket-activated service.  The
        # upgrade adds the controller/publication runtime; these three paths
        # are therefore part of the exact accepted 24-file source layout.
        str(LIB / "tools" / "supervised_development_broker.py"),
        *(
            str(LIB / "tools" / (name + ".py"))
            for name in CURRENT_MODULES
            if name
            not in {
                "supervised_development_broker",
                "supervised_development_controller",
                "supervised_development_edits",
                "supervised_development_environment",
                "supervised_development_pipeline",
                "supervised_development_publication",
                "supervised_development_review",
                "supervised_development_runtime",
                "supervised_development_scope",
                "supervised_development_worker",
            }
        ),
        *(
            str(UNITS / name)
            for name in CURRENT_UNIT_NAMES
            if all(
                token not in name
                for token in (
                    "development-admission",
                    "development-publication",
                    "development-controller",
                )
            )
        ),
        str(UNITS / "jarvis-autonomy-broker-development-admission.service"),
        str(UNITS / "jarvis-autonomy-broker-development-admission.socket"),
    }
    if set(recorded) != legacy_paths:
        raise Denied("upgrade_source_manifest_invalid")
    for name, digest in recorded.items():
        path = Path(name)
        if path.is_symlink() or not path.is_file():
            raise Denied("upgrade_source_payload_missing")
        g.trusted(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise Denied("upgrade_source_payload_modified")
    return previous


def _verified_current_install(files: dict) -> dict:
    """Verify the exact previously upgraded bundle before a second migration."""
    marker = BASE / "installed.json"
    if marker.is_symlink() or not marker.is_file():
        raise Denied("upgrade_source_manifest_missing")
    g.trusted(marker)
    previous = json.loads(marker.read_text())
    if previous.get("version") != 1 or previous.get("bundle") != CURRENT_BUNDLE:
        raise Denied("upgrade_source_bundle_not_reviewed")
    recorded = previous.get("files")
    source_paths = current_payload_paths()
    expected_paths = {str(path) for path in source_paths} | {
        str(POLICY),
        str(BASE / "docker-client/config.json"),
    }
    if not isinstance(recorded, dict) or set(recorded) != expected_paths:
        raise Denied("upgrade_source_manifest_invalid")
    verified_payload = {}
    for name, digest in recorded.items():
        path = Path(name)
        if path.is_symlink() or not path.is_file():
            raise Denied("upgrade_source_payload_missing")
        g.trusted(path)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise Denied("upgrade_source_payload_modified")
        if path in source_paths:
            verified_payload[source_paths[path]] = (data, stat.S_IMODE(path.stat().st_mode))
    # A self-consistent modified marker is not the reviewed source bundle.
    if fingerprint(verified_payload) != CURRENT_BUNDLE:
        raise Denied("upgrade_source_bundle_not_reviewed")
    return previous


def _upgrade_service_preconditions(sockets: tuple[str, ...], services: tuple[str, ...]) -> None:
    """Read-only checks make the documented rollback activation state explicit."""
    for unit in (*sockets, *([CONTROLLER_UNIT] if CONTROLLER_UNIT in services else [])):
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "--property=LoadState,UnitFileState,ActiveState",
                unit,
            ],
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if state.get("LoadState") != "loaded":
            raise Denied("upgrade_source_service_state_unverified")
        if unit in sockets:
            if state.get("UnitFileState") != "enabled" or state.get("ActiveState") != "active":
                raise Denied("upgrade_source_socket_not_enabled_active")
        elif state.get("ActiveState") not in {"inactive", "failed"}:
            raise Denied("upgrade_controller_must_be_inactive")


def _stage(path: Path, data: bytes, mode: int, directory: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=".upgrade-stage-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        return Path(name)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _workspace_identity(path: Path, original: os.stat_result) -> None:
    current = path.lstat()
    if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
        raise Denied("upgrade_workspace_reconciliation_required")


def _prepare_development_workspace(
    policy: dict, metadata: dict[Path, tuple[int, os.stat_result]]
) -> None:
    """Create only the worker's bounded writable directories.

    The root coordinator and autonomy account share the controller database;
    executable broker state, credentials, ledgers and audit directories remain
    root-owned and are never delegated to the worker.
    """
    work = BASE / "work"
    for path, directory, mode in (
        (work, True, 0o770),
        (BASE / "worktrees", True, 0o770),
        (work / "supervised-development.sqlite3", False, 0o660),
    ):
        if directory:
            path.mkdir(mode=mode, parents=True, exist_ok=True)
        elif not os.path.lexists(path):
            continue  # Database initialisation is a separate explicit operation.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise Denied("upgrade_workspace_metadata_untrusted") from error
        info = os.fstat(descriptor)
        metadata[path] = (descriptor, info)
        if (directory and not stat.S_ISDIR(info.st_mode)) or (
            not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
        ):
            raise Denied("upgrade_workspace_metadata_untrusted")
        _workspace_identity(path, info)
        # The writable DB directory is owned by the unprivileged worker.
        # Never chmod/chown through a pathname it can swap to a symlink.
        # Correct existing metadata is left untouched.
        if stat.S_IMODE(info.st_mode) != mode:
            os.fchmod(descriptor, mode)
        if os.getuid() == 0:
            uid, gid = int(policy["autonomy_uid"]), int(policy["autonomy_gid"])
            if (info.st_uid, info.st_gid) != (uid, gid):
                os.fchown(descriptor, uid, gid)
        _workspace_identity(path, info)


def upgrade(files: dict, policy: dict) -> None:
    """Migrate only an exact reviewed source to the pinned destination bundle."""
    if (
        os.geteuid() != 0
        or os.getenv("SUDO_USER") != "aaron"
        or os.getenv("SUDO_UID") != str(policy["operator_uid"])
    ):
        raise Denied("local_operator_sudo_required")
    if fingerprint(files) != UPGRADE_BUNDLE:
        raise Denied("upgrade_destination_bundle_not_reviewed")
    transaction = BASE / "upgrade-transaction.json"
    if os.path.lexists(transaction):
        raise Denied("upgrade_reconciliation_required")
    marker = BASE / "installed.json"
    source_bundle = None
    if marker.is_file() and not marker.is_symlink():
        g.trusted(marker)
        source_bundle = json.loads(marker.read_text()).get("bundle")
    managed_sockets: tuple[str, ...]
    managed_services: tuple[str, ...]
    if source_bundle == LEGACY_BUNDLE:
        previous = _verified_legacy_install(files)
        managed_sockets = SOCKETS[:3]
        managed_services = SERVICES[:3]
    elif source_bundle == CURRENT_BUNDLE:
        previous = _verified_current_install(files)
        managed_sockets = SOCKETS
        managed_services = SERVICES
    else:
        raise Denied("upgrade_source_bundle_not_reviewed")
    # New release payload paths must be genuinely absent. Never overwrite a
    # partially installed or independently provisioned service/module merely
    # because its path now appears in the reviewed destination inventory.
    for destination in files:
        if str(destination) not in previous["files"] and os.path.lexists(destination):
            raise Denied("upgrade_destination_unexpected")
    controller = g.Guardian()
    controller.integrity()
    verify_guardian_audit(controller)
    if controller.status().get("state") != "STOPPED":
        raise Denied("guardian_must_be_trusted_stopped")
    _upgrade_service_preconditions(managed_sockets, managed_services)
    guardian_marker = controller.base / "installed.json"
    guardian_before = guardian_marker.read_bytes()
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    created_dirs: list[Path] = []
    workspace_metadata: dict[Path, tuple[int, os.stat_result]] = {}
    replaced: list[Path] = []
    services_touched = False
    destination_activation_attempted = False
    guardian_manifest_touched = False
    retain_reconciliation = False
    transaction_owned = False
    marker = BASE / "installed.json"
    entries = dict(files)
    policy_bytes = POLICY.read_bytes()
    if json.loads(policy_bytes) != policy:
        raise Denied("upgrade_policy_changed")
    entries[POLICY] = (policy_bytes, stat.S_IMODE(POLICY.stat().st_mode))
    # Preserve the existing private broker configuration and its recorded hash.
    private = BASE / "docker-client/config.json"
    if private.is_symlink() or not private.is_file():
        raise Denied("upgrade_private_configuration_missing")
    private_digest = hashlib.sha256(private.read_bytes()).hexdigest()
    old_private_digest = previous["files"].get(str(private))
    if private_digest != old_private_digest:
        raise Denied("upgrade_private_configuration_modified")
    new_digests = {
        str(path): hashlib.sha256(data).hexdigest() for path, (data, _) in entries.items()
    }
    new_digests[str(private)] = private_digest
    new_marker = canonical({"version": 1, "bundle": UPGRADE_BUNDLE, "files": new_digests})
    try:
        for directory, mode_bits in (
            (LIB / "tools", 0o755),
            (BASE / "development-admission", 0o700),
            (JOBS, 0o755),
        ):
            if not directory.exists():
                directory.mkdir(mode=mode_bits)
                directory.chmod(mode_bits)
                created_dirs.append(directory)
            g.trusted(directory, directory=True)
        for workspace_dir in (BASE / "work", BASE / "worktrees"):
            if not os.path.lexists(workspace_dir):
                created_dirs.append(workspace_dir)
        _prepare_development_workspace(policy, workspace_metadata)
        for path, (data, mode_bits) in entries.items():
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise Denied("upgrade_destination_path_invalid")
            staged[path] = _stage(path, data, mode_bits, path.parent)
        marker_stage = _stage(marker, new_marker, 0o644, marker.parent)
        staged[marker] = marker_stage
        # Retain every executable/manifest backup durably before any service or
        # payload mutation. A crash or uncertain rollback never self-retries.
        for path in staged:
            if path.exists():
                backups[path] = _stage(
                    path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.parent
                )
        guardian_backup = _stage(guardian_marker, guardian_before, 0o644, guardian_marker.parent)
        backups[guardian_marker] = guardian_backup
        for directory in {path.parent for path in (*staged.values(), *backups.values())}:
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        exclusive(
            transaction,
            canonical(
                {
                    "version": 1,
                    "source_bundle": source_bundle,
                    "target_bundle": UPGRADE_BUNDLE,
                    "backups": {str(path): str(backup) for path, backup in backups.items()},
                    "staged": {str(path): str(stage) for path, stage in staged.items()},
                    "source_digests": previous["files"],
                    "guardian_manifest_sha256": hashlib.sha256(guardian_before).hexdigest(),
                }
            ),
            0o600,
        )
        transaction_owned = True
        retain_reconciliation = True
        descriptor = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Stop all broker roles before replacing executable payloads, preventing
        # mixed-version execution. No application data or ledgers are touched.
        # Stop every unit belonging to the source bundle before replacing any
        # executable, preventing mixed-version broker execution.
        services_touched = True
        command(["/usr/bin/systemctl", "disable", "--now", *managed_sockets])
        command(["/usr/bin/systemctl", "stop", *managed_services])
        for path, (_descriptor, info) in workspace_metadata.items():
            _workspace_identity(path, info)
        for path, staged_path in staged.items():
            os.replace(staged_path, path)
            replaced.append(path)
        old_manifest = json.loads(guardian_before)
        # Root verifies private configuration through the broker manifest;
        # public Guardian readers must never need to open that private file.
        # Also remove the exact verified entry if an older upgrade exposed it.
        old_manifest["files"] = _public_guardian_files(old_manifest["files"], private_digest)
        old_manifest["files"].update(_public_guardian_files(new_digests, private_digest))
        old_manifest["files"][str(marker)] = hashlib.sha256(new_marker).hexdigest()
        with controller.lock():
            guardian_manifest_touched = True
            replace_manifest(guardian_marker, canonical(old_manifest))
        command(["/usr/bin/systemd-analyze", "verify", *[str(UNITS / name) for name in UNIT_NAMES]])
        command(["/usr/bin/systemctl", "daemon-reload"])
        destination_activation_attempted = True
        command(["/usr/bin/systemctl", "enable", "--now", *SOCKETS])
        controller.integrity()
        if controller.status().get("state") != "STOPPED":
            raise Denied("upgrade_did_not_remain_stopped")
        retain_reconciliation = False
    except BaseException:
        # Restore only executable payloads and manifests; retain all broker data,
        # audit records and ledgers for reconciliation.
        if services_touched:
            try:
                # Activation can partially succeed before systemctl fails.
                # The legacy source lacks publication/controller units, but
                # destination roles may now be running the replacement code.
                rollback_sockets = SOCKETS if destination_activation_attempted else managed_sockets
                rollback_services = (
                    SERVICES if destination_activation_attempted else managed_services
                )
                command(["/usr/bin/systemctl", "disable", "--now", *rollback_sockets])
                command(["/usr/bin/systemctl", "stop", *rollback_services])
            except BaseException as error:
                # Never restore executable bytes underneath a possibly live
                # process, and never destroy the only rollback evidence.
                raise Denied("upgrade_service_reconciliation_required") from error
        try:
            for path in reversed(replaced):
                backup = backups.get(path)
                if backup and backup.exists():
                    os.replace(backup, path)
                else:
                    path.unlink(missing_ok=True)
            workspace_reconciliation = False
            for path, (descriptor, info) in workspace_metadata.items():
                try:
                    current = os.fstat(descriptor)
                    if stat.S_IMODE(current.st_mode) != stat.S_IMODE(info.st_mode):
                        os.fchmod(descriptor, stat.S_IMODE(info.st_mode))
                    if os.getuid() == 0 and (current.st_uid, current.st_gid) != (
                        info.st_uid,
                        info.st_gid,
                    ):
                        os.fchown(descriptor, info.st_uid, info.st_gid)
                    _workspace_identity(path, info)
                except (OSError, Denied):
                    workspace_reconciliation = True
            if guardian_manifest_touched and guardian_marker.exists():
                replace_manifest(guardian_marker, guardian_before)
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if services_touched:
                command(["/usr/bin/systemctl", "daemon-reload"])
            if workspace_reconciliation:
                raise Denied("upgrade_workspace_reconciliation_required")
            if services_touched:
                command(["/usr/bin/systemctl", "enable", "--now", *managed_sockets])
            retain_reconciliation = False
        except BaseException:
            raise
        raise
    finally:
        for descriptor, _info in workspace_metadata.values():
            os.close(descriptor)
        if not retain_reconciliation:
            for path in staged.values():
                path.unlink(missing_ok=True)
            for path in backups.values():
                path.unlink(missing_ok=True)
            if transaction_owned:
                transaction.unlink(missing_ok=True)
    print("Protected broker upgraded; development admission endpoint installed; autonomy STOPPED.")


def existing_upgrade_policy() -> dict:
    if POLICY.is_symlink() or not POLICY.is_file():
        raise Denied("upgrade_policy_missing")
    g.trusted(POLICY)
    value = json.loads(POLICY.read_text())
    if value.get("version") != 1 or value.get("autonomy_uid") != g.identity():
        raise Denied("broker_identity_changed")
    return value


def verify_guardian_audit(controller) -> None:
    path = controller.base / "audit/events.jsonl"
    controller.secure(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise Denied("guardian_audit_reconciliation_required")
    previous = "0" * 64
    for line in path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise Denied("guardian_audit_reconciliation_required")
        entry = json.loads(line)
        digest = entry.pop("hash")
        if (
            entry["previous"] != previous
            or hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest() != digest
        ):
            raise Denied("guardian_audit_reconciliation_required")
        previous = digest


def exclusive(path: Path, data: bytes, mode: int) -> None:
    # Initial creation must be private; final public modes are deliberately set
    # only once the complete reviewed payload has been written and flushed.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
        os.fsync(handle.fileno())


def replace_manifest(path: Path, raw: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".upgrade-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _public_guardian_files(files: dict, private_digest: str) -> dict:
    """Exclude only verified private Docker configuration, not other integrity pins."""
    private = str(BASE / "docker-client/config.json")
    public = dict(files)
    if private in public:
        if public[private] != private_digest:
            raise Denied("unexpected_private_configuration")
        del public[private]
    return public


def repair_public_manifest(files: dict) -> None:
    """One known metadata repair, not a reinstall or permission change.

    Private Docker configuration remains hashed in the broker's own manifest,
    which the privileged broker verifies before serving requests. The public
    Guardian pins that entire manifest, but must not directly open private files.
    """
    if os.geteuid() != 0 or os.getenv("SUDO_USER") != "aaron":
        raise Denied("local_operator_sudo_required")
    mode, broker_manifest = checked_existing(files, {})
    if mode != "REPEAT":
        raise Denied("installed_broker_required")
    policy = json.loads(POLICY.read_text())
    if os.getenv("SUDO_UID") != str(policy["operator_uid"]):
        raise Denied("local_operator_sudo_required")
    if g.identity() != policy["autonomy_uid"]:
        raise Denied("broker_identity_changed")
    controller = g.Guardian()
    private = BASE / "docker-client/config.json"
    marker = BASE / "installed.json"
    expected = hashlib.sha256(b"{}\n").hexdigest()
    with controller.lock():
        controller.integrity()
        verify_guardian_audit(controller)
        if controller.state()["state"] != "STOPPED":
            raise Denied("guardian_must_be_stopped")
        if broker_manifest["files"].get(str(private)) != expected:
            raise Denied("unexpected_private_configuration")
        guardian_marker = controller.base / "installed.json"
        original = guardian_marker.read_bytes()
        manifest = json.loads(original)
        if manifest["files"].get(str(marker)) != hashlib.sha256(marker.read_bytes()).hexdigest():
            raise Denied("broker_manifest_not_guardian_pinned")
        if str(private) not in manifest["files"]:
            print("Public Guardian manifest already repaired; autonomy STOPPED.")
            return
        manifest["files"] = _public_guardian_files(manifest["files"], expected)
        revised = canonical(manifest)
        digest = hashlib.sha256(revised).hexdigest()
        records = Records(BASE / "records")
        records.audit("public_manifest_repair_requested", "public-status-v1", digest)
        try:
            replace_manifest(guardian_marker, revised)
            controller.integrity()
            records.audit("public_manifest_repair_completed", "public-status-v1", digest)
        except BaseException:
            replace_manifest(guardian_marker, original)
            raise
    print("Public Guardian manifest repaired; private broker integrity retained; autonomy STOPPED.")


def install(files: dict, policy: dict) -> None:
    if (
        os.geteuid() != 0
        or os.getenv("SUDO_USER") != "aaron"
        or os.getenv("SUDO_UID") != str(policy["operator_uid"])
    ):
        raise Denied("local_operator_sudo_required")
    controller = g.Guardian()
    mode, _ = checked_existing(files, policy)
    with controller.lock():
        verify_guardian_audit(controller)
    controller.stop(policy["operator_uid"])
    controller.integrity()
    if mode == "REPEAT":
        print("Verified broker retained; autonomy STOPPED.")
        return
    marker = BASE / "installed.json"
    guardian_marker = controller.base / "installed.json"
    original = guardian_marker.read_bytes()
    old_manifest = json.loads(original)
    installed, directories = [], []
    try:
        for directory, mode_bits in [
            (LIB, 0o755),
            (LIB / "tools", 0o755),
            (BASE, 0o755),
            (BASE / "records", 0o700),
            (BASE / "snapshots", 0o700),
            (BASE / "worker", 0o700),
            (BASE / "operator", 0o700),
            (BASE / "development-admission", 0o700),
            (BASE / "docker-client", 0o700),
            (JOBS, 0o755),
            (DROPIN.parent, 0o755),
        ]:
            if not directory.exists():
                directory.mkdir(mode=mode_bits)
                directory.chmod(mode_bits)
                directories.append(directory)
            g.trusted(directory, directory=True)
        entries = dict(files)
        entries[POLICY] = (canonical(policy), 0o644)
        entries[BASE / "docker-client/config.json"] = (b"{}\n", 0o600)
        for path, (data, mode_bits) in entries.items():
            exclusive(path, data, mode_bits)
            installed.append(path)
            g.trusted(path)
        digests = {
            str(path): hashlib.sha256(data).hexdigest() for path, (data, _) in entries.items()
        }
        exclusive(
            marker, canonical({"version": 1, "bundle": fingerprint(files), "files": digests}), 0o644
        )
        installed.append(marker)
        # Extend integrity coverage; preserve existing Guardian policy, state,
        # account, unit, audit history and original installation identity.
        # Public status must remain readable by the isolated autonomy UID.
        # The private file remains pinned in the root-verified broker manifest;
        # that complete manifest is itself pinned by the Guardian below.
        private_digest = digests[str(BASE / "docker-client/config.json")]
        old_manifest["files"] = _public_guardian_files(old_manifest["files"], private_digest)
        old_manifest["files"].update(_public_guardian_files(digests, private_digest))
        old_manifest["files"][str(marker)] = hashlib.sha256(marker.read_bytes()).hexdigest()
        with controller.lock():
            replace_manifest(guardian_marker, canonical(old_manifest))
        command(["/usr/bin/systemd-analyze", "verify", *[str(UNITS / name) for name in UNIT_NAMES]])
        command(["/usr/bin/systemctl", "daemon-reload"])
        command(["/usr/bin/systemctl", "enable", "--now", *SOCKETS])
        controller.integrity()
        g.identity()
        if controller.status().get("state") != "STOPPED":
            raise Denied("installation_did_not_remain_stopped")
    except BaseException:
        # Only this transaction's known new paths are removed. User data, audit,
        # worktrees and the original Guardian are never deleted or restored.
        # A socket may have activated its service before activation failed.
        # If stopping either fails, keep protected payload/manifest in place for
        # reconciliation rather than removing executable files under a process.
        command(["/usr/bin/systemctl", "disable", "--now", *SOCKETS])
        command(["/usr/bin/systemctl", "stop", *SERVICES])
        with controller.lock():
            replace_manifest(guardian_marker, original)
        for path in reversed(installed):
            path.unlink()
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError as error:
                if error.errno != errno.ENOTEMPTY:
                    raise
                # Keep any new rejection audit/ledger; never erase evidence.
        command(["/usr/bin/systemctl", "daemon-reload"])
        raise
    print(
        "Protected broker installed; autonomy STOPPED. No credentials copied; no production release changed."
    )


def main(args: list[str]) -> int:
    if args not in ([], ["--check"], ["--upgrade"], ["--repair-public-manifest"]):
        return 2
    try:
        files = payload()
        if args == ["--repair-public-manifest"]:
            repair_public_manifest(files)
            return 0
        if args == ["--upgrade"]:
            policy = existing_upgrade_policy()
            upgrade(files, policy)
            return 0
        policy = reviewed_policy(files)
        mode, _ = checked_existing(files, policy)
        if args == ["--check"]:
            print(
                json.dumps(
                    {
                        "ready": True,
                        "mode": mode,
                        "guardian": "STOPPED",
                        "host_modified": False,
                        "bundle": fingerprint(files),
                        "autonomy_uid": policy["autonomy_uid"],
                        "tool_image_pinned": True,
                        "core_image_pinned": True,
                        "release_scope": "core-assets-only-no-data-rewind",
                        "remote_operator_keys_enrolled": bool(policy.get("operator_public_keys")),
                        "independent_reviewer_provisioned": bool(policy.get("reviewer_ids")),
                        "paths": sorted(str(p) for p in [*files, POLICY, BASE, JOBS]),
                    }
                )
            )
        else:
            install(files, policy)
        return 0
    except Denied as error:
        print("Broker precheck/upgrade refused: " + error.code, file=sys.stderr)
        return 1
    except Exception as error:
        print(
            "Broker precheck/upgrade refused (" + type(error).__name__ + "); inspect locally.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
