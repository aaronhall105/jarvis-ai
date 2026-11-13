"""Phone product boundaries: signed preparation, separate activation, read-only review.

These are not execution or GitHub capabilities. Production paths are fixed; the
operator's existing durable ledger owns preparation nonces and activation fences.
No caller selects a database, executable, identity, service or model policy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import Denied, Ledger, Request, canonical, identifier
from tools.supervised_development_controller import (
    CONTROLLER_DATABASE,
    SupervisedDevelopmentController,
)

OPERATOR_DATABASE = Path("/var/lib/jarvis-autonomy-broker/operator/operations.sqlite3")
ADMISSION_DATABASE = Path(
    "/var/lib/jarvis-autonomy-broker/development-admission/operations.sqlite3"
)
READINESS_RECEIPT = CONTROLLER_DATABASE.parent / "development-readiness.json"


def record_readiness(value: dict[str, Any]) -> None:
    """Root-only, non-secret receipt; never put provider credentials in task DB."""
    if os.geteuid() != 0:
        raise Denied("development_readiness_identity")
    path = READINESS_RECEIPT
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Denied("development_readiness_path")
    encoded = canonical(value)
    if len(encoded) > 8192:
        raise Denied("development_readiness_size")
    fd, temporary = tempfile.mkstemp(prefix=".readiness-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_readiness() -> dict[str, Any] | None:
    """An autonomy-owned replacement cannot manufacture root preflight proof."""
    if any(path.is_symlink() for path in READINESS_RECEIPT.parents):
        raise Denied("development_readiness_untrusted")
    try:
        # The worker can replace directory entries but cannot create root-owned
        # evidence. Never block on a forged FIFO before validating its type.
        fd = os.open(READINESS_RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > 8192
        ):
            raise Denied("development_readiness_untrusted")
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise Denied("development_readiness_invalid")
    if type(value.get("expires_at")) is not int or value.get("status") not in {"ready", "blocked"}:
        raise Denied("development_readiness_invalid")
    return value


def require_readiness(reader: SupervisedDevelopmentController, task_id: str) -> dict[str, Any]:
    snapshot = reader.snapshot(task_id)
    proof = read_readiness()
    if (
        proof is None
        or proof.get("status") != "ready"
        or proof.get("task_id") != task_id
        or proof.get("request_id") != snapshot["approval"].get("request_ref")
        or proof.get("parameters_sha256") != snapshot["approval"].get("parameters_sha256")
        or proof.get("source_sha") != snapshot["parameters"].get("base_sha")
        or proof.get("parameters_sha256")
        != hashlib.sha256(canonical(snapshot["parameters"])).hexdigest()
        or proof.get("expires_at", 0) <= time.time()
    ):
        raise Denied("development_execution_preflight_required")
    return proof


def admission_activation_fence(request: dict[str, Any], state: dict[str, Any]) -> None:
    """Task-bound activation must never admit other previously approved work."""
    if state.get("state") != "ACTIVE" or state.get("trusted") is not True:
        raise Denied("guardian_not_active")
    operator = readonly_ledger(OPERATOR_DATABASE)
    try:
        if not trial_fenced(operator, state):
            return  # Existing separately-authorised legacy activation semantics.
        matches = []
        for (raw,) in operator.execute(
            "SELECT body FROM development_product_requests WHERE action='activate_development'"
        ):
            body = json.loads(raw)
            if body["guardian_generation"] in {
                state.get("generation"),
                state.get("previous_generation"),
            }:
                matches.append(body)
        if len(matches) != 1 or any(
            matches[0].get(name) != request.get(name)
            for name in ("task_id", "approval_id", "source_sha", "parameters_sha256")
        ):
            raise Denied("development_admission_activation_mismatch")
    finally:
        operator.close()


CONTROLLER_UNIT = "jarvis-autonomy-broker-development-controller.service"
PREPARATION_FIELDS = {
    "version",
    "action",
    "key_id",
    "request_id",
    "candidate_id",
    "source_sha",
    "evidence_sha256",
    "candidate_sha256",
    "goal",
    "allowed_paths",
    "provider",
    "model",
    "max_calls",
    "max_cost_usd",
    "deadline_seconds",
    "guardian_generation",
    "expires_at",
    "signature",
}
ACTIVATION_FIELDS = {
    "version",
    "action",
    "key_id",
    "request_id",
    "task_id",
    "approval_id",
    "source_sha",
    "parameters_sha256",
    "guardian_generation",
    "expires_at",
    "signature",
}
DOMAINS = {
    "prepare_development": b"jarvis-development-preparation-v1\n",
    "activate_development": b"jarvis-development-activation-v1\n",
}


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def verify_request(body: dict[str, Any], keys: dict[str, bytes], generation: str) -> None:
    action = body.get("action")
    fields = PREPARATION_FIELDS if action == "prepare_development" else ACTIVATION_FIELDS
    if (
        action not in DOMAINS
        or set(body) != fields
        or type(body["version"]) is not int
        or body["version"] != 1
    ):
        raise Denied("development_product_schema")
    identifier(body["request_id"])
    identifier(body["guardian_generation"])
    if body["guardian_generation"] != generation:
        raise Denied("development_product_stale_generation")
    if (
        type(body["expires_at"]) is not int
        or not time.time() < body["expires_at"] <= time.time() + 300
    ):
        raise Denied("development_product_expired")
    if not isinstance(body["source_sha"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", body["source_sha"]
    ):
        raise Denied("development_product_source")
    try:
        encoded = keys[body["key_id"]]
        if hashlib.sha256(encoded).hexdigest() != body["key_id"]:
            raise Denied("operator_key_integrity")
        public_identity = serialization.load_der_public_key(encoded)
        if not isinstance(public_identity, ec.EllipticCurvePublicKey) or not isinstance(
            public_identity.curve, ec.SECP256R1
        ):
            raise Denied("operator_key_type")
        if not isinstance(body["signature"], str) or len(body["signature"]) > 128:
            raise Denied("operator_signature")
        public_identity.verify(
            base64.b64decode(body["signature"], validate=True),
            DOMAINS[action] + canonical({k: v for k, v in body.items() if k != "signature"}),
            ec.ECDSA(hashes.SHA256()),
        )
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise Denied("operator_signature") from exc


def readonly_ledger(path: Path) -> sqlite3.Connection:
    """Read and verify an existing protected ledger without schema/lock writes."""
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Denied("development_product_ledger_untrusted")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise Denied("development_product_ledger_untrusted")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    try:
        audit = Ledger.__new__(Ledger)
        audit.db = db
        audit.verify_audit()
    except BaseException:
        db.close()
        raise
    return db


def candidate_request(
    source: Any,
    candidate_id: int,
    state: dict[str, Any],
    *,
    request_id: str | None = None,
    expires_at: int | None = None,
) -> dict[str, Any]:
    from tools.supervised_development_pipeline import GovernorAdapter
    from tools.supervised_development_runtime import OWNER_ID, _configured_call_limit

    if type(candidate_id) is not int or candidate_id <= 0:
        raise Denied("development_candidate_invalid")
    if state.get("trusted") is not True or state.get("state") != "STOPPED":
        raise Denied("guardian_must_be_stopped")
    candidate = next(
        (
            item
            for item in source.review_queue()
            if item["candidate_id"] == candidate_id and item["eligible"]
        ),
        None,
    )
    if candidate is None:
        raise Denied("development_candidate_ineligible")
    request_id = request_id or str(uuid.uuid4())
    expires_at = expires_at if expires_at is not None else int(time.time()) + 300
    task = GovernorAdapter.from_candidate(
        candidate,
        owner_id=OWNER_ID,
        request_ref=request_id,
        source_sha=candidate["current_source_sha"],
        allowed_paths=tuple(candidate["allowed_paths"]),
        guardian_generation=state["generation"],
        expires_at=expires_at,
        max_calls=_configured_call_limit(),
        model=os.getenv("JARVIS_SUPERVISED_MODEL", "gpt-5.3-codex"),
    )
    evidence = candidate["evidence"]
    return {
        "version": 1,
        "action": "prepare_development",
        "request_id": request_id,
        "candidate_id": candidate_id,
        "source_sha": task.source_sha,
        "evidence_sha256": evidence["evidence_sha256"],
        "candidate_sha256": digest(
            {"candidate_id": candidate_id, "goal": task.objective, "evidence": evidence}
        ),
        "goal": task.objective,
        "allowed_paths": list(task.allowed_paths),
        "provider": task.provider,
        "model": task.model,
        "max_calls": task.max_calls,
        "max_cost_usd": task.max_cost_usd,
        "deadline_seconds": task.deadline_seconds,
        "guardian_generation": state["generation"],
        "expires_at": expires_at,
    }


def approved_task(
    reader: SupervisedDevelopmentController, task_id: str, admission: sqlite3.Connection
) -> dict[str, Any]:
    identifier(task_id)
    pending = reader.pending_owner_task()
    if pending is None or pending.get("task_id") != task_id:
        raise Denied("development_task_not_pending")
    row = admission.execute(
        "SELECT payload,status,fingerprint FROM development_admissions WHERE approval_id=?",
        (pending["request_ref"],),
    ).fetchone()
    if row is None or row[1] != "approved":
        raise Denied("development_owner_approval_required")
    approved = json.loads(row[0])
    from tools.supervised_development_broker import _fingerprint

    if _fingerprint(approved) != row[2]:
        raise Denied("development_owner_approval_mismatch")
    # Display-only goal is not an admission field. Every signed field must match.
    for name, value in pending.items():
        if (
            name not in {"goal", "status"}
            and approved.get("request_id" if name == "request_ref" else name) != value
        ):
            raise Denied("development_owner_approval_mismatch")
    return pending


def activation_request(
    reader: SupervisedDevelopmentController,
    task_id: str,
    state: dict[str, Any],
    admission: sqlite3.Connection,
    *,
    request_id: str | None = None,
    expires_at: int | None = None,
) -> dict[str, Any]:
    pending = approved_task(reader, task_id, admission)
    if (
        state.get("trusted") is not True
        or state.get("state") != "STOPPED"
        or state.get("generation") != pending["guardian_generation"]
    ):
        raise Denied("development_product_stale_generation")
    return {
        "version": 1,
        "action": "activate_development",
        "request_id": request_id or str(uuid.uuid4()),
        "task_id": task_id,
        "approval_id": pending["request_ref"],
        "source_sha": pending["source_sha"],
        "parameters_sha256": pending["parameters_sha256"],
        "guardian_generation": state["generation"],
        "expires_at": min(pending["expires_at"], int(time.time()) + 300)
        if expires_at is None
        else expires_at,
    }


class ProductOperator:
    """Operator-service adapter. Callbacks are fixed production code, never request fields."""

    def __init__(
        self,
        ledger: Ledger,
        keys: dict[str, bytes],
        guardian: Callable,
        prepare: Callable,
        activate: Callable,
        notify: Callable | None = None,
    ):
        self.ledger, self.keys, self.guardian, self.prepare, self.activate = (
            ledger,
            keys,
            guardian,
            prepare,
            activate,
        )
        self.notify = notify
        self.ledger.db.execute(
            "CREATE TABLE IF NOT EXISTS development_product_requests (id TEXT PRIMARY KEY, action TEXT NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL)"
        )
        self.ledger.db.commit()

    def dispatch(self, body: dict[str, Any], actor: int) -> dict[str, Any]:
        request = Request(
            {
                "request_id": body.get("request_id"),
                "goal_id": body.get("request_id"),
                "source_sha": body.get("source_sha"),
                "operation": "operator_control",
                "signed_request": body,
            }
        )
        prior = self.ledger.completed(request.body, actor)
        if prior is not None:
            if (
                body.get("action") == "prepare_development"
                and self.notify is not None
                and body["expires_at"] > time.time()
            ):
                self.notify()
            return prior
        state = self.guardian()
        if state.get("trusted") is not True or state.get("state") != "STOPPED":
            raise Denied("guardian_must_be_stopped")
        verify_request(body, self.keys, state["generation"])
        self.ledger.reserve(request, actor)
        try:
            with self.ledger.db:
                self.ledger.db.execute(
                    "INSERT INTO development_product_requests VALUES(?,?,?,?)",
                    (
                        body["request_id"],
                        body["action"],
                        canonical(body).decode(),
                        int(time.time()),
                    ),
                )
            result = (
                self.prepare(body)
                if body["action"] == "prepare_development"
                else self.activate(body, actor)
            )
        except BaseException:
            self.ledger.finish(request, actor, None)
            raise
        self.ledger.finish(request, actor, result)
        # Persist before notification. Repeating this fixed, non-executing
        # notification is safe: the controller consumes each request at most
        # once; no signature/nonce or task is regenerated after a failed start.
        if body["action"] == "prepare_development" and self.notify is not None:
            self.notify()
        return result


def queued_preparation(
    controller: SupervisedDevelopmentController, operator: sqlite3.Connection
) -> dict[str, Any] | None:
    """An uncertain dispatch never becomes implicit permission to create another task."""
    used = {
        json.loads(row[0] or "{}").get("request_ref")
        for row in controller.db.execute("SELECT approval FROM tasks")
    }
    tables = {
        row[0] for row in operator.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "development_product_requests" not in tables:
        return None
    rows = operator.execute(
        "SELECT p.body,c.status,c.fingerprint FROM development_product_requests p JOIN calls c ON c.id=p.id WHERE p.action='prepare_development' ORDER BY p.created_at,p.id"
    ).fetchall()
    candidates = []
    for raw, status, fingerprint in rows:
        body = json.loads(raw)
        recorded = Request(
            {
                "request_id": body["request_id"],
                "goal_id": body["request_id"],
                "source_sha": body["source_sha"],
                "operation": "operator_control",
                "signed_request": body,
            }
        )
        if recorded.fingerprint != fingerprint:
            raise Denied("development_preparation_record_changed")
        if (
            status == "completed"
            and body["request_id"] not in used
            and body["expires_at"] > time.time()
        ):
            candidates.append(body)
    if len(candidates) > 1:
        raise Denied("development_multiple_preparations")
    return candidates[0] if candidates else None


def trial_fenced(operator: sqlite3.Connection, state: dict[str, Any]) -> bool:
    if state.get("state") != "ACTIVE":
        return False
    tables = {
        row[0] for row in operator.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "development_product_requests" not in tables:
        return False
    for (raw,) in operator.execute(
        "SELECT body FROM development_product_requests WHERE action='activate_development'"
    ):
        body = json.loads(raw)
        if body["guardian_generation"] in {
            state.get("generation"),
            state.get("previous_generation"),
        }:
            return True
    return False


def production_preview(
    body: dict[str, Any], reader: SupervisedDevelopmentController, *, export: bool = False
) -> dict[str, Any]:
    from tools.autonomy_broker_runtime import guardian_status, installed_policy
    from tools.autonomy_broker_host import CANONICAL
    from tools.supervised_development_runtime import GovernorCandidateSource, WORKTREE_ROOT
    from tools.supervised_development_integration import prepare_proposal, approval_request, _git

    view = body.get("view")
    wanted = {"version", "view", "candidate_id" if view == "prepare" else "task_id"}
    if set(body) != wanted or body.get("version") != 3:
        raise Denied("development_product_schema")
    if view == "prepare":
        source = GovernorCandidateSource(source_root=CANONICAL)
        try:
            request = candidate_request(source, body["candidate_id"], guardian_status())
            claimed = {
                json.loads(row[0]).get("candidate_id")
                for row in reader.db.execute("SELECT parameters FROM tasks")
            }
            if body["candidate_id"] in claimed:
                raise Denied("development_candidate_already_claimed")
        finally:
            source.close()
        proof = read_readiness()
        if (
            proof is not None
            and proof.get("status") == "blocked"
            and proof.get("candidate_id") == body["candidate_id"]
            and proof.get("source_sha") == request["source_sha"]
            and proof["expires_at"] > time.time()
        ):
            return {
                "version": 3,
                "status": "blocked",
                "request": None,
                "reason": "development_execution_preflight_failed",
            }
        return {"version": 3, "status": "ready", "request": request}
    if view == "activation":
        require_readiness(reader, body["task_id"])
        admission = readonly_ledger(ADMISSION_DATABASE)
        try:
            request = activation_request(reader, body["task_id"], guardian_status(), admission)
        finally:
            admission.close()
        return {"version": 3, "status": "ready", "request": request}
    if view not in {"proposal", "integration_export"} or (view == "integration_export") != export:
        raise Denied("development_product_view_denied")
    policy = installed_policy()
    proposal = prepare_proposal(
        reader.database, body["task_id"], WORKTREE_ROOT, expected_receipt_uid=policy["autonomy_uid"]
    )
    if export:
        return {"version": 3, "status": "ready", "proposal": proposal}
    snapshot = reader.snapshot(body["task_id"])
    evidence = snapshot["evidence"]
    usage = evidence.get("provider_usage", {})
    cost = usage.get("estimated_cost_usd")
    if (
        type(cost) not in {int, float}
        or not math.isfinite(cost)
        or not 0 <= cost <= snapshot["parameters"]["max_cost_usd"]
        or type(usage.get("actual_provider_calls")) is not int
        or usage["actual_provider_calls"] != snapshot["provider_calls"]
    ):
        raise Denied("development_proposal_usage_invalid")
    diff = _git(
        Path(evidence["worktree"]),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        proposal["base_sha"],
        "--",
    )
    if len(diff) > 200_000 or hashlib.sha256(diff).hexdigest() != proposal["diff_sha256"]:
        raise Denied("development_proposal_display_limit")
    request = approval_request(proposal, key_id="0" * 64, expires_at=int(time.time()) + 300)
    request.pop("key_id")
    displayed = {
        key: proposal[key]
        for key in (
            "task_id",
            "base_sha",
            "parameters_sha256",
            "evidence_sha256",
            "diff_sha256",
            "proposal_sha256",
            "allowed_paths",
        )
    }
    displayed.update(
        changed_files=[item["path"] for item in proposal["files"]],
        diff=diff.decode("utf-8", "strict"),
        checks=evidence["tests"]["checks"],
        provider_calls=snapshot["provider_calls"],
        cost_usd=cost,
        risk="owner_review_required",
    )
    return {
        "version": 3,
        "status": "ready",
        "request": request,
        "proposal": displayed,
        "handoff": None,
    }


def production_operator(ledger: Ledger, keys: dict[str, bytes], guardian: Any) -> ProductOperator:
    from tools.autonomy_broker_host import CANONICAL, SAFE_ENV
    from tools.autonomy_guardian import fixed_systemctl, UNIT
    from tools.supervised_development_runtime import GovernorCandidateSource, _trusted_source_git
    import subprocess

    def prepare(body: dict[str, Any]) -> dict[str, Any]:
        source = GovernorCandidateSource(source_root=CANONICAL)
        reader = SupervisedDevelopmentController(CONTROLLER_DATABASE, read_only=True)
        try:
            expected = candidate_request(
                source,
                body["candidate_id"],
                guardian.status(),
                request_id=body["request_id"],
                expires_at=body["expires_at"],
            )
            if expected != {k: v for k, v in body.items() if k not in {"key_id", "signature"}}:
                raise Denied("development_preparation_changed")
            if reader.db.execute(
                "SELECT 1 FROM tasks WHERE state NOT IN ('FAILED','REVIEW_REQUIRED') LIMIT 1"
            ).fetchone():
                raise Denied("development_task_already_active")
            if any(
                json.loads(row[0]).get("candidate_id") == body["candidate_id"]
                for row in reader.db.execute("SELECT parameters FROM tasks")
            ):
                raise Denied("development_candidate_already_claimed")
        finally:
            reader.close()
            source.close()
        return {"status": "preparation_requested", "request_id": body["request_id"]}

    def activate(body: dict[str, Any], actor: int) -> dict[str, Any]:
        reader = SupervisedDevelopmentController(CONTROLLER_DATABASE, read_only=True)
        admission = readonly_ledger(ADMISSION_DATABASE)
        try:
            require_readiness(reader, body["task_id"])
            expected = activation_request(
                reader,
                body["task_id"],
                guardian.status(),
                admission,
                request_id=body["request_id"],
                expires_at=body["expires_at"],
            )
            pending = reader.pending_owner_task()
            if (
                expected != {k: v for k, v in body.items() if k not in {"key_id", "signature"}}
                or pending is None
                or body["expires_at"] > pending["expires_at"]
            ):
                raise Denied("development_activation_changed")
            if _trusted_source_git(CANONICAL, "rev-parse", "HEAD") != body[
                "source_sha"
            ] or _trusted_source_git(CANONICAL, "status", "--porcelain"):
                raise Denied("development_source_drift")

            def ready() -> bool:
                active = subprocess.run(
                    ["/usr/bin/systemctl", "is-active", UNIT],
                    env=SAFE_ENV,
                    capture_output=True,
                    timeout=5,
                )
                enabled = subprocess.run(
                    ["/usr/bin/systemctl", "is-enabled", UNIT],
                    env=SAFE_ENV,
                    capture_output=True,
                    timeout=5,
                )
                return (
                    active.stdout.strip() == b"inactive"
                    and enabled.stdout.strip() in {b"disabled", b"masked"}
                    and fixed_systemctl("is-active", "--quiet", CONTROLLER_UNIT)
                )

            try:
                guardian.resume(
                    0,
                    actor,
                    ready,
                    lambda: fixed_systemctl("is-active", "--quiet", CONTROLLER_UNIT),
                    expected_generation=body["guardian_generation"],
                )
                after = guardian.status()
                if (
                    after.get("trusted") is not True
                    or after.get("state") != "ACTIVE"
                    or after.get("previous_generation") != body["guardian_generation"]
                ):
                    raise Denied("development_activation_unconfirmed")
            except BaseException:
                guardian.stop(actor)
                after = guardian.status()
                if after.get("trusted") is not True or after.get("state") != "STOPPED":
                    raise Denied("critical_guardian_stop_unconfirmed") from None
                raise
            return {
                "status": "active",
                "task_id": body["task_id"],
                "request_id": body["request_id"],
            }
        finally:
            admission.close()
            reader.close()

    def notify() -> None:
        state = guardian.status()
        if state.get("trusted") is not True or state.get("state") != "STOPPED":
            raise Denied("guardian_must_be_stopped")
        if not fixed_systemctl("start", "--no-block", CONTROLLER_UNIT):
            raise Denied("development_controller_start_failed")

    return ProductOperator(ledger, keys, guardian.status, prepare, activate, notify)
