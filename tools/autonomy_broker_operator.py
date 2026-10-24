"""Operator-signed control verification; no shared Core token grants resume.

Public keys must be enrolled through a separately reviewed local operator path.
No key enrollment or policy editing is accepted over the worker/relay protocol.
"""

import base64
import hashlib
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import Denied, Ledger, Request, canonical, decode_request, identifier

FIELDS = {"version", "action", "key_id", "request_id", "expires_at", "generation", "signature"}


def verify_operator(
    body: dict[str, Any],
    *,
    peer_uid: int,
    autonomy_uid: int,
    relay_uid: int,
    public_keys: dict[str, bytes],
    now: float,
    current_generation: str,
) -> dict[str, Any]:
    # An enrolled key alone is not a worker capability. The worker socket never
    # becomes an operator route, including when a captured request is presented.
    if peer_uid != relay_uid or peer_uid == autonomy_uid:
        raise Denied("operator_peer_denied")
    if set(body) != FIELDS or type(body["version"]) is not int or body["version"] != 1:
        raise Denied("operator_schema")
    if body["action"] not in {"stop", "resume", "status"}:
        raise Denied("operator_action")
    identifier(body["request_id"])
    identifier(body["generation"])
    if body["generation"] != current_generation:
        raise Denied("operator_stale_generation")
    if type(body["expires_at"]) is not int or not now < body["expires_at"] <= now + 60:
        raise Denied("operator_request_expired")
    key_id = body["key_id"]
    if not isinstance(key_id, str) or key_id not in public_keys:
        raise Denied("operator_key_not_enrolled")
    encoded = public_keys[key_id]
    if hashlib.sha256(encoded).hexdigest() != key_id:
        raise Denied("operator_key_integrity")
    try:
        verifier = serialization.load_der_public_key(encoded)
        if not isinstance(verifier, ec.EllipticCurvePublicKey) or not isinstance(
            verifier.curve, ec.SECP256R1
        ):
            raise Denied("operator_key_type")
        signature = body["signature"]
        if not isinstance(signature, str) or len(signature) > 128:
            raise Denied("operator_signature")
        raw = base64.b64decode(signature, validate=True)
        signed = {k: v for k, v in body.items() if k != "signature"}
        verifier.verify(
            raw, b"jarvis-autonomy-control-v1\n" + canonical(signed), ec.ECDSA(hashes.SHA256())
        )
    except (ValueError, TypeError, InvalidSignature):
        raise Denied("operator_signature") from None
    # A production dispatcher must atomically reserve request_id before invoking
    # the Guardian. Crash between reservation and invocation requires reconciliation.
    return signed


class OperatorDispatcher:
    """Signed actions with durable nonce binding, separate from worker admission.

    The installed adapter provides only fixed Guardian status/stop/resume calls.
    No model/worker callback, command, service name or caller UID is accepted.
    Public-key enrollment is intentionally NOT an IPC action.
    """

    def __init__(
        self,
        ledger: Ledger,
        *,
        relay_uid: int,
        autonomy_uid: int,
        public_keys: dict[str, bytes],
        guardian,
        stop,
        resume,
    ):
        self.ledger = ledger
        self.relay_uid, self.autonomy_uid = relay_uid, autonomy_uid
        self.public_keys = public_keys
        self.guardian, self.stop, self.resume = guardian, stop, resume

    def dispatch(self, raw: bytes, actor: int) -> dict[str, Any]:
        if actor != self.relay_uid or actor == self.autonomy_uid:
            raise Denied("operator_peer_denied")
        body = decode_request(raw)
        # Fingerprint includes the signature itself. Replaying a completed
        # RESUME after a later STOP returns its old receipt, never resumes again.
        receipt = Request(
            {
                "request_id": body.get("request_id"),
                "goal_id": body.get("request_id"),
                "source_sha": "0" * 40,
                "operation": "operator_control",
                "signed_request": body,
            }
        )
        prior = self.ledger.completed(receipt.body, actor)
        if prior is not None:
            return prior
        state = self.guardian()
        if state.get("trusted") is not True:
            # Independent host CLI STOP remains available if protected policy or
            # audit is damaged. No remote RESUME can repair its own authority.
            raise Denied("operator_guardian_untrusted")
        signed = verify_operator(
            body,
            peer_uid=actor,
            relay_uid=self.relay_uid,
            autonomy_uid=self.autonomy_uid,
            public_keys=self.public_keys,
            now=time.time(),
            current_generation=state["generation"],
        )
        prior = self.ledger.reserve(receipt, actor)
        if prior is not None:
            return prior
        try:
            # Check again immediately before transition. Guardian stop/resume
            # adapters must serialize against its own protected control lock.
            current = self.guardian()
            if (
                current.get("trusted") is not True
                or current.get("generation") != signed["generation"]
            ):
                raise Denied("operator_stale_generation")
            if signed["action"] == "stop":
                self.stop(actor)
            elif signed["action"] == "resume":
                self.resume(actor, signed["generation"])
            after = self.guardian()
            if after.get("trusted") is not True:
                raise Denied("operator_guardian_untrusted")
            wanted = {"stop": "STOPPED", "resume": "ACTIVE"}.get(signed["action"])
            if wanted is not None and after.get("state") != wanted:
                raise Denied("operator_transition_unconfirmed")
            result = {"state": after["state"], "generation": after["generation"], "trusted": True}
        except BaseException:
            self.ledger.finish(receipt, actor, None)
            raise
        self.ledger.finish(receipt, actor, result)
        return result
