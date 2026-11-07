"""Disposable/protected broker protocol for supervised development admission.

The operator approval is a domain-separated ECDSA signature.  Admission is a
separate worker-facing capability: it is bound to the signed approval, exact
task parameters and the current trusted Guardian generation.  This module does
not install a service or change the live broker; the protected service must be
deployed separately with root-owned storage and a fixed socket.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import struct
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import Denied, Ledger, canonical, decode_request


DOMAIN = b"jarvis-development-admission-v1\n"
APPROVAL_FIELDS = frozenset(
    {
        "version",
        "action",
        "key_id",
        "request_id",
        "task_id",
        "source_sha",
        "parameters_sha256",
        "allowed_paths",
        "provider",
        "model",
        "max_calls",
        "max_cost_usd",
        "deadline_seconds",
        "expires_at",
        "guardian_generation",
        "signature",
    }
)
MAX_FRAME = 16_384


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def _verify_signature(body: dict[str, Any], public_keys: dict[str, bytes]) -> dict[str, Any]:
    if set(body) != APPROVAL_FIELDS or body.get("version") != 1:
        raise Denied("development_approval_schema")
    if body.get("action") != "approve_development":
        raise Denied("development_approval_action")
    key_id = body.get("key_id")
    encoded = public_keys.get(key_id) if isinstance(key_id, str) else None
    if encoded is None or hashlib.sha256(encoded).hexdigest() != key_id:
        raise Denied("development_approval_key")
    signature = body.get("signature")
    if not isinstance(signature, str):
        raise Denied("development_approval_signature")
    signed = {key: value for key, value in body.items() if key != "signature"}
    try:
        verifier = serialization.load_der_public_key(encoded)
        if not isinstance(verifier, ec.EllipticCurvePublicKey) or not isinstance(
            verifier.curve, ec.SECP256R1
        ):
            raise Denied("development_approval_key_type")
        verifier.verify(
            base64.b64decode(signature, validate=True),
            DOMAIN + canonical(signed),
            ec.ECDSA(hashes.SHA256()),
        )
    except (ValueError, TypeError, InvalidSignature):
        raise Denied("development_approval_signature") from None
    return signed


def _validate_limits(body: dict[str, Any]) -> None:
    if (
        not isinstance(body.get("task_id"), str)
        or not isinstance(body.get("request_id"), str)
        or not isinstance(body.get("source_sha"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", body["source_sha"])
        or not isinstance(body.get("parameters_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", body["parameters_sha256"])
        or not isinstance(body.get("allowed_paths"), list)
        or not body["allowed_paths"]
        or any(
            not isinstance(path, str)
            or path.startswith("/")
            or ".." in Path(path).parts
            or "\\" in path
            for path in body["allowed_paths"]
        )
        or body.get("provider") != "openai"
        or not isinstance(body.get("model"), str)
        or body.get("max_calls") != 1
        or not isinstance(body.get("max_cost_usd"), (int, float))
        or not 0 < body["max_cost_usd"] <= 1
        or not isinstance(body.get("deadline_seconds"), int)
        or not 0 < body["deadline_seconds"] <= 120
        or not isinstance(body.get("expires_at"), int)
        or body["expires_at"] <= int(time.time())
    ):
        raise Denied("development_approval_limits")


class DevelopmentAdmissionBroker:
    """Owner approval and worker admission over the existing protected ledger."""

    def __init__(
        self,
        ledger: Ledger,
        *,
        relay_uid: int,
        worker_uid: int,
        public_keys: dict[str, bytes],
        guardian,
    ) -> None:
        self.ledger = ledger
        self.relay_uid = relay_uid
        self.worker_uid = worker_uid
        self.public_keys = dict(public_keys)
        self.guardian = guardian
        self.ledger.db.execute(
            """CREATE TABLE IF NOT EXISTS development_admissions (
                approval_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                receipt TEXT
            )"""
        )

    def approve(self, raw: bytes, actor_uid: int) -> dict[str, Any]:
        if actor_uid != self.relay_uid or actor_uid == self.worker_uid:
            raise Denied("development_approval_peer")
        body = _verify_signature(decode_request(raw), self.public_keys)
        _validate_limits(body)
        fingerprint = _fingerprint(body)
        with self.ledger.db:
            existing = self.ledger.db.execute(
                "SELECT fingerprint,payload,status,receipt FROM development_admissions WHERE approval_id=?",
                (body["request_id"],),
            ).fetchone()
            if existing is not None:
                if existing[0] != fingerprint:
                    raise Denied("development_approval_replay_conflict")
                return json.loads(existing[3] or existing[1])
            self.ledger.event(
                "accepted",
                body["request_id"],
                actor_uid,
                body["task_id"],
                "approve_development",
                body["source_sha"],
            )
            self.ledger.db.execute(
                "INSERT INTO development_admissions VALUES(?,?,?,?,NULL)",
                (body["request_id"], fingerprint, json.dumps(body, sort_keys=True), "approved"),
            )
        return {"status": "approved", "approval_id": body["request_id"]}

    def admit(self, request: dict[str, Any], actor_uid: int) -> dict[str, Any]:
        if actor_uid != self.worker_uid or actor_uid == 0:
            raise Denied("development_admission_peer")
        required = {
            "task_id",
            "approval_id",
            "source_sha",
            "parameters_sha256",
            "allowed_paths",
            "provider",
            "model",
            "max_calls",
            "max_cost_usd",
            "deadline_seconds",
        }
        if set(request) != required:
            raise Denied("development_admission_schema")
        row = self.ledger.db.execute(
            "SELECT payload,status,receipt FROM development_admissions WHERE approval_id=?",
            (request["approval_id"],),
        ).fetchone()
        if row is None or row[1] not in {"approved", "admitted"}:
            raise Denied("development_approval_missing")
        approval = json.loads(row[0])
        for key, value in request.items():
            approval_key = "request_id" if key == "approval_id" else key
            if approval.get(approval_key) != value:
                raise Denied("development_admission_mismatch")
        if row[1] == "admitted":
            return json.loads(row[2])
        if approval["expires_at"] <= int(time.time()):
            raise Denied("development_approval_expired")
        active_generation = self._guardian_active(approval["guardian_generation"])
        if active_generation is None:
            raise Denied("guardian_not_active")
        receipt = {
            **request,
            "status": "approved",
            "authority": "jarvis-autonomy-broker",
            "expires_at": approval["expires_at"],
            "guardian_generation": active_generation,
        }
        with self.ledger.db:
            self.ledger.event(
                "completed",
                approval["request_id"],
                actor_uid,
                approval["task_id"],
                "admit_development",
                approval["source_sha"],
                hashlib.sha256(canonical(receipt)).hexdigest(),
            )
            self.ledger.db.execute(
                "UPDATE development_admissions SET status='admitted',receipt=? WHERE approval_id=?",
                (json.dumps(receipt, sort_keys=True), approval["request_id"]),
            )
        return receipt

    def _guardian_active(self, generation: str) -> str | None:
        try:
            status = self.guardian()
        except Exception:
            # Observation failure is an authentication failure.  Never allow
            # an unavailable Guardian to authorize a development admission.
            return None
        if status.get("trusted") is not True or status.get("state") != "ACTIVE":
            return None
        if status.get("generation") == generation:
            return str(status["generation"])
        # Guardian rotates its generation on STOPPED -> ACTIVE.  The signed
        # approval binds the pre-activation generation; accepting only the
        # immediately preceding generation preserves that binding while
        # rejecting stale approvals after any later STOP/ACTIVE cycle.
        if status.get("previous_generation") == generation:
            return str(status["generation"])
        return None


def serve_admission_connection(
    connection: socket.socket, broker: DevelopmentAdmissionBroker
) -> None:
    """Handle one already-accepted worker connection; bounded and fail-closed."""
    try:
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", credentials)
        header = _recv_exact(connection, 4)
        if len(header) != 4:
            raise Denied("development_admission_frame")
        size = struct.unpack("!I", header)[0]
        if size <= 0 or size > MAX_FRAME:
            raise Denied("development_admission_size")
        payload = _recv_exact(connection, size)
        if len(payload) != size:
            raise Denied("development_admission_frame")
        body = decode_request(payload)
        if body.get("action") == "approve_development":
            result = broker.approve(payload, uid)
        else:
            result = broker.admit(body, uid)
        encoded = canonical(result)
    except Exception as exc:
        encoded = canonical({"error": getattr(exc, "code", "development_admission_denied")})
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
