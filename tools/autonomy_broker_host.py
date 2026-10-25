"""Fixed host adapters. No caller argv, destination, environment or image selection.

Only the installed root service imports this module in production. Tests inject
transport/process fakes; IPC cannot inject these dependencies.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from tools.autonomy_broker import (
    DIGEST,
    REPOSITORY,
    Denied,
    Request,
    canonical,
    relative_source,
    source_sha,
)
from tools.autonomy_broker_snapshot import OMITTED_ROOT_ENTRIES

BROKER_BASE = Path("/var/lib/jarvis-autonomy-broker")
CANONICAL = Path("/home/aaron/jarvis-unified-production-worktree")
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "DOCKER_HOST": "unix:///run/docker.sock",
    "DOCKER_CONFIG": "/var/lib/jarvis-autonomy-broker/docker-client",
    "DOCKER_BUILDKIT": "0",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise Denied("unexpected_github_redirect")


class GitHub:
    """Read-only public-repository evidence. No host credential or OAuth token."""

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.cache: dict[str, Any] = {}

    def get(self, path: str, *, immutable: bool = False) -> Any:
        if immutable and path in self.cache:
            return self.cache[path]
        request = urllib.request.Request(
            "https://api.github.com/repos/" + REPOSITORY + "/" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Jarvis-Autonomy-Broker/1",
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise Denied("github_response_limit")
            value = json.loads(raw)
        except (OSError, ValueError, urllib.error.URLError):
            raise Denied("github_evidence_unavailable") from None
        if immutable:
            if len(self.cache) >= 32:
                self.cache.clear()
            self.cache[path] = value
        return value

    def tree(self, sha: str) -> dict[str, str]:
        source_sha(sha)
        value = self.get(f"git/trees/{sha}?recursive=1", immutable=True)
        if (
            not isinstance(value, dict)
            or value.get("truncated") is not False
            or not isinstance(value.get("tree"), list)
        ):
            raise Denied("incomplete_github_tree")
        result = {}
        for entry in value["tree"]:
            if not isinstance(entry, dict):
                raise Denied("invalid_github_tree")
            if entry["type"] == "tree":
                continue
            if entry["type"] != "blob" or entry["mode"] not in {"100644", "100755"}:
                raise Denied("unsupported_git_entry")
            if entry["path"] in OMITTED_ROOT_ENTRIES:
                continue
            relative_source(entry["path"])
            if entry["path"] in result:
                raise Denied("ambiguous_git_tree")
            result[entry["path"]] = source_sha(entry["sha"])
        return result


def git_blob(data: bytes) -> str:
    # Git object identity, not a password or a new security-signature algorithm.
    return hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
    ).hexdigest()


def verify_tree(files: dict[str, bytes], tree: dict[str, str]) -> None:
    if set(files) != set(tree) or any(git_blob(data) != tree[path] for path, data in files.items()):
        raise Denied("workspace_does_not_match_exact_commit")


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/run/docker.sock")


class Docker:
    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        connection = UnixHTTP("localhost", timeout=15)
        try:
            connection.request(
                method,
                "/v1.45" + path,
                body=canonical(body) if body is not None else None,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(8 * 1024 * 1024 + 1)
            if (
                response.status == 404
                and method == "DELETE"
                and re.fullmatch(
                    r"/containers/jarvis-autonomy-[0-9a-f]{32}\?force=true&v=true", path
                )
            ):
                return None  # Already absent is successful fixed-container cleanup.
            if response.status >= 300 or len(raw) > 8 * 1024 * 1024:
                raise Denied("docker_operation_failed")
            return json.loads(raw) if raw else None
        except (OSError, ValueError, http.client.HTTPException):
            raise Denied("docker_operation_uncertain") from None
        finally:
            connection.close()

    def inspect(self, container: str) -> dict[str, Any]:
        if container != "jarvis-core" and not (
            len(container) == 64 and all(c in "0123456789abcdef" for c in container)
        ):
            raise Denied("invalid_container_identity")
        return self.call("GET", f"/containers/{container}/json")

    def cancel_sandbox(self, request_id: str) -> None:
        import uuid

        name = "jarvis-autonomy-" + uuid.UUID(request_id).hex
        self.call("DELETE", f"/containers/{name}?force=true&v=true")


def bounded_process(
    argv: list[str],
    fence: Callable[[], None],
    *,
    timeout: float = 610,
    stdin: bytes | None = None,
    cancel: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Private adapter primitive, never an IPC operation. Drain bounded diagnostics.

    Output is deliberately not returned: tests can print arbitrary source text.
    Exit status and a digest/byte count are enough for protected release evidence.
    """
    import tempfile

    fence()
    with tempfile.TemporaryFile() as input_file:
        if stdin:
            input_file.write(stdin)
            input_file.seek(0)
        process = subprocess.Popen(
            argv,
            stdin=input_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=SAFE_ENV,
            start_new_session=True,
        )
        digest, count = hashlib.sha256(), 0
        deadline = time.monotonic() + timeout
        selector = selectors.DefaultSelector()
        if process.stdout is None:
            process.kill()
            process.wait(timeout=5)
            raise Denied("execution_pipe_unavailable")
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                fence()
                if time.monotonic() >= deadline:
                    raise Denied("execution_timeout")
                for key, _ in selector.select(0.2):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > 1024 * 1024:
                        raise Denied("execution_output_limit")
                    digest.update(chunk)
            while process.poll() is None:
                fence()
                if time.monotonic() >= deadline:
                    raise Denied("execution_timeout")
                time.sleep(0.1)
            fence()
            return {
                "exit_code": process.returncode,
                "output_bytes": count,
                "output_sha256": digest.hexdigest(),
                "outcome": "passed" if process.returncode == 0 else "failed",
            }
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            if cancel:
                cancel()
            raise
        finally:
            selector.close()
            process.stdout.close()


def core_context(files: dict[str, bytes], policy: dict[str, Any], sha: str) -> bytes:
    source_sha(sha)
    base = policy["core_base_image"]
    if not DIGEST.fullmatch(base):
        raise Denied("untrusted_base_image")
    for path, expected in policy["dependency_hashes"].items():
        if hashlib.sha256(files.get(path, b"")).hexdigest() != expected:
            raise Denied("dependency_change_requires_offline_image_provisioning")
    dockerfile = (
        f"FROM {base}\nRUN rm -rf /app/app\nCOPY app /app/app\n"
        f"ENV JARVIS_SOURCE_SHA={sha}\nLABEL org.opencontainers.image.revision={sha}\n"
    ).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        contents = {"Dockerfile": dockerfile}
        contents.update(
            {
                path.removeprefix("bridge/"): data
                for path, data in files.items()
                if path.startswith("bridge/app/")
            }
        )
        if len(contents) < 2:
            raise Denied("missing_core_source")
        for path, data in sorted(contents.items()):
            relative_source(path)
            entry = tarfile.TarInfo(path)
            entry.size, entry.mode, entry.uid, entry.gid, entry.mtime = len(data), 0o444, 0, 0, 0
            archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def validate_core_container(value: dict[str, Any], policy: dict[str, Any]) -> None:
    host = value["HostConfig"]
    if (
        value["Name"] != "/jarvis-core"
        or host.get("Privileged")
        or host.get("CapAdd")
        or host.get("Devices")
        or host.get("DeviceRequests")
        or host.get("PidMode")
        or host.get("IpcMode") not in {"", "private"}
        or host.get("VolumesFrom")
        or host.get("NetworkMode") != policy["core_network"]
    ):
        raise Denied("unsupported_core_runtime")
    mounts = {item["Destination"]: item["Source"] for item in value["Mounts"]}
    if (
        len(mounts) != len(value["Mounts"])
        or mounts != policy["core_mounts"]
        or any(item["Type"] != "bind" for item in value["Mounts"])
    ):
        raise Denied("unexpected_core_mounts")
    if host.get("PortBindings") != policy["core_ports"]:
        raise Denied("unexpected_core_ports")


class CoreRelease:
    """Image replacement only. No repository deploy scripts or arbitrary services.

    Old container is retained (including its daemon-held configuration) as rollback
    target. No environment/credential values are written to broker state/results.
    Database schema/data restoration is NOT attempted; uncertain migrations block.
    """

    def __init__(
        self,
        docker: Docker,
        policy: dict[str, Any],
        record: Callable[[dict[str, Any]], None],
        health: Callable[[str], bool],
        data_guard: Callable[[Request], bool],
    ):
        self.docker, self.policy, self.record, self.health = docker, policy, record, health
        # Mandatory trusted admission adapter, not a caller-supplied boolean.
        # Production wiring must prove data/schema rollback compatibility before
        # any container is stopped. Container restoration is NOT data rollback.
        self.data_guard = data_guard

    def install(self, request: Request, fence: Callable[[], None]) -> dict[str, Any]:
        import uuid

        fence()
        if self.data_guard(request) is not True:
            raise Denied("data_rollback_not_proven")
        fence()
        old = self.docker.inspect("jarvis-core")
        validate_core_container(old, self.policy)
        old_id = old["Id"]
        if not (len(old_id) == 64 and all(c in "0123456789abcdef" for c in old_id)):
            raise Denied("invalid_container_identity")
        old_sha = old["Config"].get("Labels", {}).get("org.opencontainers.image.revision")
        source_sha(old_sha)
        if not self.health(old_sha):
            raise Denied("baseline_core_not_ready")
        artifact = request.body["parameters"]["artifact"]
        image = self.docker.call("GET", f"/images/{artifact}/json")
        if (
            image["Id"] != artifact
            or image["Config"].get("Labels", {}).get("org.opencontainers.image.revision")
            != request.body["source_sha"]
        ):
            raise Denied("artifact_identity_changed")
        previous = {
            "old_id": old_id,
            "old_sha": old_sha,
            "old_image": old["Image"],
            "request_id": request.request_id,
            "stage": "prepared",
        }
        self.record(previous)  # Durable BEFORE changing any container.
        backup = "jarvis-autonomy-previous-" + uuid.UUID(request.request_id).hex
        new_id = None
        try:
            fence()
            self.docker.call("POST", f"/containers/{old_id}/stop?t=10")
            fence()
            self.docker.call("POST", f"/containers/{old_id}/rename?name={backup}")
            fence()
            # Only established fixed runtime fields are retained. Image defaults
            # cannot supply mounts, devices, host namespaces or extra privileges.
            config = {
                key: old["Config"].get(key)
                for key in (
                    "Env",
                    "Cmd",
                    "Entrypoint",
                    "WorkingDir",
                    "User",
                    "ExposedPorts",
                    "Healthcheck",
                )
            }
            config.update(
                {
                    "Image": artifact,
                    "Labels": {
                        "org.opencontainers.image.revision": request.body["source_sha"],
                        "io.jarvis.autonomy.request": request.request_id,
                    },
                    "HostConfig": {
                        "Binds": [
                            f"{src}:{dst}:rw" for dst, src in self.policy["core_mounts"].items()
                        ],
                        "NetworkMode": self.policy["core_network"],
                        "PortBindings": self.policy["core_ports"],
                        "RestartPolicy": {"Name": "unless-stopped"},
                        "Privileged": False,
                    },
                }
            )
            config["Env"] = [
                item
                for item in (config["Env"] or [])
                if not item.startswith(("JARVIS_SOURCE_SHA=", "JARVIS_SPECULATIVE_READS_ENABLED="))
            ]
            config["Env"] += [
                "JARVIS_SOURCE_SHA=" + request.body["source_sha"],
                "JARVIS_SPECULATIVE_READS_ENABLED=false",
            ]
            created = self.docker.call("POST", "/containers/create?name=jarvis-core", config)
            new_id = created["Id"]
            if not (
                isinstance(new_id, str)
                and len(new_id) == 64
                and all(c in "0123456789abcdef" for c in new_id)
            ):
                raise Denied("invalid_container_identity")
            previous.update({"new_id": new_id, "stage": "created"})
            self.record(previous)
            fence()
            self.docker.call("POST", f"/containers/{new_id}/start")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                fence()
                if self.health(request.body["source_sha"]):
                    previous["stage"] = "installed"
                    self.record(previous)
                    return {
                        "outcome": "installed",
                        "source_sha": request.body["source_sha"],
                        "artifact": artifact,
                        "rollback_artifact": old["Image"],
                    }
                time.sleep(0.5)
            raise Denied("candidate_core_unhealthy")
        except BaseException:
            # Bounded compensation only: never continue deployment after STOP.
            # Restore the exact pre-operation container so normal Jarvis is not
            # intentionally left stopped. No new candidate/phase is authorized.
            try:
                self.restore(previous, new_id)
            except Exception:
                self.record({**previous, "stage": "reconciliation_required"})
            raise

    def restore(self, previous: dict[str, Any], new_id: str | None) -> None:
        old_id = previous["old_id"]
        old = self.docker.inspect(old_id)
        if old["Image"] != previous["old_image"]:
            raise Denied("rollback_identity_changed")
        if new_id:
            current = self.docker.inspect(new_id)
            if (
                current["Config"].get("Labels", {}).get("io.jarvis.autonomy.request")
                != previous["request_id"]
            ):
                raise Denied("rollback_target_changed")
            self.docker.call("DELETE", f"/containers/{new_id}?force=true")
        # If the previous stop never completed, fixed rename/start may return
        # an error; that is reconciliation, not fabricated recovery.
        if old["Name"] != "/jarvis-core":
            self.docker.call("POST", f"/containers/{old_id}/rename?name=jarvis-core")
        if not old["State"]["Running"]:
            self.docker.call("POST", f"/containers/{old_id}/start")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.health(previous["old_sha"]):
                self.record({**previous, "stage": "container_restored"})
                return
            time.sleep(0.5)
        raise Denied("rollback_health_unconfirmed")
