"""Disposable Docker-only root environment installation and tamper rehearsal."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    assert os.geteuid() == 0
    trusted = Path("/opt/reviewed/tools")
    trusted.mkdir(parents=True)
    for name in (
        "supervised_development_environment.py",
        "supervised_development_environment.lock.json",
    ):
        shutil.copyfile(Path("/source/tools") / name, trusted / name)
    module = trusted / "supervised_development_environment.py"
    spec = importlib.util.spec_from_file_location("worker_environment", module)
    env = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env)
    lock, _ = env._lock()
    root = env.environment_path()
    checks = []

    def denies(function, category):
        try:
            function()
        except env.EnvironmentError as exc:
            assert category in str(exc), str(exc)
        else:
            raise AssertionError("expected denial: " + category)

    # Malformed wheel input fails before any environment/interpreter exists.
    bad = Path("/bad-wheels")
    bad.mkdir()
    for wheel in lock["wheels"]:
        (bad / wheel["filename"]).symlink_to(Path("/wheels") / wheel["filename"])
    first = bad / lock["wheels"][0]["filename"]
    first.unlink()
    first.write_bytes(b"modified wheel")
    denies(lambda: env.install_worker_environment(bad), "wheel_hash_mismatch")
    assert not root.exists()
    first.unlink()
    os.mkfifo(first)
    denies(lambda: env.install_worker_environment(bad), "wheel_invalid")
    assert not root.exists()
    first.unlink()
    first.symlink_to(Path("/wheels") / first.name)
    (bad / "unexpected.whl").write_bytes(b"extra")
    denies(lambda: env.install_worker_environment(bad), "wheel_inventory_invalid")
    assert not root.exists()

    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", str(module), "--install-wheelhouse", "/wheels"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-5000:]
    interpreter = env.verify_worker_environment()
    assert interpreter == root / "bin/python3.14"
    checks.append("offline_install")

    def drop_identity():
        os.setgroups([])
        os.setgid(600)
        os.setuid(600)

    code = (
        "import importlib.util,json,os,sys;"
        "import openai,cryptography,pytest,pytest_asyncio,ruff,bandit,fastapi,"
        "pydantic,pydantic_settings,httpx,websockets,uvicorn,pypdf;"
        "s=importlib.util.spec_from_file_location('env',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "assert str(m.verify_worker_environment())==sys.executable;"
        "assert sys.dont_write_bytecode and sys.pycache_prefix==str(m.environment_path()/m.CACHE_DIRECTORY);"
        "print(json.dumps({'worker_uid':os.getuid(),'sdk':openai.__version__}))"
    )
    result = subprocess.run(
        [str(interpreter), *env.worker_python_arguments(interpreter), "-c", code, str(module)],
        capture_output=True,
        text=True,
        timeout=60,
        preexec_fn=drop_identity,
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )
    evidence = json.loads(result.stdout)
    checks.append("unprivileged_imports")
    result = subprocess.run(
        [
            str(interpreter),
            *env.worker_python_arguments(interpreter),
            "-c",
            "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('denied')",
            str(root / "untrusted.py"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        preexec_fn=drop_identity,
        check=False,
    )
    assert result.returncode != 0 and "PermissionError" in result.stderr
    assert not (root / "untrusted.py").exists()
    checks.append("worker_write_denied")
    manifest = (root / env.MANIFEST).read_bytes()
    denies(lambda: env.install_worker_environment(Path("/wheels")), "already_exists")
    assert (root / env.MANIFEST).read_bytes() == manifest
    checks += [
        "existing_env_preserved",
        "modified_wheel_denied",
        "fifo_wheel_denied",
        "extra_wheel_denied",
    ]
    root.chmod(0o755)
    denies(env.verify_worker_environment, "root_mutable")
    root.chmod(0o555)
    checks.append("root_mode_denied")
    file = root / "lib/python3.14/site-packages/openai/__init__.py"
    original = file.read_bytes()
    file.chmod(0o644)
    file.write_bytes(original + b"\n# tampered\n")
    file.chmod(0o444)
    denies(env.verify_worker_environment, "inventory_mismatch")
    checks.append("changed_file_denied")
    file.unlink()
    denies(env.verify_worker_environment, "inventory_mismatch")
    checks.append("missing_file_denied")
    file.write_bytes(original)
    file.chmod(0o444)
    extra = root / "injected.py"
    extra.write_text("VALUE = 1\n")
    extra.chmod(0o444)
    denies(env.verify_worker_environment, "inventory_mismatch")
    checks.append("extra_file_denied")
    extra.unlink()
    changed = json.loads(manifest)
    changed["lock_sha256"] = "0" * 64
    (root / env.MANIFEST).chmod(0o644)
    (root / env.MANIFEST).write_text(json.dumps(changed))
    (root / env.MANIFEST).chmod(0o444)
    denies(env.verify_worker_environment, "manifest_invalid")
    checks.append("manifest_drift_denied")
    (root / env.MANIFEST).chmod(0o644)
    (root / env.MANIFEST).write_bytes(manifest)
    (root / env.MANIFEST).chmod(0o444)
    assert env.verify_worker_environment() == interpreter
    evidence.update(sealed=True, verified_checks=checks)
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
