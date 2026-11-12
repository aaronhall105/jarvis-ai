"""Pinned, offline-provisioned Python for the unprivileged coding worker only.

This module uses only the system standard library. The privileged broker must
never import packages from the worker environment. A separately authorised
installation seals one new, versioned environment; ordinary execution only
verifies its complete inventory and returns its fixed interpreter path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import venv
from typing import Any

LOCK_PATH = Path(__file__).with_suffix(".lock.json")
ENVIRONMENT_PARENT = Path("/opt")
MANIFEST = "environment.json"
CACHE_DIRECTORY = "bytecode-disabled"
OWNER_UID = 0


class EnvironmentError(RuntimeError):
    """An unverified worker environment must never be executed."""


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _lock() -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(LOCK_PATH.read_bytes())
        if value["version"] != 1 or value["python"]["version"] != "3.14.4":
            raise ValueError
        names = set()
        for wheel in value["wheels"]:
            if (
                set(wheel) != {"filename", "sha256", "name", "version"}
                or not re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", wheel["filename"])
                or not re.fullmatch(r"[a-f0-9]{64}", wheel["sha256"])
                or wheel["name"] in names
            ):
                raise ValueError
            names.add(wheel["name"])
        required = {"openai", "cryptography", "pytest", "pytest-asyncio", "ruff", "bandit", "pip"}
        if not required.issubset(names):
            raise ValueError
        if next(w["version"] for w in value["wheels"] if w["name"] == "openai") != "2.54.0":
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
        raise EnvironmentError("worker_environment_lock_invalid") from exc
    return value, hashlib.sha256(_encoded(value)).hexdigest()


def environment_path() -> Path:
    _, digest = _lock()
    return ENVIRONMENT_PARENT / ("jarvis-supervised-worker-" + digest[:20])


def worker_python_arguments(interpreter: Path) -> list[str]:
    """Never load mutable OS timestamp caches, nor emit worker bytecode."""
    return ["-I", "-B", "-X", "pycache_prefix=" + str(interpreter.parent.parent / CACHE_DIRECTORY)]


def _trusted_path(path: Path, *, directory: bool | None = None) -> os.stat_result:
    """No symlink/writable ancestor can replace a verified root-owned object."""
    for parent in reversed((path, *path.parents)):
        metadata = parent.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != OWNER_UID
            or metadata.st_mode & 0o022
        ):
            raise EnvironmentError("worker_environment_path_untrusted")
    metadata = path.stat()
    if directory is True and not stat.S_ISDIR(metadata.st_mode):
        raise EnvironmentError("worker_environment_directory_invalid")
    if directory is False and not stat.S_ISREG(metadata.st_mode):
        raise EnvironmentError("worker_environment_file_invalid")
    return metadata


def _inventory(root: Path, *, sealed: bool, exclude_manifest: bool = False) -> dict[str, Any]:
    _trusted_path(root, directory=True)
    inventory: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if exclude_manifest and relative == MANIFEST:
            continue
        metadata = path.lstat()
        if metadata.st_uid != OWNER_UID or (
            not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & (0o222 if sealed else 0o022)
        ):
            raise EnvironmentError("worker_environment_permissions_invalid")
        if stat.S_ISLNK(metadata.st_mode):
            # Venv's python aliases/lib64 may only reference the same sealed tree.
            destination = path.resolve(strict=True)
            if not destination.is_relative_to(root):
                raise EnvironmentError("worker_environment_symlink_escape")
            inventory[relative] = {"link": os.readlink(path)}
        elif stat.S_ISDIR(metadata.st_mode):
            inventory[relative] = {"directory": True, "mode": stat.S_IMODE(metadata.st_mode)}
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            inventory[relative] = {
                "sha256": _digest(path),
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        else:
            raise EnvironmentError("worker_environment_object_invalid")
    return inventory


def _base_inventory(lock: dict[str, Any]) -> dict[str, str]:
    # CPython searches this archive before its stdlib directory. The reviewed
    # Ubuntu baseline has none; never accept an unpinned earlier import source.
    archive = Path(lock["python"]["stdlib"]).parent / "python314.zip"
    if archive.exists() or archive.is_symlink():
        raise EnvironmentError("worker_environment_stdlib_archive_denied")
    source = Path(lock["python"]["executable"])
    _trusted_path(source, directory=False)
    if _digest(source) != lock["python"]["sha256"]:
        raise EnvironmentError("worker_environment_python_drift")
    root = Path(lock["python"]["stdlib"])
    _trusted_path(root, directory=True)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            _trusted_path(path, directory=True)
            continue
        destination = path.resolve(strict=True)
        _trusted_path(destination, directory=False)
        relative = path.relative_to(root)
        # Worker Python always redirects cache reads to its empty sealed
        # prefix. Normal OS __pycache__ generation therefore cannot change
        # worker imports or the pinned source/native/sourceless-bytecode set.
        if "__pycache__" not in relative.parts:
            result[relative.as_posix()] = _digest(destination)
    if hashlib.sha256(_encoded(result)).hexdigest() != lock["python"]["stdlib_sha256"]:
        raise EnvironmentError("worker_environment_stdlib_drift")
    return result


def _versions(root: Path, lock: dict[str, Any]) -> None:
    site = root / "lib/python3.14/site-packages"
    actual = {}
    for metadata in site.glob("*.dist-info/METADATA"):
        values = {}
        for line in metadata.read_text().splitlines():
            if not line:
                break
            if line.startswith(("Name: ", "Version: ")):
                key, value = line.split(": ", 1)
                values[key] = value
        name = re.sub(r"[-_.]+", "-", values.get("Name", "")).lower()
        if not name or name in actual:
            raise EnvironmentError("worker_environment_distribution_invalid")
        actual[name] = values.get("Version")
    expected = {wheel["name"]: wheel["version"] for wheel in lock["wheels"]}
    if actual != expected:
        raise EnvironmentError("worker_environment_dependencies_mismatch")
    # No wheel in this reviewed dependency set requires import-time path hooks.
    if (
        list(site.rglob("*.pth"))
        or list(site.rglob("sitecustomize.py"))
        or list(site.rglob("usercustomize.py"))
    ):
        raise EnvironmentError("worker_environment_import_hook_denied")


def verify_worker_environment() -> Path:
    """Hash every installed file offline before returning the fixed interpreter."""
    try:
        lock, lock_digest = _lock()
        root = environment_path()
        _trusted_path(LOCK_PATH, directory=False)
        if _trusted_path(root, directory=True).st_mode & 0o222:
            raise EnvironmentError("worker_environment_root_mutable")
        metadata = _trusted_path(root / MANIFEST, directory=False)
        if metadata.st_mode & 0o222:
            raise EnvironmentError("worker_environment_manifest_mutable")
        manifest = json.loads((root / MANIFEST).read_bytes())
        if (
            set(manifest) != {"version", "lock_sha256", "files"}
            or manifest["version"] != 1
            or manifest["lock_sha256"] != lock_digest
        ):
            raise EnvironmentError("worker_environment_manifest_invalid")
        _base_inventory(lock)
        if _inventory(root, sealed=True, exclude_manifest=True) != manifest["files"]:
            raise EnvironmentError("worker_environment_inventory_mismatch")
        _versions(root, lock)
        cache = root / CACHE_DIRECTORY
        if _trusted_path(cache, directory=True).st_mode & 0o222 or any(cache.iterdir()):
            raise EnvironmentError("worker_environment_bytecode_cache_invalid")
        interpreter = root / "bin/python3.14"
        if interpreter.is_symlink() or _digest(interpreter) != lock["python"]["sha256"]:
            raise EnvironmentError("worker_environment_interpreter_invalid")
        configuration = (root / "pyvenv.cfg").read_text()
        if "include-system-site-packages = false" not in configuration:
            raise EnvironmentError("worker_environment_system_packages_denied")
        return interpreter
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise EnvironmentError("worker_environment_unavailable") from exc


def _requirements(lock: dict[str, Any]) -> str:
    return "".join(
        f"{wheel['name']}=={wheel['version']} --hash=sha256:{wheel['sha256']}\n"
        for wheel in lock["wheels"]
    )


def download_wheels(destination: Path) -> None:
    """Unprivileged preparation only; the privileged installer never networks."""
    if os.geteuid() == 0:
        raise EnvironmentError("worker_environment_download_must_be_unprivileged")
    lock, _ = _lock()
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="jarvis-worker-download-") as temporary:
        requirements = Path(temporary) / "requirements.txt"
        requirements.write_text(_requirements(lock))
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "pip",
                "--disable-pip-version-check",
                "download",
                "--no-cache-dir",
                "--index-url",
                "https://pypi.org/simple",
                "--require-hashes",
                "--only-binary=:all:",
                "--dest",
                str(destination),
                "-r",
                str(requirements),
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PIP_CONFIG_FILE": "/dev/null"},
            check=True,
            timeout=300,
        )


def install_worker_environment(wheelhouse: Path) -> Path:
    """Separate owner-only offline operation. Never replace any existing env."""
    if os.geteuid() != 0:
        raise EnvironmentError("worker_environment_install_requires_root")
    lock, lock_digest = _lock()
    _trusted_path(LOCK_PATH, directory=False)
    _trusted_path(Path(__file__), directory=False)
    _trusted_path(ENVIRONMENT_PARENT, directory=True)
    _base_inventory(lock)
    if sys.version.split()[0] != lock["python"]["version"]:
        raise EnvironmentError("worker_environment_python_version_mismatch")
    root = environment_path()
    if root.exists() or root.is_symlink():
        raise EnvironmentError("worker_environment_already_exists")
    # Copy and hash bytes into a root-owned private directory before invoking
    # pip. Mutable download paths cannot race pip's subsequent wheel reads.
    staging = Path(tempfile.mkdtemp(prefix=".jarvis-worker-stage-", dir=ENVIRONMENT_PARENT))
    os.chmod(staging, 0o700)
    wheels = staging / "wheels"
    wheels.mkdir(mode=0o700)
    try:
        if set(path.name for path in wheelhouse.iterdir()) != {
            w["filename"] for w in lock["wheels"]
        }:
            raise EnvironmentError("worker_environment_wheel_inventory_invalid")
        for wheel in lock["wheels"]:
            source = wheelhouse / wheel["filename"]
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise EnvironmentError("worker_environment_wheel_invalid")
                target = wheels / wheel["filename"]
                with target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
            if _digest(target) != wheel["sha256"]:
                raise EnvironmentError("worker_environment_wheel_hash_mismatch")
        requirements = staging / "requirements.txt"
        requirements.write_text(_requirements(lock))
        # Create at its final versioned pathname so console shebangs cannot
        # point at staging. Mode0700 prevents worker access until sealed.
        root.mkdir(mode=0o700)
        venv.EnvBuilder(with_pip=False, symlinks=False).create(root)
        interpreter = root / "bin/python3.14"
        if interpreter.is_symlink() or _digest(interpreter) != lock["python"]["sha256"]:
            raise EnvironmentError("worker_environment_interpreter_invalid")
        (root / CACHE_DIRECTORY).mkdir(mode=0o555)
        pip_wheel = next(w for w in lock["wheels"] if w["name"] == "pip")
        # Bootstrap only the already copied, exact-hash-verified pip wheel.
        # ensurepip may execute an OS wheel outside the pinned stdlib tree.
        subprocess.run(
            [
                str(interpreter),
                *worker_python_arguments(interpreter),
                "-c",
                "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                "runpy.run_module('pip',run_name='__main__')",
                str(wheels / pip_wheel["filename"]),
                "install",
                "--no-index",
                "--no-cache-dir",
                "--no-compile",
                "--require-hashes",
                "--only-binary=:all:",
                "--find-links",
                str(wheels),
                "-r",
                str(requirements),
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PIP_CONFIG_FILE": "/dev/null"},
            check=True,
            timeout=300,
        )
        _versions(root, lock)
        _base_inventory(lock)
        for path in sorted(root.rglob("*"), reverse=True):
            if not path.is_symlink():
                path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
        manifest = {
            "version": 1,
            "lock_sha256": lock_digest,
            "files": _inventory(root, sealed=True),
        }
        with (root / MANIFEST).open("xb") as stream:
            stream.write(_encoded(manifest) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        (root / MANIFEST).chmod(0o444)
        root.chmod(0o555)
        for directory in (root, root.parent):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return verify_worker_environment()
    finally:
        # Failure preserves the new incomplete environment for reconciliation;
        # it has no valid manifest and can never pass execution preflight.
        shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify", action="store_true")
    action.add_argument("--download-wheelhouse", type=Path)
    action.add_argument("--install-wheelhouse", type=Path)
    args = parser.parse_args()
    if args.download_wheelhouse:
        download_wheels(args.download_wheelhouse)
    elif args.install_wheelhouse:
        print(install_worker_environment(args.install_wheelhouse))
    else:
        print(verify_worker_environment())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
