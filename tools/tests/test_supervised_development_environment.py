"""Offline worker-environment identity, isolation and root fixture checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from tools import supervised_development_environment as environment


def test_reviewed_lock_pins_actual_execution_and_regression_dependencies():
    lock, digest = environment._lock()
    packages = {wheel["name"]: wheel["version"] for wheel in lock["wheels"]}
    assert packages["openai"] == "2.54.0"
    assert lock["python"]["version"] == "3.14.4"
    assert {
        "cryptography",
        "pytest",
        "pytest-asyncio",
        "ruff",
        "bandit",
        "pip",
        "fastapi",
        "pydantic",
        "pydantic-settings",
        "uvicorn",
        "httpx",
        "websockets",
    } <= packages.keys()
    assert len(packages) == len(lock["wheels"])
    assert environment.environment_path() == Path("/opt/jarvis-supervised-worker-" + digest[:20])
    for wheel in lock["wheels"]:
        assert f"{wheel['name']}=={wheel['version']} --hash=sha256:{wheel['sha256']}\n" in (
            environment._requirements(lock)
        )


@pytest.mark.parametrize("mutation", ["sdk", "digest", "duplicate", "filename", "missing"])
def test_changed_or_incomplete_lock_denied(tmp_path, monkeypatch, mutation):
    lock, _ = environment._lock()
    if mutation == "sdk":
        next(w for w in lock["wheels"] if w["name"] == "openai")["version"] = "2.1.0"
    elif mutation == "digest":
        lock["wheels"][0]["sha256"] = "not-a-sha256"
    elif mutation == "duplicate":
        lock["wheels"].append(lock["wheels"][0])
    elif mutation == "filename":
        lock["wheels"][0]["filename"] = "../outside.whl"
    else:
        lock["wheels"] = [w for w in lock["wheels"] if w["name"] != "cryptography"]
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    monkeypatch.setattr(environment, "LOCK_PATH", path)
    with pytest.raises(environment.EnvironmentError, match="lock_invalid"):
        environment._lock()


def _fixture_inventory(tmp_path, monkeypatch):
    # Unit fixture substitutes ownership only; actual root/nonroot checks also
    # run through the opt-in root-owned Docker rehearsal below.
    monkeypatch.setattr(environment, "OWNER_UID", os.getuid())
    monkeypatch.setattr(environment, "_trusted_path", lambda path, **kw: path.stat())
    root = tmp_path / "environment"
    root.mkdir()
    file = root / "package.py"
    file.write_text("VALUE = 1\n")
    file.chmod(0o444)
    return root, file


def test_exact_inventory_detects_changed_missing_and_extra_files(tmp_path, monkeypatch):
    root, file = _fixture_inventory(tmp_path, monkeypatch)
    original = environment._inventory(root, sealed=True)
    file.chmod(0o644)
    file.write_text("VALUE = 2\n")
    file.chmod(0o444)
    assert environment._inventory(root, sealed=True) != original
    file.unlink()
    assert environment._inventory(root, sealed=True) != original
    replacement = root / "unexpected.py"
    replacement.write_text("VALUE = 1\n")
    replacement.chmod(0o444)
    assert environment._inventory(root, sealed=True) != original


@pytest.mark.parametrize("attack", ["writable", "symlink", "hardlink", "fifo"])
def test_inventory_rejects_mutable_escaped_and_nonregular_objects(tmp_path, monkeypatch, attack):
    root, file = _fixture_inventory(tmp_path, monkeypatch)
    if attack == "writable":
        file.chmod(0o644)
    elif attack == "symlink":
        (root / "outside").symlink_to(tmp_path)
    elif attack == "hardlink":
        os.link(file, root / "other")
    else:
        os.mkfifo(root / "pipe", mode=0o400)
    with pytest.raises(environment.EnvironmentError):
        environment._inventory(root, sealed=True)


def test_trusted_path_rejects_actual_non_root_owned_source(tmp_path):
    if os.getuid() == 0:
        pytest.skip("nonroot ownership assertion; root fixture below exercises both identities")
    file = tmp_path / "module.py"
    file.write_text("VALUE = 1\n")
    with pytest.raises(environment.EnvironmentError, match="path_untrusted"):
        environment._trusted_path(file)


@pytest.mark.parametrize("hook", ["injected.pth", "sitecustomize.py", "usercustomize.py"])
def test_distribution_import_hooks_rejected(tmp_path, hook):
    site = tmp_path / "lib/python3.14/site-packages"
    metadata = site / "example-1.0.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Name: Example\nVersion: 1.0\n\nName: body text is not metadata\n")
    lock = {"wheels": [{"name": "example", "version": "1.0"}]}
    environment._versions(tmp_path, lock)
    (site / hook).write_text("import os\n")
    with pytest.raises(environment.EnvironmentError, match="import_hook_denied"):
        environment._versions(tmp_path, lock)


def test_distribution_exact_versions_and_inventory_required(tmp_path):
    site = tmp_path / "lib/python3.14/site-packages"
    metadata = site / "example-1.0.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Name: Example\nVersion: 1.0\n\n")
    with pytest.raises(environment.EnvironmentError, match="dependencies_mismatch"):
        environment._versions(tmp_path, {"wheels": [{"name": "example", "version": "2.0"}]})


def test_os_cache_generation_does_not_change_pinned_source_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "_trusted_path", lambda path, **kw: path.stat())
    binary = tmp_path / "python3.14"
    binary.write_bytes(b"fixture interpreter, not executable")
    stdlib = tmp_path / "python3.14-lib"
    stdlib.mkdir()
    source = stdlib / "source.py"
    source.write_text("value = 1\n")
    expected = {"source.py": environment._digest(source)}
    lock = {
        "python": {
            "executable": str(binary),
            "sha256": environment._digest(binary),
            "stdlib": str(stdlib),
            "stdlib_sha256": hashlib.sha256(environment._encoded(expected)).hexdigest(),
        }
    }
    assert environment._base_inventory(lock) == expected
    cache = stdlib / "__pycache__"
    cache.mkdir()
    (cache / "source.cpython-314.pyc").write_bytes(b"generated OS cache")
    assert environment._base_inventory(lock) == expected
    source.write_text("value = 2\n")
    with pytest.raises(environment.EnvironmentError, match="stdlib_drift"):
        environment._base_inventory(lock)


def test_unpinned_earlier_stdlib_archive_denied(tmp_path, monkeypatch):
    (tmp_path / "python314.zip").write_bytes(b"unreviewed import source")
    with pytest.raises(environment.EnvironmentError, match="stdlib_archive_denied"):
        environment._base_inventory({"python": {"stdlib": str(tmp_path / "python3.14")}})


def test_root_network_download_and_unprivileged_install_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(environment.os, "geteuid", lambda: 0)
    with pytest.raises(environment.EnvironmentError, match="download_must_be_unprivileged"):
        environment.download_wheels(tmp_path / "wheels")
    monkeypatch.setattr(environment.os, "geteuid", lambda: 600)
    with pytest.raises(environment.EnvironmentError, match="install_requires_root"):
        environment.install_worker_environment(tmp_path / "wheels")


def test_download_is_hash_locked_public_binary_only_and_has_no_inherited_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(environment.os, "geteuid", lambda: 600)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-real-secret")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://invalid.example/fixture")
    calls = []
    monkeypatch.setattr(
        environment.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    destination = tmp_path / "wheels"
    environment.download_wheels(destination)
    assert len(calls) == 1
    argv, options = calls[0]
    assert "--require-hashes" in argv and "--only-binary=:all:" in argv
    assert argv[argv.index("--index-url") + 1] == "https://pypi.org/simple"
    assert options["env"] == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "PIP_CONFIG_FILE": "/dev/null",
    }
    assert options["check"] and options["timeout"] == 300
    with pytest.raises(FileExistsError):
        environment.download_wheels(destination)


@pytest.mark.skipif(
    os.environ.get("JARVIS_PROCESS_DOCKER_TEST") != "1",
    reason="explicit disposable Docker/root-identity rehearsal opt-in required",
)
def test_root_offline_provision_sealed_environment_and_unprivileged_verification():
    root = Path(__file__).resolve().parents[2]
    wheelhouse = Path(os.environ["JARVIS_WORKER_TEST_WHEELHOUSE"]).resolve(strict=True)
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--mount",
        "type=bind,src=/usr,dst=/usr,readonly",
        "--mount",
        "type=bind,src=/etc/python3.14,dst=/etc/python3.14,readonly",
        "--mount",
        f"type=bind,src={root},dst=/source,readonly",
        "--mount",
        f"type=bind,src={wheelhouse},dst=/wheels,readonly",
        "python:3.12-slim",
        "/usr/bin/python3",
        "-I",
        "-B",
        "/source/tools/tests/fixtures/worker_environment_rehearsal.py",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-5000:]
    evidence = json.loads(result.stdout)
    assert evidence["worker_uid"] == 600
    assert evidence["sdk"] == "2.54.0"
    assert evidence["sealed"] is True
    assert evidence["verified_checks"] == [
        "offline_install",
        "unprivileged_imports",
        "worker_write_denied",
        "existing_env_preserved",
        "modified_wheel_denied",
        "fifo_wheel_denied",
        "extra_wheel_denied",
        "root_mode_denied",
        "changed_file_denied",
        "missing_file_denied",
        "extra_file_denied",
        "manifest_drift_denied",
    ]
