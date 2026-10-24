"""Reviewed local operator ceremony; never an IPC/Developer/Guardian operation.

Run via the adjacent isolated-Python shell wrapper. No credential is accepted.
Only a public P-256 identity and its proof of possession are read from the TTY.
Installed Guardian/broker code is reused, not reinstalled. Interrupted metadata
transactions remain fail-closed and require explicit operator reconciliation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

BASE = Path("/var/lib/jarvis-autonomy-broker")
POLICY = Path("/etc/jarvis-autonomy/broker.json")
BROKER_MARKER = BASE / "installed.json"
GUARDIAN_MARKER = Path("/var/lib/jarvis-autonomy-guardian/installed.json")
LIB = Path("/usr/local/libexec/jarvis-autonomy-broker")
SOCKET = "jarvis-autonomy-broker-operator.socket"
SERVICE = "jarvis-autonomy-broker-operator.service"


class EnrollmentDenied(ValueError):
    pass


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def key_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{64}", value) is None:
        raise EnrollmentDenied("invalid_fingerprint")
    return value


def operator_authority(euid: int, sudo_uid: str, sudo_user: str, tty: bool, policy: dict) -> None:
    if (
        euid != 0
        or sudo_user != "aaron"
        or sudo_uid != str(policy["operator_uid"])
        or policy["operator_uid"] == policy["autonomy_uid"]
        or not tty
    ):
        raise EnrollmentDenied("interactive_local_operator_sudo_required")


def validate_bundle(raw: bytes) -> dict:
    if len(raw) > 4096:
        raise EnrollmentDenied("public_request_too_large")

    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise EnrollmentDenied("duplicate_field")
            result[name] = value
        return result

    try:
        body = json.loads(raw, object_pairs_hook=unique)
        if (
            not isinstance(body, dict)
            or set(body) != {"version", "key_id", "nonce", "public_key", "signature"}
            or type(body["version"]) is not int
            or body["version"] != 1
            or str(uuid.UUID(body["nonce"])) != body["nonce"]
        ):
            raise EnrollmentDenied("public_request_schema")
        key_id(body["key_id"])
        encoded = base64.b64decode(body["public_key"], validate=True)
        if len(encoded) != 91 or digest(encoded) != body["key_id"]:
            raise EnrollmentDenied("public_key_fingerprint")
        public = serialization.load_der_public_key(encoded)
        if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(
            public.curve, ec.SECP256R1
        ):
            raise EnrollmentDenied("public_key_type")
        if (
            public.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            != encoded
        ):
            raise EnrollmentDenied("public_key_encoding")
        if not isinstance(body["signature"], str) or len(body["signature"]) > 128:
            raise EnrollmentDenied("public_proof_invalid")
        signature = base64.b64decode(body["signature"], validate=True)
        unsigned = {k: v for k, v in body.items() if k != "signature"}
        public.verify(
            signature,
            b"jarvis-autonomy-enrollment-v1\n" + canonical(unsigned),
            ec.ECDSA(hashes.SHA256()),
        )
        return body
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, InvalidSignature):
        raise EnrollmentDenied("invalid_public_enrollment_request") from None


def revised_keys(existing: dict, action: str, previous: str, bundle: dict | None) -> dict:
    if action == "enroll" and bundle is not None and not previous:
        if existing and existing != {bundle["key_id"]: bundle["public_key"]}:
            raise EnrollmentDenied("explicit_replacement_required")
        return {bundle["key_id"]: bundle["public_key"]}
    if action in {"replace", "revoke"} and set(existing) == {key_id(previous)}:
        if action == "revoke" and bundle is None:
            return {}
        if action == "replace" and bundle is not None and bundle["key_id"] != previous:
            return {bundle["key_id"]: bundle["public_key"]}
    raise EnrollmentDenied("unknown_action_or_previous_identity")


def replacement_plan(originals: dict[Path, bytes], keys: dict) -> dict[Path, bytes]:
    policy = json.loads(originals[POLICY])
    broker = json.loads(originals[BROKER_MARKER])
    guardian = json.loads(originals[GUARDIAN_MARKER])
    if (
        broker["files"].get(str(POLICY)) != digest(originals[POLICY])
        or guardian["files"].get(str(POLICY)) != digest(originals[POLICY])
        or guardian["files"].get(str(BROKER_MARKER)) != digest(originals[BROKER_MARKER])
    ):
        raise EnrollmentDenied("policy_not_integrity_pinned")
    policy["operator_public_keys"] = keys
    new_policy = canonical(policy)
    broker["files"][str(POLICY)] = digest(new_policy)
    new_broker = canonical(broker)
    guardian["files"][str(POLICY)] = digest(new_policy)
    guardian["files"][str(BROKER_MARKER)] = digest(new_broker)
    return {POLICY: new_policy, BROKER_MARKER: new_broker, GUARDIAN_MARKER: canonical(guardian)}


def protected(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if (
        info.st_uid != 0
        or info.st_mode & 0o022
        or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
    ):
        raise EnrollmentDenied("unprotected_host_path")
    for parent in path.parents:
        info = parent.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise EnrollmentDenied("unprotected_host_parent")


def verify_manifests() -> None:
    for marker in (GUARDIAN_MARKER, BROKER_MARKER):
        protected(marker)
        for name, expected in json.loads(marker.read_bytes())["files"].items():
            path = Path(name)
            protected(path)
            if digest(path.read_bytes()) != expected:
                raise EnrollmentDenied("protected_integrity_failed")


def replace(path: Path, raw: bytes, mode: int = 0o644) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".operator-enrollment-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def transaction(originals, replacements, *, save, write, verify, audit, quiesce, reopen) -> None:
    """Callbacks are test seams, never CLI arguments. Failures leave IPC closed.

    A durable pending journal precedes any policy mutation. Power loss cannot
    silently resume a partial operation; a later invocation refuses that journal.
    """
    request = str(uuid.uuid4())
    change_hash = digest(canonical({str(k): digest(v) for k, v in replacements.items()}))
    quiesce()
    audit("operator_enrollment_requested", request, change_hash)
    journal = {
        "request_id": request,
        "status": "pending",
        "originals": {str(k): base64.b64encode(v).decode() for k, v in originals.items()},
        "target_hashes": {str(k): digest(v) for k, v in replacements.items()},
    }
    save(journal)
    try:
        for path, raw in replacements.items():
            write(path, raw)
        verify()
        audit("operator_enrollment_completed", request, change_hash)
        save({**journal, "status": "completed"})
    except BaseException:
        # If restore or audit fails too, pending remains and IPC stays closed.
        for path, raw in originals.items():
            write(path, raw)
        verify()
        audit("operator_enrollment_restored", request, change_hash)
        save({**journal, "status": "restored"})
        raise
    reopen()


def control_services(action: str) -> None:
    if action == "close":
        arguments = ["stop", SOCKET, SERVICE]
    elif action == "open":
        arguments = ["start", SOCKET]  # Socket activation loads the new verified policy.
    else:
        raise EnrollmentDenied("unsupported_service_action")
    result = subprocess.run(
        ["/usr/bin/systemctl", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    if result.returncode:
        raise EnrollmentDenied("operator_socket_reconciliation_required")


def check_journal(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    protected(path)
    journal = json.loads(path.read_bytes())
    if journal["status"] not in {"completed", "restored"}:
        raise EnrollmentDenied("interrupted_enrollment_operator_reconciliation_required")
    return journal["status"] == "completed"


def main(arguments: list[str]) -> int:
    if not sys.flags.isolated or os.geteuid() != 0:
        raise EnrollmentDenied("reviewed_local_sudo_wrapper_required")
    if not (
        arguments == ["enroll"] or (len(arguments) == 2 and arguments[0] in {"replace", "revoke"})
    ):
        raise EnrollmentDenied("use_enroll_or_replace_revoke_exact_old_fingerprint")
    verify_manifests()
    policy = json.loads(POLICY.read_bytes())
    operator_authority(
        os.geteuid(),
        os.getenv("SUDO_UID", ""),
        os.getenv("SUDO_USER", ""),
        sys.stdin.isatty() and sys.stdout.isatty(),
        policy,
    )
    # Only the root-protected installed package is imported by this ceremony.
    protected(LIB, directory=True)
    sys.path.insert(0, str(LIB))
    from tools.autonomy_broker_runtime import installed_policy
    from tools.autonomy_broker_store import Records
    from tools.autonomy_guardian import Guardian

    controller = Guardian()
    pending = BASE / "records/operator-enrollment.json"
    check_journal(pending)
    bundle = None
    if arguments[0] != "revoke":
        print("Paste the PUBLIC enrollment request from your phone here, locally (one line):")
        bundle = validate_bundle(sys.stdin.buffer.readline(4097))
    confirmation = bundle["key_id"] if bundle else key_id(arguments[1])
    print("Compare the fingerprint with your phone. For revocation, confirm the old identity.")
    print("Type the exact fingerprint to confirm (not a password):")
    if sys.stdin.readline(66).strip() != confirmation:
        raise EnrollmentDenied("fingerprint_confirmation_failed")
    with controller.lock():
        completed_journal = check_journal(pending)  # Serialize concurrent operator ceremonies.
        installed_policy()
        controller.integrity()
        if controller.state()["state"] != "STOPPED":
            raise EnrollmentDenied("guardian_must_remain_stopped")
        originals = {p: p.read_bytes() for p in (POLICY, BROKER_MARKER, GUARDIAN_MARKER)}
        policy = json.loads(originals[POLICY])
        keys = revised_keys(
            policy["operator_public_keys"],
            arguments[0],
            arguments[1] if len(arguments) == 2 else "",
            bundle,
        )
        if keys == policy["operator_public_keys"]:
            if completed_journal:
                # Recover a crash after commit but before reopening the socket.
                # This only enables signed IPC, never autonomous development.
                control_services("open")
            print("Exact operator public identity already enrolled; autonomy remains STOPPED.")
            return 0
        records = Records(BASE / "records")

        def verify():
            installed_policy()
            controller.integrity()
            if controller.state()["state"] != "STOPPED":
                raise EnrollmentDenied("guardian_state_changed")

        transaction(
            originals,
            replacement_plan(originals, keys),
            save=lambda value: replace(pending, canonical(value), 0o600),
            write=replace,
            verify=verify,
            audit=records.audit,
            quiesce=lambda: control_services("close"),
            reopen=lambda: control_services("open"),
        )
    print(
        "Operator public identity updated; autonomy STOPPED. No private key or credential copied."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception:
        # Do not emit input, paths from malformed data, keys, or tracebacks.
        print(
            "Enrollment refused or incomplete. Autonomy was not enabled. Inspect the protected transaction before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
