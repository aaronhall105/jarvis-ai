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
)
UNIT_NAMES = tuple(
    "jarvis-autonomy-broker-" + role + suffix
    for role in ("worker", "operator")
    for suffix in (".service", ".socket")
)
DROPIN = UNITS / "jarvis-autonomy.service.d/broker.conf"
SOCKETS = ("jarvis-autonomy-broker-operator.socket", "jarvis-autonomy-broker-worker.socket")
SERVICES = ("jarvis-autonomy-broker-operator.service", "jarvis-autonomy-broker-worker.service")


def payload() -> dict[Path, tuple[bytes, int]]:
    files = {
        LIB / "tools" / (name + ".py"): ((ROOT / "tools" / (name + ".py")).read_bytes(), 0o644)
        for name in MODULES
    }
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
        if manifest["files"][str(private)] != expected:
            raise Denied("unexpected_private_configuration")
        del manifest["files"][str(private)]
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
        old_manifest["files"].update(
            {
                name: digest
                for name, digest in digests.items()
                if name != str(BASE / "docker-client/config.json")
            }
        )
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
    if args not in ([], ["--check"], ["--repair-public-manifest"]):
        return 2
    try:
        files = payload()
        if args == ["--repair-public-manifest"]:
            repair_public_manifest(files)
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
