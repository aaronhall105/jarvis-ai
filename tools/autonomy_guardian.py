"""Root-installed autonomy interlock. No model, network, shell, or release executor.

All paths are fixed in the installed entry point. Constructor injection is only for
unprivileged deterministic tests, never a command-line/configuration override.
"""

from __future__ import annotations

import fcntl
import grp
import hashlib
import json
import os
import pwd
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ACCOUNT = "jarvis-autonomy"
BASE = Path("/var/lib/jarvis-autonomy-guardian")
POLICY = Path("/etc/jarvis-autonomy/guardian.json")
WORK = Path("/var/lib/jarvis-autonomy/work")
UNIT = "jarvis-autonomy.service"
GUARDIAN_UNIT = "jarvis-autonomy-guardian.service"
COMMANDS = {"status", "validate", "check-active", "verify-isolation", "stop", "resume", "watch"}


class UnsafeState(RuntimeError):
    pass


def trusted(path: Path, uid: int = 0, *, directory: bool = False) -> None:
    info = path.lstat()
    if info.st_uid != uid or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
        raise UnsafeState("Untrusted ownership or mode")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeState("Expected protected directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UnsafeState("Expected protected regular file")


def trusted_parents(path: Path) -> None:
    for parent in path.parents:
        trusted(parent, directory=True)


def validate_identity(user: Any, groups: set[str]) -> None:
    if (
        user.pw_name != ACCOUNT
        or not 0 < user.pw_uid < 1000
        or user.pw_dir != "/var/lib/jarvis-autonomy"
        or user.pw_shell != "/usr/sbin/nologin"
        or groups != {ACCOUNT}
    ):
        raise UnsafeState("Autonomy identity is not isolated")


def identity() -> int:
    user = pwd.getpwnam(ACCOUNT)
    groups = {grp.getgrgid(g).gr_name for g in os.getgrouplist(ACCOUNT, user.pw_gid)}
    validate_identity(user, groups)
    return user.pw_uid


def operator_allowed(euid: int, actor_uid: int, policy: dict[str, Any]) -> bool:
    return euid == 0 and actor_uid in {0, policy["operator_uid"]}


def fixed_systemctl(*arguments: str) -> bool:
    result = subprocess.run(
        ["/usr/bin/systemctl", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=8,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        check=False,
    )
    return result.returncode == 0


def terminate_autonomy() -> None:
    # No client-supplied unit, UID or command is accepted here.
    try:
        fixed_systemctl("kill", "--signal=SIGKILL", "--kill-whom=all", UNIT)
    finally:
        try:
            uid = identity()
            subprocess.run(
                ["/usr/bin/pkill", "-KILL", "-u", str(uid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                env={"PATH": "/usr/bin", "LANG": "C"},
            )
        except (KeyError, UnsafeState):
            # Never target a reused/privileged account; fixed systemd fence still applies.
            pass
        finally:
            fixed_systemctl("stop", UNIT)


class Guardian:
    def __init__(
        self,
        base: Path = BASE,
        policy_path: Path = POLICY,
        owner_uid: int = 0,
        check_parents: bool = True,
    ):
        self.base = base
        self.policy_path = policy_path
        self.owner_uid = owner_uid
        self.check_parents = check_parents

    def secure(self, path: Path, *, directory: bool = False) -> None:
        trusted(path, self.owner_uid, directory=directory)
        if self.check_parents:
            trusted_parents(path)

    def policy(self) -> dict[str, Any]:
        self.secure(self.policy_path)
        value = json.loads(self.policy_path.read_text())
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or type(value.get("operator_uid")) is not int
            or value["operator_uid"] < 1000
            or value.get("autonomy_account") != ACCOUNT
            or value.get("privileged_operations") != []
        ):
            raise UnsafeState("Invalid Guardian policy")
        # Every privileged operation is denied in this bootstrap. No generic executor.
        return value

    def state(self) -> dict[str, Any]:
        self.secure(self.base / "state.json")
        value = json.loads((self.base / "state.json").read_text())
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("state") not in {"ACTIVE", "STOPPED"}
            or str(uuid.UUID(value["generation"])) != value["generation"]
        ):
            raise UnsafeState("Invalid Guardian state")
        return value

    def integrity(self) -> None:
        path = self.base / "installed.json"
        self.secure(path)
        manifest = json.loads(path.read_text())
        if not isinstance(manifest, dict):
            raise UnsafeState("Invalid installation manifest")
        files = manifest["files"]
        if not isinstance(files, dict) or str(self.policy_path) not in files:
            raise UnsafeState("Invalid installation manifest")
        for name, digest in files.items():
            target = Path(name)
            self.secure(target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise UnsafeState("Guardian installation changed")

    def status(self) -> dict[str, Any]:
        try:
            self.policy()
            self.integrity()
            value = self.state()
            return {"state": value["state"], "generation": value["generation"], "trusted": True}
        except (OSError, ValueError, KeyError, TypeError, UnsafeState):
            return {"state": "STOPPED", "trusted": False, "reason": "Guardian validation failed"}

    @contextmanager
    def lock(self):
        self.secure(self.base, directory=True)
        path = self.base / "control.lock"
        self.secure(path)
        with path.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def write_state(self, state: str) -> None:
        if state not in {"ACTIVE", "STOPPED"}:
            raise UnsafeState("Unknown state")
        self.secure(self.base, directory=True)
        value = {
            "version": 1,
            "state": state,
            "generation": str(uuid.uuid4()),
            "changed_at": int(time.time()),
        }
        fd, name = tempfile.mkstemp(prefix=".state-", dir=self.base)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(value, handle)
                handle.flush()
                os.fchmod(handle.fileno(), 0o644)
                os.fsync(handle.fileno())
            os.replace(name, self.base / "state.json")
            directory = os.open(self.base, os.O_DIRECTORY | os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def audit(self, event: str, actor_uid: int) -> None:
        if event not in {"installed", "stopped", "resumed", "resume_rejected", "fence_triggered"}:
            raise UnsafeState("Unknown audit event")
        path = self.base / "audit" / "events.jsonl"
        self.secure(path)
        if path.stat().st_mode & 0o077:
            raise UnsafeState("Audit must be private")
        with path.open("r+b") as handle:
            handle.seek(0, os.SEEK_END)
            length = handle.tell()
            previous = "0" * 64
            if length:
                handle.seek(max(0, length - 2048))
                tail = handle.read()
                if not tail.endswith(b"\n"):
                    raise UnsafeState("Audit needs operator reconciliation")
                previous = json.loads(tail.splitlines()[-1])["hash"]
            entry = {
                "event": event,
                "actor_uid": actor_uid,
                "at": int(time.time()),
                "previous": previous,
            }
            digest = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
            handle.seek(0, os.SEEK_END)
            handle.write((json.dumps({**entry, "hash": digest}, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())

    def stop(self, actor_uid: int, terminate=terminate_autonomy) -> None:
        # Stop remains effective even with broken policy/audit. Never change normal Jarvis units.
        entered = False
        try:
            with self.lock():
                entered = True
                try:
                    self.write_state("STOPPED")
                    self.audit("stopped", actor_uid)
                finally:
                    terminate()
        finally:
            if not entered:
                terminate()

    def resume(
        self,
        euid: int,
        actor_uid: int,
        runtime_ready,
        start,
        terminate=terminate_autonomy,
        *,
        expected_generation: str | None = None,
    ) -> None:
        with self.lock():
            policy = self.policy()
            if not operator_allowed(euid, actor_uid, policy):
                raise UnsafeState("Operator authentication required")
            self.integrity()
            if (
                expected_generation is not None
                and self.state()["generation"] != expected_generation
            ):
                self.audit("resume_rejected", actor_uid)
                raise UnsafeState("Operator request is stale")
            if not self.status()["trusted"] or not runtime_ready():
                self.audit("resume_rejected", actor_uid)
                raise UnsafeState("Autonomy runtime is not ready")
            try:
                self.write_state("ACTIVE")
                if not start():
                    raise UnsafeState("Autonomy service did not start")
                self.audit("resumed", actor_uid)
            except BaseException:
                try:
                    self.write_state("STOPPED")
                    self.audit("resume_rejected", actor_uid)
                finally:
                    terminate()
                raise


def runtime_ready() -> bool:
    identity()
    target = WORK / "runtime" / "governor.py"
    return target.is_file() and fixed_systemctl("is-active", "--quiet", GUARDIAN_UNIT)


def verify_isolation(guardian: Guardian) -> bool:
    """Read-only kernel/DAC checks, run by installer as the actual dedicated UID."""
    if os.geteuid() != identity() or not guardian.status()["trusted"]:
        return False
    protected = [
        BASE,
        BASE / "state.json",
        BASE / "installed.json",
        BASE / "audit",
        POLICY.parent,
        POLICY,
        Path("/usr/local/libexec/jarvis-autonomy"),
        Path("/usr/local/bin/jarvis-autonomy"),
        Path("/etc/systemd/system"),
        Path("/etc/sudoers.d"),
    ]
    if any(os.access(path, os.W_OK) for path in protected):
        return False
    if any(
        os.access(path, os.W_OK)
        for path in [
            Path("/run/docker.sock"),
            Path("/var/run/docker.sock"),
        ]
    ):
        return False
    if os.access(BASE / "audit/events.jsonl", os.R_OK):
        return False
    # A new dedicated identity must have no sudo grant, including unrelated host
    # policy. This is a non-interactive policy query, never an attempted resume.
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "-l"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        env={"PATH": "/usr/bin", "LANG": "C"},
        check=False,
    )
    return result.returncode != 0


def watch(guardian: Guardian) -> None:
    # Restart/reboot never resumes unattended work; operator must explicitly resume.
    guardian.stop(0)
    try:
        while True:
            # Serialize observation/fencing with resume; never kill a newly resumed
            # process based on a stale STOPPED observation.
            with guardian.lock():
                safe = guardian.status()["state"] == "ACTIVE"
                try:
                    identity()
                except (KeyError, UnsafeState):
                    safe = False
                if not safe:
                    terminate_autonomy()
            time.sleep(1)
    finally:
        terminate_autonomy()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print("Expected exactly one Guardian operation", file=sys.stderr)
        return 2
    command = argv[0]
    guardian = Guardian()
    if command == "verify-isolation":
        try:
            passed = verify_isolation(guardian)
        except (OSError, ValueError, KeyError, UnsafeState, subprocess.SubprocessError):
            passed = False
        print(json.dumps({"isolation_verified": passed}))
        return 0 if passed else 1
    if command in {"status", "validate", "check-active"}:
        status = guardian.status()
        try:
            identity()
        except (KeyError, UnsafeState):
            status = {"state": "STOPPED", "trusted": False, "reason": "Identity validation failed"}
        if command != "check-active":
            print(json.dumps(status))
        return (
            0
            if status["trusted"] and (command != "check-active" or status["state"] == "ACTIVE")
            else 1
        )
    if os.geteuid() != 0:
        print("Operator authentication required", file=sys.stderr)
        return 1
    try:
        actor_uid = int(os.getenv("SUDO_UID", "0"))
        if command == "stop":
            guardian.stop(actor_uid)
        elif command == "resume":
            guardian.resume(0, actor_uid, runtime_ready, lambda: fixed_systemctl("start", UNIT))
        elif command == "watch":
            if actor_uid != 0:
                raise UnsafeState("Guardian service only")
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            watch(guardian)
        print(json.dumps(guardian.status()))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnsafeState, subprocess.SubprocessError):
        print("Guardian operation failed closed; inspect protected audit locally", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
