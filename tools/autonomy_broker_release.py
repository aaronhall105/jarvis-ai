"""Fixed Core-only prebuilt release mode of deploy_unified_core.sh.

Uses the established Compose project, files, persistent mounts and Core service.
Never builds online, touches speaker-verifier, installs dependencies or rewinds
user data. Caller requests are already recorded in protected broker storage.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

from tools.autonomy_broker import DIGEST, Denied, Request, canonical
from tools.autonomy_broker_data import compatible_state, schema_state
from tools.autonomy_broker_host import Docker, bounded_process, validate_core_container
from tools.autonomy_broker_store import Records

LIVE_ROOT = Path("/home/aaron/.local/share/jarvis-runtime")


def fixed_compose(image: str, sha: str) -> bytes:
    from tools.autonomy_broker import source_sha

    source_sha(sha)
    if not DIGEST.fullmatch(image):
        raise Denied("invalid_artifact")
    return canonical(
        {
            "services": {
                "jarvis-core": {
                    "image": image,
                    "environment": {
                        "JARVIS_SOURCE_SHA": sha,
                        "JARVIS_SPECULATIVE_READS_ENABLED": "false",
                    },
                    "labels": {"org.opencontainers.image.revision": sha},
                }
            }
        }
    )


def compose_argv(override: Path) -> list[str]:
    return [
        "/usr/bin/docker",
        "compose",
        "--project-name",
        "jarvis",
        "--project-directory",
        str(LIVE_ROOT),
        "--file",
        str(LIVE_ROOT / "docker-compose.yml"),
        "--file",
        str(LIVE_ROOT / "docker-compose.override.yml"),
        "--file",
        str(override),
        "up",
        "-d",
        "--no-build",
        "--no-deps",
        "--pull",
        "never",
        "jarvis-core",
    ]


def unchanged_runtime(policy: dict) -> None:
    # Exact fixed operator files only. No request-supplied root source/destination.
    expected_paths = {
        str(LIVE_ROOT / name)
        for name in ("docker-compose.yml", "docker-compose.override.yml", ".env")
    }
    if set(policy["runtime_hashes"]) != expected_paths:
        raise Denied("unsupported_runtime_policy")
    for name, digest in policy["runtime_hashes"].items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise Denied("runtime_configuration_changed")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise Denied("runtime_configuration_changed")


class ComposeRelease:
    def __init__(
        self,
        policy: dict,
        records: Records,
        docker: Docker,
        health: Callable[[str], bool],
        run=bounded_process,
        schemas=schema_state,
    ):
        self.policy, self.records, self.docker, self.health = policy, records, docker, health
        self.run, self.schemas = run, schemas

    def install(self, request: Request, fence, admission: dict) -> dict:
        # Admission is protected helper input written only after remote checks,
        # exact source/artifact validation and executable compatibility checks.
        if admission.get("compatibility") != "identical-persistence-runtime-v1":
            raise Denied("unknown_persistence_compatibility")
        if self.records.read("releases", request.request_id) is not None:
            raise Denied("reconciliation_required")
        fence()
        unchanged_runtime(self.policy)
        old = self.docker.inspect("jarvis-core")
        validate_core_container(old, self.policy)
        previous_sha = old["Config"]["Labels"]["org.opencontainers.image.revision"]
        if previous_sha != admission["previous_sha"] or old["Image"] != admission["previous_image"]:
            raise Denied("production_changed_after_admission")
        if not self.health(previous_sha):
            raise Denied("baseline_core_not_ready")
        schemas = self.schemas(LIVE_ROOT / "data")
        record = {
            "request_id": request.request_id,
            "goal_id": request.body["goal_id"],
            "previous_sha": previous_sha,
            "previous_image": old["Image"],
            "target_sha": request.body["source_sha"],
            "target_image": request.body["parameters"]["artifact"],
            "component": "core",
            "schemas": schemas,
            "compatibility": admission["compatibility"],
            "health_baseline": "healthy",
            "stage": "prepared",
        }
        self.records.write("releases", request.request_id, record, new=True)
        try:
            fence()
            record["stage"] = "installing"
            self.records.write("releases", request.request_id, record)
            self.switch(record["target_image"], record["target_sha"], request.request_id, fence)
            self.wait_health(record["target_sha"], fence)
            live = self.docker.inspect("jarvis-core")
            validate_core_container(live, self.policy)
            if live["Image"] != record["target_image"]:
                raise Denied("deployed_image_mismatch")
            compatible_state(schemas, self.schemas(LIVE_ROOT / "data"))
            record["stage"] = "installed"
            self.records.write("releases", request.request_id, record)
            return {
                "outcome": "installed",
                "source_sha": record["target_sha"],
                "data_reverted": False,
            }
        except BaseException:
            # Only deterministic compensation, no new candidate or new phase.
            # Never restore a data backup. If schema changed, even code rollback
            # is unsafe: stop and leave explicit reconciliation evidence.
            try:
                self.restore(record, lambda: None)
            except Exception:
                record["stage"] = "reconciliation_required"
                self.records.write("releases", request.request_id, record)
            raise

    def switch(self, image: str, sha: str, request_id: str, fence) -> None:
        unchanged_runtime(self.policy)
        override = self.records.base / ("compose-" + request_id + ".json")
        # Private fixed root scratch, never a caller path. Existing unknown file
        # is refused; compensation may replace only the same validated content.
        if override.exists() or override.is_symlink():
            if (
                override.is_symlink()
                or override.stat().st_uid != self.records.owner
                or override.stat().st_nlink != 1
            ):
                raise Denied("unprotected_compose_override")
        import os

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(override, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(fixed_compose(image, sha))
            handle.flush()
            os.fsync(handle.fileno())
        result = self.run(compose_argv(override), fence, timeout=180)
        if result["exit_code"] != 0:
            raise Denied("compose_transition_failed")

    def wait_health(self, sha: str, fence) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            fence()
            if self.health(sha):
                return
            time.sleep(0.5)
        raise Denied("release_health_failed")

    def restore(self, record: dict, fence) -> dict:
        if record["stage"] not in {"installing", "installed"}:
            raise Denied("rollback_state_uncertain")
        fence()
        unchanged_runtime(self.policy)
        current = self.docker.inspect("jarvis-core")
        validate_core_container(current, self.policy)
        if current["Image"] not in {record["previous_image"], record["target_image"]}:
            raise Denied("rollback_target_changed")
        if current["Config"]["Labels"].get("org.opencontainers.image.revision") not in {
            record["previous_sha"],
            record["target_sha"],
        }:
            raise Denied("rollback_target_changed")
        compatible_state(record["schemas"], self.schemas(LIVE_ROOT / "data"))
        record["stage"] = "rolling_back"
        self.records.write("releases", record["request_id"], record)
        self.switch(record["previous_image"], record["previous_sha"], record["request_id"], fence)
        self.wait_health(record["previous_sha"], fence)
        compatible_state(record["schemas"], self.schemas(LIVE_ROOT / "data"))
        record["stage"] = "rolled_back"
        self.records.write("releases", record["request_id"], record)
        return {
            "outcome": "rolled_back",
            "source_sha": record["previous_sha"],
            "data_reverted": False,
        }
