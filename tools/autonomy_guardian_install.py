"""One-time operator installation. --check is unprivileged and never changes the host."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = Path("/usr/local/libexec/jarvis-autonomy")
ETC = Path("/etc/jarvis-autonomy")
BASE = Path("/var/lib/jarvis-autonomy-guardian")
AUTONOMY_HOME = Path("/var/lib/jarvis-autonomy")
SUDOERS = Path("/etc/sudoers.d/jarvis-autonomy")
FILES = {
    "tools/autonomy_guardian.py": (LIB / "guardian.py", 0o644),
    "tools/autonomy_guardian_control.sh": (LIB / "control", 0o755),
    "tools/autonomy_guardian_cli.sh": (Path("/usr/local/bin/jarvis-autonomy"), 0o755),
    "systemd/autonomy/jarvis-autonomy-guardian.service": (
        Path("/etc/systemd/system/jarvis-autonomy-guardian.service"),
        0o644,
    ),
    "systemd/autonomy/jarvis-autonomy.service": (
        Path("/etc/systemd/system/jarvis-autonomy.service"),
        0o644,
    ),
}


def load_guardian(path: Path):
    spec = importlib.util.spec_from_file_location("guardian", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Guardian source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle_hash() -> str:
    digest = hashlib.sha256()
    for source in sorted(
        [*FILES, "tools/install_autonomy_guardian.sh", "tools/autonomy_guardian_install.py"]
    ):
        path = ROOT / source
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise RuntimeError("Unsafe installer source")
        digest.update(source.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def sudo_policy(operator: str) -> str:
    if operator != "aaron":
        raise RuntimeError("This reviewed host installation is for Aaron only")
    return (
        "# Jarvis autonomy: operator controls only; NO autonomous-user grant.\n"
        "Defaults!/usr/local/libexec/jarvis-autonomy/control env_reset, !setenv\n"
        "aaron ALL=(root) NOPASSWD: /usr/local/libexec/jarvis-autonomy/control stop, "
        "/usr/local/libexec/jarvis-autonomy/control resume\n"
    )


def run(argv: list[str]) -> None:
    subprocess.run(
        argv,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )


def write(path: Path, data: bytes, mode: int) -> None:
    # Parent is root-owned/non-writable before this function is called.
    # Keep incomplete content private, even for a final public helper/manifest.
    # Publish only the reviewed final mode after the complete payload is flushed.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
        os.fsync(handle.fileno())


def install() -> None:
    if os.geteuid() != 0 or os.getenv("SUDO_USER") != "aaron":
        raise RuntimeError("Run the reviewed installer locally using sudo as Aaron")
    operator = pwd.getpwnam("aaron")
    if int(os.getenv("SUDO_UID", "-1")) != operator.pw_uid or operator.pw_uid < 1000:
        raise RuntimeError("Unexpected operator identity")
    guardian = load_guardian(ROOT / "tools/autonomy_guardian.py")
    fingerprint = bundle_hash()
    marker = BASE / "installed.json"
    if marker.exists():
        guardian.trusted(marker)
        previous = json.loads(marker.read_text())
        if previous.get("bundle") != fingerprint:
            raise RuntimeError("Different installed bundle: explicit operator review required")
        guardian.identity()
        controller = guardian.Guardian()
        controller.integrity()
        guardian.trusted(SUDOERS)
        if SUDOERS.read_text() != sudo_policy(operator.pw_name):
            raise RuntimeError("Installed operator authority differs from reviewed policy")
        run(["/usr/sbin/visudo", "-cf", str(SUDOERS)])
        controller.stop(operator.pw_uid)
        run(
            [
                "/usr/sbin/runuser",
                "-u",
                guardian.ACCOUNT,
                "--",
                "/usr/bin/python3",
                "-I",
                str(LIB / "guardian.py"),
                "verify-isolation",
            ]
        )
        print("Existing verified installation retained; autonomy STOPPED.")
        return
    # Refuse unmanaged files, symlinks, or partially installed/conflicting state.
    targets = [LIB, ETC, BASE, AUTONOMY_HOME, SUDOERS, *[p for p, _ in FILES.values()]]
    for target in targets:
        if target.exists() or target.is_symlink():
            raise RuntimeError("Conflicting installation path; operator inspection required")
        for parent in target.parents:
            if parent.exists():
                guardian.trusted(parent, directory=True)
    try:
        pwd.getpwnam(guardian.ACCOUNT)
    except KeyError:
        pass
    else:
        raise RuntimeError("Existing autonomy account needs operator inspection")
    for program in [
        "/usr/sbin/visudo",
        "/usr/sbin/useradd",
        "/usr/sbin/runuser",
        "/usr/bin/sudo",
        "/usr/bin/systemctl",
        "/usr/bin/pkill",
    ]:
        if not os.access(program, os.X_OK):
            raise RuntimeError("Required host program missing")
    # Private root staging; privileged runtime never imports from the source worktree.
    with tempfile.TemporaryDirectory(prefix="jarvis-guardian-install-", dir="/run") as scratch:
        staging = Path(scratch)
        staged = {}
        for source, (destination, mode) in FILES.items():
            data = (ROOT / source).read_bytes()
            local = staging / destination.name
            write(local, data, mode)
            staged[destination] = (local, mode)
        policy_text = sudo_policy(operator.pw_name)
        write(staging / "sudoers", policy_text.encode(), 0o440)
        run(["/usr/sbin/visudo", "-cf", str(staging / "sudoers")])
        if bundle_hash() != fingerprint:
            raise RuntimeError("Installer source changed during staging")
        run(
            [
                "/usr/sbin/useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--home-dir",
                str(AUTONOMY_HOME),
                "--shell",
                "/usr/sbin/nologin",
                guardian.ACCOUNT,
            ]
        )
        uid = guardian.identity()
        gid = pwd.getpwnam(guardian.ACCOUNT).pw_gid
        for directory, mode in [
            (LIB, 0o755),
            (ETC, 0o755),
            (BASE, 0o755),
            (BASE / "audit", 0o700),
            (AUTONOMY_HOME, 0o755),
        ]:
            directory.mkdir(mode=mode)
            directory.chmod(mode)
        work = AUTONOMY_HOME / "work"
        work.mkdir(mode=0o700)
        os.chown(work, uid, gid)
        digests = {}
        for destination, (local, mode) in staged.items():
            data = local.read_bytes()
            write(destination, data, mode)
            digests[str(destination)] = hashlib.sha256(data).hexdigest()
        policy = {
            "version": 1,
            "operator_uid": operator.pw_uid,
            "autonomy_account": guardian.ACCOUNT,
            "privileged_operations": [],
            "note": "No deploy/Docker/credential broker is authorized by this bootstrap",
        }
        policy_data = (json.dumps(policy, indent=2) + "\n").encode()
        write(ETC / "guardian.json", policy_data, 0o644)
        digests[str(ETC / "guardian.json")] = hashlib.sha256(policy_data).hexdigest()
        write(BASE / "control.lock", b"", 0o600)
        write(BASE / "audit" / "events.jsonl", b"", 0o600)
        write(
            marker,
            json.dumps({"version": 1, "bundle": fingerprint, "files": digests}).encode(),
            0o644,
        )
        controller = guardian.Guardian()
        controller.write_state("STOPPED")
        controller.audit("installed", operator.pw_uid)
        write(SUDOERS, policy_text.encode(), 0o440)
        run(["/usr/sbin/visudo", "-cf", str(SUDOERS)])
        controller.integrity()
        guardian.trusted(SUDOERS)
        guardian.identity()
        run(
            [
                "/usr/sbin/runuser",
                "-u",
                guardian.ACCOUNT,
                "--",
                "/usr/bin/python3",
                "-I",
                str(LIB / "guardian.py"),
                "verify-isolation",
            ]
        )
        run(["/usr/bin/systemctl", "daemon-reload"])
        # Only the protective interlock starts. Worker has no enable/install target.
        run(["/usr/bin/systemctl", "enable", "--now", guardian.GUARDIAN_UNIT])
        run(["/usr/bin/systemctl", "is-active", "--quiet", guardian.GUARDIAN_UNIT])
        if controller.status().get("state") != "STOPPED":
            raise RuntimeError("Installation did not remain stopped")
    print(
        "Guardian installed; dedicated account isolated; autonomy STOPPED. No credentials copied."
    )


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        try:
            fingerprint = bundle_hash()
            print(
                json.dumps(
                    {
                        "bundle_sha256": fingerprint,
                        "host_modified": False,
                        "install_paths": [str(p) for p, _ in FILES.values()],
                        "autonomy_on_install": "STOPPED",
                    },
                    indent=2,
                )
            )
            return 0
        except (OSError, RuntimeError):
            print("Installer source validation failed", file=sys.stderr)
            return 1
    if argv:
        print(
            "Only --check or a local sudo installation without arguments is supported",
            file=sys.stderr,
        )
        return 2
    try:
        install()
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print(
            "Installation refused or incomplete. No enablement requested; inspect locally before retrying.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
