"""Installed-only production wiring; no CLI/environment path overrides."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import struct
import sys
import time
from pathlib import Path

from tools import autonomy_guardian as guardian_module
from tools.autonomy_broker import (
    Broker,
    Denied,
    Ledger,
    Request,
    active_generation,
    canonical,
    sandbox_argv,
    decode_request,
)
from tools.autonomy_broker_data import compatible_code
from tools.autonomy_broker_evidence import merged_release
from tools.autonomy_broker_host import (
    CANONICAL,
    SAFE_ENV,
    Docker,
    GitHub,
    bounded_process,
    core_context,
    verify_tree,
)
from tools.autonomy_broker_ipc import handle, receive
from tools.autonomy_broker_operator import OperatorDispatcher
from tools.autonomy_broker_release import ComposeRelease
from tools.autonomy_broker_snapshot import materialize_snapshot, read_source_tree
from tools.autonomy_broker_store import Records

LIB = Path("/usr/local/libexec/jarvis-autonomy-broker")
BASE = Path("/var/lib/jarvis-autonomy-broker")
JOBS = Path("/var/lib/jarvis-autonomy-jobs")
POLICY = Path("/etc/jarvis-autonomy/broker.json")


def installed_policy() -> dict:
    guardian_module.trusted(POLICY)
    guardian_module.trusted_parents(POLICY)
    manifest = BASE / "installed.json"
    guardian_module.trusted(manifest)
    value = json.loads(manifest.read_text())
    for name, digest in value["files"].items():
        target = Path(name)
        guardian_module.trusted(target)
        guardian_module.trusted_parents(target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise Denied("broker_installation_changed")
    if str(POLICY) not in value["files"]:
        raise Denied("policy_not_pinned")
    policy = json.loads(POLICY.read_text())
    if policy["autonomy_uid"] != guardian_module.identity() or policy["version"] != 1:
        raise Denied("broker_identity_changed")
    return policy


def guardian_status() -> dict:
    return guardian_module.Guardian().status()


def emergency_stop() -> None:
    # Root worker can request STOP only; never RESUME. Separate operator service
    # remains reachable while the worker's own transaction is blocked/uncertain.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect("/run/jarvis-autonomy-broker/operator.sock")
        raw = canonical({"emergency_stop": True})
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        response = json.loads(receive(connection))
        if response.get("ok") is not True or response.get("result", {}).get("state") != "STOPPED":
            raise Denied("emergency_stop_unconfirmed")


def health(sha: str, *, quiet: bool = False) -> bool:
    import urllib.request
    from tools.autonomy_broker_host import NoRedirect

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open("http://127.0.0.1:8000/health", timeout=3) as response:
            value = json.loads(response.read(262144))
        with opener.open("http://127.0.0.1:8000/api/realtime/voice/status", timeout=3) as response:
            voice = json.loads(response.read(262144))
        return (
            value.get("status") == "healthy"
            and value.get("source_commit") == sha
            and voice.get("speculative_reads_enabled") is False
            and (not quiet or voice.get("active_sessions") == 0)
        )
    except Exception:
        return False


def canonical_clean(policy: dict, sha: str) -> None:
    # These Git operations cannot run source hooks, filters or fsmonitor. Local
    # Git metadata/config inputs are pinned by the reviewed installer; no include
    # or filter/credential command is accepted in that config.
    if CANONICAL.is_symlink() or CANONICAL.stat().st_uid != policy["operator_uid"]:
        raise Denied("canonical_worktree_owner_changed")
    for name, digest in policy["git_metadata_hashes"].items():
        path = Path(name)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise Denied("canonical_git_configuration_changed")
    prefix = [
        "/usr/bin/git",
        "--no-optional-locks",
        # SAFE_ENV intentionally omits SUDO_UID and global Git configuration.
        # Root may inspect only this fixed, operator-owned repository. Never
        # persist a global exception or trust caller-selected repositories.
        "-c",
        "safe.directory=" + str(CANONICAL),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        "-C",
        str(CANONICAL),
    ]
    for args, expected in [
        (["rev-parse", "HEAD"], sha),
        (["branch", "--show-current"], "jarvis/unified-production"),
        (["status", "--porcelain", "--untracked-files=normal"], ""),
    ]:
        result = subprocess.run(
            prefix + args,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise Denied("canonical_git_query_failed")
        if len(result.stdout) > 65536 or result.stdout.decode().strip() != expected:
            raise Denied("canonical_worktree_not_clean_current")


class Runtime:
    def __init__(self, policy: dict, records: Records, ledger: Ledger, github=None, docker=None):
        self.policy, self.records, self.ledger = policy, records, ledger
        self.github, self.docker = github or GitHub(), docker or Docker()
        self.prepared: dict = {}

    def job(self, request: Request) -> dict:
        job = self.records.read("jobs", request.body["goal_id"])
        if (
            not job
            or job["branch"] != request.body["branch"]
            or job["actor"] != self.policy["autonomy_uid"]
        ):
            raise Denied("job_not_authorized")
        return job

    def evidence(self, request: Request) -> dict:
        body, operation = request.body, request.body["operation"]
        self.prepared = {}
        if operation == "register_job":
            if (
                self.records.read("jobs", body["goal_id"]) is not None
                or (JOBS / body["goal_id"]).exists()
            ):
                raise Denied("job_already_exists")
            ref = self.github.get("git/ref/heads/jarvis/unified-production")
            if ref["object"]["sha"] != body["source_sha"]:
                raise Denied("job_base_not_canonical")
            return {"workspace_verified": True}
        self.job(request)
        if operation in {"inspect_release", "rollback_core_release"}:
            return {"workspace_verified": True}
        files, digest = read_source_tree(JOBS / body["goal_id"], self.policy["autonomy_uid"])
        self.prepared = {"files": files, "snapshot": digest}
        if operation == "run_verification":
            # A verification snapshot may be an uncommitted change. Its digest is
            # reported separately; it is never certified as a committed artifact.
            self.github.tree(body["source_sha"])
            return {"workspace_verified": True}
        tree = self.github.tree(body["source_sha"])
        verify_tree(files, tree)
        old = self.docker.inspect("jarvis-core")
        previous_sha = old["Config"]["Labels"]["org.opencontainers.image.revision"]
        compatible_code(self.github.tree(previous_sha), tree, self.policy["persistence_blobs"])
        self.prepared["admission"] = {
            "previous_sha": previous_sha,
            "previous_image": old["Image"],
            "compatibility": "identical-persistence-runtime-v1",
        }
        if operation == "build_core_candidate":
            return {"workspace_verified": True}
        canonical_clean(self.policy, body["source_sha"])
        remote = merged_release(self.github, request, self.policy)
        artifact = self.find_artifact(request)
        self.prepared["admission"].update(remote)
        return {
            "workspace_verified": True,
            "release": {
                "repository": body["repository"],
                "branch": "jarvis/unified-production",
                "canonical_sha": body["source_sha"],
                "canonical_clean": True,
                "goal_id": body["goal_id"],
                "goal_completed": True,
                "pr_number": body["parameters"]["pr_number"],
                "pr_head": body["parameters"]["pr_head"],
                "pr_branch": body["branch"],
                "pr_base": "jarvis/unified-production",
                "pr_merged": True,
                "merge_sha": body["source_sha"],
                "checks": remote["checks"],
                "artifact": artifact["image"],
                "artifact_source": artifact["source_sha"],
                "component": "core",
                "rollback_artifact": old["Image"],
                "policy_unchanged": True,
                "independent_review_passed": True,
            },
        }

    def find_artifact(self, request: Request) -> dict:
        paths = list(self.records.base.glob("artifacts-*.json"))
        if len(paths) > 10000:
            raise Denied("artifact_inventory_limit")
        for path in paths:
            item = self.records.read("artifacts", path.name[len("artifacts-") : -5])
            if (
                item
                and item["image"] == request.body["parameters"]["artifact"]
                and item["source_sha"] == request.body["source_sha"]
                and item["goal_id"] == request.body["goal_id"]
            ):
                inspected = self.docker.call("GET", f"/images/{item['image']}/json")
                if (
                    inspected["Id"] == item["image"]
                    and inspected["Config"]["Labels"].get("org.opencontainers.image.revision")
                    == item["source_sha"]
                ):
                    return item
        raise Denied("artifact_not_owned_verified")

    def execute(self, request: Request, fence) -> dict:
        body, operation = request.body, request.body["operation"]
        if operation == "register_job":
            fence()
            path = JOBS / body["goal_id"]
            path.mkdir(mode=0o700)
            os.chown(path, self.policy["autonomy_uid"], self.policy["autonomy_gid"])
            self.records.write(
                "jobs",
                body["goal_id"],
                {
                    "branch": body["branch"],
                    "actor": self.policy["autonomy_uid"],
                    "base_sha": body["source_sha"],
                },
                new=True,
            )
            return {"outcome": "registered", "workspace": str(path), "branch": body["branch"]}
        if operation == "inspect_release":
            live = self.docker.inspect("jarvis-core")
            return {
                "source_sha": live["Config"]["Labels"]["org.opencontainers.image.revision"],
                "component": "core",
            }
        if operation == "run_verification":
            snapshot = materialize_snapshot(
                BASE / "snapshots",
                hashlib.sha256(request.request_id.encode()).hexdigest(),
                self.prepared["files"],
            )
            result = bounded_process(
                sandbox_argv(request, snapshot, self.policy["tool_image"]),
                fence,
                cancel=lambda: self.docker.cancel_sandbox(request.request_id),
            )
            return {**result, "snapshot_sha256": self.prepared["snapshot"]}
        if operation == "build_core_candidate":
            tag = "jarvis-autonomy-core:" + request.request_id
            result = bounded_process(
                [
                    "/usr/bin/docker",
                    "build",
                    "--network=none",
                    "--pull=false",
                    "--force-rm",
                    "--tag",
                    tag,
                    "-",
                ],
                fence,
                stdin=core_context(self.prepared["files"], self.policy, body["source_sha"]),
                timeout=300,
            )
            if result["exit_code"] != 0:
                raise Denied("candidate_build_failed")
            image = self.docker.call("GET", f"/images/{tag}/json")
            artifact = {
                "image": image["Id"],
                "source_sha": body["source_sha"],
                "goal_id": body["goal_id"],
            }
            self.records.write("artifacts", request.request_id, artifact, new=True)
            return {"outcome": "built", **artifact}
        self.records.write(
            "handoff",
            request.request_id,
            {"request": body, "admission": self.prepared.get("admission", {})},
            new=True,
        )
        fence()
        # The fixed helper owns STOP polling and bounded compensation. Killing
        # it merely because STOP arrived could interrupt restoration of normal
        # Core. The outer watchdog is longer than its bounded transition budget.
        result = bounded_process(
            [
                "/bin/bash",
                str(LIB / "deploy_unified_core.sh"),
                "--broker-prebuilt-core",
                request.request_id,
            ],
            lambda: None,
            timeout=900,
        )
        finished = self.records.read("results", request.request_id)
        if result["exit_code"] != 0 or finished is None:
            raise Denied("release_reconciliation_required")
        return finished


def release_entry(key: str, policy: dict) -> None:
    from tools.autonomy_broker import identifier

    identifier(key)
    records = Records(BASE / "records")
    handoff = records.read("handoff", key)
    if handoff is None or records.read("results", key) is not None:
        raise Denied("release_handoff_not_pending")
    request = Request(handoff["request"])
    fence = lambda: active_generation(guardian_status(), request.body["guardian_generation"])
    fence()
    engine = ComposeRelease(policy, records, Docker(), health)
    if request.body["operation"] == "install_core_release":
        if not health(handoff["admission"]["previous_sha"], quiet=True):
            raise Denied("core_not_idle_and_healthy")
        canonical_clean(policy, request.body["source_sha"])
        merged_release(GitHub(), request, policy)
        try:
            result = engine.install(request, fence, handoff["admission"])
        except Exception:
            record = records.read("releases", key)
            if not record or record["stage"] != "rolled_back":
                raise
            result = {
                "outcome": "failed_restored",
                "source_sha": record["previous_sha"],
                "data_reverted": False,
            }
    elif request.body["operation"] == "rollback_core_release":
        record = records.read("releases", request.body["parameters"]["release_request_id"])
        if not record or record["goal_id"] != request.body["goal_id"]:
            raise Denied("rollback_not_owned")
        result = engine.restore(record, fence)
    else:
        raise Denied("unsupported_release_operation")
    records.write("results", key, result, new=True)


def audited_dispatch(ledger: Ledger, dispatch, raw: bytes, actor: int) -> dict:
    try:
        return dispatch(raw, actor)
    except Denied:
        # Never log attacker-controlled fields or error bodies. Even malformed
        # requests get a protected, content-free rejection receipt.
        with ledger.db:
            ledger.event("denied", hashlib.sha256(raw).hexdigest(), actor, "", "rejected", "")
        raise


def recover_uncertain(ledger: Ledger, docker: Docker, stop) -> None:
    rows = ledger.db.execute("SELECT id,operation FROM calls WHERE status='uncertain'").fetchall()
    if not rows:
        return
    try:
        stop()
    finally:
        for request_id, operation in rows:
            if operation == "run_verification":
                docker.cancel_sandbox(request_id)
    # Never promote an interrupted test/release to success or replay its effect.
    # Unknown releases remain blocked for explicit operator reconciliation.


def serve(role: str, policy: dict) -> None:
    if os.getenv("LISTEN_PID") != str(os.getpid()) or os.getenv("LISTEN_FDS") != "1":
        raise Denied("protected_socket_activation_required")
    listener = socket.socket(fileno=3)
    expected = "/run/jarvis-autonomy-broker/" + role + ".sock"
    if listener.family != socket.AF_UNIX or listener.getsockname() != expected:
        raise Denied("wrong_socket")
    ledger = Ledger(BASE / role / "operations.sqlite3")
    if role == "worker":
        runtime = Runtime(policy, Records(BASE / "records"), ledger)
        recover_uncertain(ledger, runtime.docker, emergency_stop)
        broker = Broker(ledger, policy["autonomy_uid"], guardian_status)

        def dispatch(raw, actor):
            if actor == policy["autonomy_uid"]:
                completed = ledger.completed(decode_request(raw), actor)
                if completed is not None:
                    return completed
            # Never start another effect while a previous one is uncertain.
            if ledger.db.execute("SELECT 1 FROM calls WHERE status='uncertain' LIMIT 1").fetchone():
                emergency_stop()
                raise Denied("reconciliation_required")
            try:
                return broker.dispatch(raw, actor, runtime.evidence, runtime.execute)
            except BaseException:
                if ledger.db.execute(
                    "SELECT 1 FROM calls WHERE status IN ('running','uncertain') LIMIT 1"
                ).fetchone():
                    emergency_stop()
                raise
    else:
        controller = guardian_module.Guardian()
        operator = OperatorDispatcher(
            ledger,
            relay_uid=policy["operator_uid"],
            autonomy_uid=policy["autonomy_uid"],
            public_keys={
                k: base64.b64decode(v, validate=True)
                for k, v in policy["operator_public_keys"].items()
            },
            guardian=controller.status,
            stop=controller.stop,
            resume=lambda actor, epoch: controller.resume(
                0,
                actor,
                guardian_module.runtime_ready,
                lambda: guardian_module.fixed_systemctl("start", guardian_module.UNIT),
                expected_generation=epoch,
            ),
        )

        def dispatch(raw, actor):
            if actor == 0 and raw == canonical({"emergency_stop": True}):
                controller.stop(0)
                return {"state": "STOPPED"}
            return operator.dispatch(raw, actor)

    try:
        while True:
            connection, _ = listener.accept()
            with connection:
                handle(
                    connection, lambda raw, actor: audited_dispatch(ledger, dispatch, raw, actor)
                )
    finally:
        ledger.close()


def main(arguments: list[str]) -> int:
    if os.geteuid() != 0:
        return 1
    try:
        policy = installed_policy()
        if arguments in (["worker"], ["operator"]):
            serve(arguments[0], policy)
        elif len(arguments) == 2 and arguments[0] == "release":
            release_entry(arguments[1], policy)
        else:
            return 2
        return 0
    except Exception:
        print(
            "Protected broker operation unavailable; inspect protected state locally.",
            file=sys.stderr,
        )
        return 1
