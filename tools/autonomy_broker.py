"""Protected broker policy and durable admission ledger.

No deployment script, subprocess, network client or production entry point lives
in this module. An executor must independently supply trusted runtime evidence;
request fields and Internet/model output are never authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import stat
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

REPOSITORY = "aaronhall105/jarvis-ai"
PRODUCTION_BRANCH = "jarvis/unified-production"
REQUIRED_CHECKS = frozenset(
    {"core-tests", "quality-security", "android-unit-tests", "Analyze Python"}
)
MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 65536
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
ENVELOPE = frozenset(
    {
        "version",
        "request_id",
        "goal_id",
        "operation",
        "repository",
        "branch",
        "source_sha",
        "expires_at",
        "guardian_generation",
        "parameters",
    }
)
PARAMETERS = {
    "register_job": set(),
    "run_verification": {"profile", "target"},
    "build_core_candidate": set(),
    "install_core_release": {"artifact", "pr_number", "pr_head"},
    "rollback_core_release": {"release_request_id"},
    "inspect_release": set(),
}
SANDBOX_PROFILES = {
    "verify_python": (
        "/usr/local/bin/python",
        "-m",
        "pytest",
        "-q",
        "-o",
        "cache_dir=/tmp/pytest-cache",
        "--basetemp=/tmp/pytest",
    ),
    "verify_security": ("/usr/local/bin/python", "-m", "bandit", "-q", "-r", "-lll"),
}
FORBIDDEN_PARTS = {".git", ".env", ".ssh", ".aws", ".config", "credentials", "secrets"}


class Denied(RuntimeError):
    """Only predefined, content-free reason codes cross the broker boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def identifier(value: Any) -> str:
    if not isinstance(value, str):
        raise Denied("invalid_identifier")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise Denied("invalid_identifier") from None
    return value


def source_sha(value: Any) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Denied("invalid_source_sha")
    return value


def relative_source(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", value):
        raise Denied("invalid_source_path")
    parts = value.split("/")
    if (
        len(parts) > 16
        or any(p in {"", ".", ".."} or p.startswith("-") for p in parts)
        or any(
            p.lower() in FORBIDDEN_PARTS
            or p.lower().endswith((".pem", ".key", ".p12"))
            or p.lower().startswith(".env.")
            for p in parts
        )
    ):
        raise Denied("forbidden_source_path")
    return str(PurePosixPath(value))


def decode_request(data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_REQUEST_BYTES:
        raise Denied("request_size")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Denied("duplicate_json_key")
            result[key] = value
        return result

    try:
        result = json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Denied("invalid_json")),
        )
    except (UnicodeError, ValueError, RecursionError):
        raise Denied("invalid_json") from None
    if not isinstance(result, dict):
        raise Denied("invalid_schema")
    return result


def peer_uid(connection: socket.socket) -> int:
    # Kernel credentials, not JSON fields or environment variables.
    _, uid, _ = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    return uid


@dataclass(frozen=True)
class Request:
    body: dict[str, Any]

    @property
    def request_id(self) -> str:
        return self.body["request_id"]

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical(self.body)).hexdigest()


def validate_request(body: dict[str, Any], now: float) -> Request:
    if set(body) != ENVELOPE or type(body.get("version")) is not int or body["version"] != 1:
        raise Denied("invalid_schema")
    for key in ("request_id", "goal_id", "guardian_generation"):
        identifier(body[key])
    if body["repository"] != REPOSITORY:
        raise Denied("wrong_repository")
    if body["branch"] != "jarvis/autonomous-" + uuid.UUID(body["goal_id"]).hex:
        raise Denied("wrong_branch")
    source_sha(body["source_sha"])
    expires = body["expires_at"]
    if type(expires) is not int or not now < expires <= now + 300:
        raise Denied("request_expired_or_overlong")
    operation, params = body["operation"], body["parameters"]
    if not isinstance(operation, str) or operation not in PARAMETERS:
        raise Denied("unsupported_operation")
    if not isinstance(params, dict) or set(params) != PARAMETERS[operation]:
        raise Denied("invalid_parameters")
    if operation == "run_verification":
        profile = params["profile"]
        if not isinstance(profile, str) or profile not in SANDBOX_PROFILES:
            raise Denied("unsupported_profile")
        target = relative_source(params["target"])
        allowed = (
            ("bridge/tests", "developer_gateway/tests", "tools/tests")
            if profile == "verify_python"
            else ("bridge/app", "developer_gateway")
        )
        if not any(target == prefix or target.startswith(prefix + "/") for prefix in allowed):
            raise Denied("unsupported_target")
    elif operation == "install_core_release":
        if not isinstance(params["artifact"], str) or not DIGEST.fullmatch(params["artifact"]):
            raise Denied("invalid_artifact")
        if type(params["pr_number"]) is not int or not 0 < params["pr_number"] < 10**9:
            raise Denied("invalid_pr")
        source_sha(params["pr_head"])
    elif operation == "rollback_core_release":
        identifier(params["release_request_id"])
    return Request(body)


def active_generation(status: dict[str, Any], generation: str) -> None:
    if status.get("trusted") is not True or status.get("state") != "ACTIVE":
        raise Denied("guardian_not_active")
    if status.get("generation") != generation:
        raise Denied("guardian_generation_changed")


def validate_release(request: Request, evidence: dict[str, Any]) -> None:
    """Evidence MUST be fetched by a trusted adapter, never supplied by a caller."""
    body, params = request.body, request.body["parameters"]
    required = {
        "repository",
        "branch",
        "canonical_sha",
        "canonical_clean",
        "goal_id",
        "goal_completed",
        "pr_number",
        "pr_head",
        "pr_branch",
        "pr_base",
        "pr_merged",
        "merge_sha",
        "checks",
        "artifact",
        "artifact_source",
        "component",
        "rollback_artifact",
        "policy_unchanged",
        "independent_review_passed",
    }
    if set(evidence) != required:
        raise Denied("incomplete_release_evidence")
    expected = {
        "repository": REPOSITORY,
        "branch": PRODUCTION_BRANCH,
        "canonical_sha": body["source_sha"],
        "canonical_clean": True,
        "goal_id": body["goal_id"],
        "goal_completed": True,
        "pr_number": params["pr_number"],
        "pr_head": params["pr_head"],
        "pr_branch": body["branch"],
        "pr_base": PRODUCTION_BRANCH,
        "pr_merged": True,
        "merge_sha": body["source_sha"],
        "artifact": params["artifact"],
        "artifact_source": body["source_sha"],
        "component": "core",
        "policy_unchanged": True,
        "independent_review_passed": True,
    }
    for key, value in expected.items():
        if type(evidence[key]) is not type(value) or evidence[key] != value:
            raise Denied("release_evidence_mismatch")
    checks = evidence["checks"]
    if not isinstance(checks, list):
        raise Denied("invalid_checks")
    for name in REQUIRED_CHECKS:
        rows = [c for c in checks if isinstance(c, dict) and c.get("name") == name]
        if (
            len(rows) != 1
            or rows[0].get("head_sha") != params["pr_head"]
            or rows[0].get("conclusion") != "success"
            or rows[0].get("status") != "completed"
            or rows[0].get("trusted_workflow") is not True
        ):
            raise Denied("protected_checks_not_passed")
    rollback = evidence["rollback_artifact"]
    if (
        not isinstance(rollback, str)
        or not DIGEST.fullmatch(rollback)
        or rollback == params["artifact"]
    ):
        raise Denied("rollback_not_recorded")


class Ledger:
    """At-most-once dispatch, durable result replay; uncertain effects never replay.

    Audit and state transitions share a FULL-synchronous SQLite transaction.
    A service owner must hold its exclusive process lock for the Ledger lifetime.
    """

    def __init__(self, path: Path, owner_uid: int = 0):
        import fcntl

        parent = path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != owner_uid or parent.st_mode & 0o077:
            raise Denied("unprotected_ledger_directory")
        self.lock_fd = os.open(
            path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        lock_info = os.fstat(self.lock_fd)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_nlink != 1
            or lock_info.st_uid != owner_uid
            or lock_info.st_mode & 0o077
        ):
            os.close(self.lock_fd)
            raise Denied("unprotected_ledger_lock")
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != owner_uid
                    or info.st_mode & 0o077
                ):
                    raise Denied("unprotected_ledger")
            else:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            self.db = sqlite3.connect(path)
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS calls (
                  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, actor INTEGER NOT NULL,
                  goal TEXT NOT NULL, operation TEXT NOT NULL, source_sha TEXT NOT NULL,
                  status TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                  seq INTEGER PRIMARY KEY, entry TEXT NOT NULL, hash TEXT NOT NULL);
            """)
            self.verify_audit()
            with self.db:
                rows = self.db.execute(
                    "SELECT id,actor,goal,operation,source_sha FROM calls WHERE status='running'"
                ).fetchall()
                for request_id, actor, goal, operation, source in rows:
                    self.event(
                        "uncertain_after_restart", request_id, actor, goal, operation, source
                    )
                self.db.execute("UPDATE calls SET status='uncertain' WHERE status='running'")
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            os.close(self.lock_fd)
            raise

    def close(self) -> None:
        self.db.close()
        os.close(self.lock_fd)

    def event(
        self,
        category: str,
        request_id: str,
        actor: int,
        goal: str,
        operation: str,
        source: str,
        result_hash: str | None = None,
    ) -> None:
        if category not in {
            "accepted",
            "completed",
            "uncertain",
            "uncertain_after_restart",
            "denied",
        }:
            raise Denied("invalid_audit_category")
        previous = self.db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        entry = {
            "category": category,
            "request_id": request_id,
            "actor_uid": actor,
            "goal_id": goal,
            "operation": operation,
            "source_sha": source,
            "at": int(time.time()),
            "previous": previous[0] if previous else "0" * 64,
            "result_hash": result_hash,
        }
        raw = canonical(entry)
        self.db.execute(
            "INSERT INTO audit(entry,hash) VALUES(?,?)",
            (raw.decode(), hashlib.sha256(raw).hexdigest()),
        )

    def verify_audit(self) -> int:
        previous, count = "0" * 64, 0
        for raw, digest in self.db.execute("SELECT entry,hash FROM audit ORDER BY seq"):
            entry = json.loads(raw)
            if entry["previous"] != previous or hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise Denied("audit_chain_invalid")
            previous, count = digest, count + 1
        return count

    def reserve(self, request: Request, actor: int) -> dict[str, Any] | None:
        body = request.body
        with self.db:
            existing = self.db.execute(
                "SELECT fingerprint,actor,status,result FROM calls WHERE id=?",
                (request.request_id,),
            ).fetchone()
            if existing:
                if existing[0] != request.fingerprint or existing[1] != actor:
                    raise Denied("request_identity_conflict")
                if existing[2] != "completed":
                    raise Denied("reconciliation_required")
                return json.loads(existing[3])
            self.event(
                "accepted",
                request.request_id,
                actor,
                body["goal_id"],
                body["operation"],
                body["source_sha"],
            )
            self.db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL)",
                (
                    request.request_id,
                    request.fingerprint,
                    actor,
                    body["goal_id"],
                    body["operation"],
                    body["source_sha"],
                    "running",
                ),
            )
        return None

    def completed(self, body: dict[str, Any], actor: int) -> dict[str, Any] | None:
        """A completed result is read-only, including after expiry/STOP/restart."""
        request_id = body.get("request_id")
        if not isinstance(request_id, str):
            return None
        row = self.db.execute(
            "SELECT fingerprint,actor,status,result FROM calls WHERE id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != hashlib.sha256(canonical(body)).hexdigest() or row[1] != actor:
            raise Denied("request_identity_conflict")
        if row[2] != "completed":
            raise Denied("reconciliation_required")
        return json.loads(row[3])

    def finish(self, request: Request, actor: int, result: dict[str, Any] | None) -> None:
        body = request.body
        encoded = canonical(result) if result is not None else None
        if encoded is not None and len(encoded) > MAX_RESPONSE_BYTES:
            raise Denied("result_size")
        state = "completed" if encoded is not None else "uncertain"
        with self.db:
            row = self.db.execute(
                "SELECT status,fingerprint,actor FROM calls WHERE id=?", (request.request_id,)
            ).fetchone()
            if row != ("running", request.fingerprint, actor):
                raise Denied("invalid_ledger_transition")
            self.event(
                state,
                request.request_id,
                actor,
                body["goal_id"],
                body["operation"],
                body["source_sha"],
                hashlib.sha256(encoded).hexdigest() if encoded else None,
            )
            self.db.execute(
                "UPDATE calls SET status=?,result=? WHERE id=?",
                (state, encoded.decode() if encoded else None, request.request_id),
            )


class Broker:
    def __init__(self, ledger: Ledger, autonomy_uid: int, guardian: Callable[[], dict[str, Any]]):
        self.ledger, self.autonomy_uid, self.guardian = ledger, autonomy_uid, guardian

    def dispatch(self, raw: bytes, peer: int, evidence, execute) -> dict[str, Any]:
        if peer != self.autonomy_uid or peer == 0:
            raise Denied("unauthorized_peer")
        body = decode_request(raw)
        prior = self.ledger.completed(body, peer)
        if prior is not None:
            return prior
        request = validate_request(body, time.time())

        def fence():
            try:
                active_generation(self.guardian(), request.body["guardian_generation"])
            except Denied:
                raise
            except Exception:
                raise Denied("guardian_unavailable") from None

        fence()
        # New effects need fresh workspace/job/SHA evidence. Completed replay
        # above returns only an already durable result, even after STOP/expiry.
        observed = evidence(request)
        if observed.get("workspace_verified") is not True:
            raise Denied("workspace_unverified")
        if request.body["operation"] == "install_core_release":
            validate_release(request, observed["release"])
        fence()
        prior = self.ledger.reserve(request, peer)
        if prior is not None:
            return prior
        try:
            fence()
            result = execute(request, fence)
            if not isinstance(result, dict):
                raise Denied("invalid_result")
        except BaseException:
            self.ledger.finish(request, peer, None)
            raise
        # A failed durable completion leaves a running/uncertain record. Never
        # mask that failure with a second, potentially conflicting transition.
        self.ledger.finish(request, peer, result)
        return result


def sandbox_argv(request: Request, snapshot: Path, image: str) -> list[str]:
    if request.body["operation"] != "run_verification" or not DIGEST.fullmatch(image):
        raise Denied("unsupported_sandbox")
    if not snapshot.is_absolute() or "," in str(snapshot) or "\n" in str(snapshot):
        raise Denied("invalid_snapshot")
    params = request.body["parameters"]
    # The production adapter must create an immutable root-owned snapshot. It
    # must NEVER pass a caller-owned host path straight to a privileged mount.
    return [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        "jarvis-autonomy-" + uuid.UUID(request.request_id).hex,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--memory=2g",
        "--memory-swap=2g",
        "--cpus=2",
        "--pids-limit=256",
        "--user=65534:65534",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--env=HOME=/tmp",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=PYTHONPATH=/workspace/bridge:/workspace",
        "--mount",
        f"type=bind,src={snapshot},dst=/workspace,readonly",
        "--workdir=/workspace",
        "--entrypoint=/usr/bin/timeout",
        image,
        "--signal=KILL",
        "600",
        *SANDBOX_PROFILES[params["profile"]],
        params["target"],
    ]
