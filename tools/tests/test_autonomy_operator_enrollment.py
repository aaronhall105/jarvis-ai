"""Root-free enrollment/revocation rehearsal; never changes live host policy."""

import base64
import copy
import json
import os
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from tools import enroll_autonomy_operator as enrollment
from tools.autonomy_broker import Denied
from tools.autonomy_broker_operator import verify_operator


def bundle_for(key):
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    body = {
        "version": 1,
        "nonce": str(uuid.uuid4()),
        "key_id": enrollment.digest(public),
        "public_key": base64.b64encode(public).decode(),
    }
    body["signature"] = base64.b64encode(
        key.sign(
            b"jarvis-autonomy-enrollment-v1\n" + enrollment.canonical(body),
            ec.ECDSA(hashes.SHA256()),
        )
    ).decode()
    return body


@pytest.fixture
def bundle():
    return bundle_for(ec.generate_private_key(ec.SECP256R1()))


def test_public_proof_accepted_without_private_material(bundle):
    assert enrollment.validate_bundle(enrollment.canonical(bundle)) == bundle
    assert set(bundle) == {"version", "nonce", "key_id", "public_key", "signature"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("key_id", "a" * 64),
        ("nonce", "not-uuid"),
        ("public_key", "broken"),
        ("signature", "bad"),
        ("signature", "a" * 129),
        ("signature", None),
        ("public_key", {}),
        ("nonce", "00000000-0000-4000-8000-000000000001"),
    ],
)
def test_malformed_or_modified_public_request_rejected(bundle, field, value):
    bundle[field] = value
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.validate_bundle(enrollment.canonical(bundle))


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{", b"x" * 4097, b'{"version":1,"version":1}'])
def test_strict_bounded_input(raw):
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.validate_bundle(raw)


def test_private_or_command_field_is_not_accepted(bundle):
    for field in ("private_key", "command", "resume", "operator_uid"):
        value = {**bundle, field: "never authority"}
        with pytest.raises(enrollment.EnrollmentDenied):
            enrollment.validate_bundle(enrollment.canonical(value))


@pytest.mark.parametrize("curve", [ec.SECP384R1(), ec.SECP256K1()])
def test_wrong_curve_rejected(curve):
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.validate_bundle(enrollment.canonical(bundle_for(ec.generate_private_key(curve))))


def test_wrong_key_type_rejected(bundle):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    bundle.update(public_key=base64.b64encode(public).decode(), key_id=enrollment.digest(public))
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.validate_bundle(enrollment.canonical(bundle))


@pytest.mark.parametrize(
    "euid,uid,user,tty",
    [
        (1000, "1000", "aaron", True),  # manual Developer still unprivileged
        (995, "995", "jarvis-autonomy", True),
        (0, "995", "jarvis-autonomy", True),
        (0, "1001", "external", True),
        (0, "1000", "aaron", False),  # no daemon / unattended enrollment
        (0, "", "", True),
    ],
)
def test_unprivileged_or_unattended_enrollment_denied(euid, uid, user, tty):
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.operator_authority(
            euid, uid, user, tty, {"operator_uid": 1000, "autonomy_uid": 995}
        )


def test_local_authenticated_operator_authority():
    enrollment.operator_authority(
        0, "1000", "aaron", True, {"operator_uid": 1000, "autonomy_uid": 995}
    )


def test_initial_enrollment_and_repeat_are_exact(bundle):
    keys = enrollment.revised_keys({}, "enroll", "", bundle)
    assert keys == {bundle["key_id"]: bundle["public_key"]}
    assert enrollment.revised_keys(keys, "enroll", "", bundle) == keys


def test_second_key_cannot_silently_replace_first(bundle):
    keys = enrollment.revised_keys({}, "enroll", "", bundle)
    second = bundle_for(ec.generate_private_key(ec.SECP256R1()))
    with pytest.raises(enrollment.EnrollmentDenied, match="explicit_replacement"):
        enrollment.revised_keys(keys, "enroll", "", second)
    assert enrollment.revised_keys(keys, "replace", bundle["key_id"], second) == {
        second["key_id"]: second["public_key"]
    }
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.revised_keys(keys, "replace", "0" * 64, second)


def test_revoked_key_cannot_resume():
    key = ec.generate_private_key(ec.SECP256R1())
    bundle = bundle_for(key)
    keys = enrollment.revised_keys(
        {bundle["key_id"]: bundle["public_key"]}, "revoke", bundle["key_id"], None
    )
    assert keys == {}
    body = {
        "version": 1,
        "action": "resume",
        "key_id": bundle["key_id"],
        "request_id": str(uuid.uuid4()),
        "generation": str(uuid.uuid4()),
        "expires_at": 1030,
    }
    body["signature"] = base64.b64encode(
        key.sign(
            b"jarvis-autonomy-control-v1\n" + enrollment.canonical(body), ec.ECDSA(hashes.SHA256())
        )
    ).decode()
    with pytest.raises(Denied, match="operator_key_not_enrolled"):
        verify_operator(
            body,
            peer_uid=1000,
            autonomy_uid=995,
            relay_uid=1000,
            public_keys=keys,
            now=1000,
            current_generation=body["generation"],
        )


@pytest.fixture
def originals():
    policy = enrollment.canonical(
        {"version": 1, "operator_public_keys": {}, "unchanged": "fixed release policy"}
    )
    broker = enrollment.canonical(
        {
            "bundle": "trusted bundle",
            "files": {
                str(enrollment.POLICY): enrollment.digest(policy),
                "/fixed/code": "code hash",
            },
        }
    )
    guardian = enrollment.canonical(
        {
            "files": {
                str(enrollment.POLICY): enrollment.digest(policy),
                str(enrollment.BROKER_MARKER): enrollment.digest(broker),
                "/fixed/guardian": "guardian hash",
            }
        }
    )
    return {
        enrollment.POLICY: policy,
        enrollment.BROKER_MARKER: broker,
        enrollment.GUARDIAN_MARKER: guardian,
    }


def test_plan_preserves_all_non_enrollment_policy_and_payload(originals, bundle):
    plan = enrollment.replacement_plan(originals, {bundle["key_id"]: bundle["public_key"]})
    assert set(plan) == set(originals)
    policy = json.loads(plan[enrollment.POLICY])
    assert policy["unchanged"] == "fixed release policy"
    broker = json.loads(plan[enrollment.BROKER_MARKER])
    guardian = json.loads(plan[enrollment.GUARDIAN_MARKER])
    assert broker["bundle"] == "trusted bundle"
    assert broker["files"]["/fixed/code"] == "code hash"
    assert guardian["files"]["/fixed/guardian"] == "guardian hash"
    assert broker["files"][str(enrollment.POLICY)] == enrollment.digest(plan[enrollment.POLICY])
    assert guardian["files"][str(enrollment.BROKER_MARKER)] == enrollment.digest(
        plan[enrollment.BROKER_MARKER]
    )


def test_unpinned_policy_refused(originals):
    originals[enrollment.POLICY] += b" "
    with pytest.raises(enrollment.EnrollmentDenied):
        enrollment.replacement_plan(originals, {})


@pytest.mark.parametrize(
    "failure", [None, "audit", "save", "first_write", "second_write", "verify"]
)
def test_transaction_rehearsal_failures_leave_ipc_closed_and_originals_safe(
    originals, bundle, failure
):
    filesystem = dict(originals)
    plan = enrollment.replacement_plan(originals, {bundle["key_id"]: bundle["public_key"]})
    events, journals = [], []
    writes = 0
    failed = False

    def fault(at):
        nonlocal failed
        if failure == at and not failed:
            failed = True
            raise OSError("simulated failure")

    def write(path, raw):
        nonlocal writes
        writes += 1
        fault("first_write" if writes == 1 else "second_write" if writes == 2 else "later_write")
        assert events[0] == "closed" and journals[0]["status"] == "pending"
        filesystem[path] = raw

    def save(value):
        fault("save")
        journals.append(copy.deepcopy(value))

    def audit(*_):
        fault("audit")

    def verify():
        fault("verify")

    def run():
        enrollment.transaction(
            originals,
            plan,
            save=save,
            write=write,
            audit=audit,
            verify=verify,
            quiesce=lambda: events.append("closed"),
            reopen=lambda: events.append("opened"),
        )

    if failure:
        with pytest.raises(OSError):
            run()
        assert filesystem == originals
        assert events == ["closed"]
    else:
        run()
        assert filesystem == plan
        assert journals[-1]["status"] == "completed"
        assert events == ["closed", "opened"]


def test_uncertain_restore_keeps_pending_journal(originals):
    journal = []

    def fail(*_):
        raise OSError("interrupted write/restore")

    with pytest.raises(OSError):
        enrollment.transaction(
            originals,
            originals,
            save=journal.append,
            write=fail,
            verify=lambda: None,
            audit=lambda *_: None,
            quiesce=lambda: None,
            reopen=lambda: pytest.fail("must stay closed"),
        )
    assert journal[-1]["status"] == "pending"


def test_atomic_replacement_rehearsal_modes(tmp_path):
    target = tmp_path / "public-policy.json"
    enrollment.replace(target, b"public", 0o644)
    assert target.read_bytes() == b"public" and target.stat().st_mode & 0o777 == 0o644
    journal = tmp_path / "protected-journal.json"
    enrollment.replace(journal, b"record", 0o600)
    assert journal.stat().st_mode & 0o777 == 0o600
    assert journal.stat().st_uid == os.getuid()  # Production EUID is root.


def test_no_new_enrollment_sudo_grant_or_ipc_action():
    root = Path(__file__).resolve().parents[2]
    assert "enroll" not in (root / "tools/autonomy_guardian_cli.sh").read_text()
    assert "enroll" not in (root / "tools/autonomy_broker_runtime.py").read_text()
    assert (
        "getPrivateKey().getEncoded"
        not in (
            root
            / "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/AutonomyControl.java"
        ).read_text()
    )


def test_real_filesystem_guardian_manifest_rehearsal(tmp_path, monkeypatch, bundle):
    from tools.autonomy_guardian import Guardian
    from tools.autonomy_broker_store import Records

    guardian_base = tmp_path / "guardian"
    guardian_base.mkdir(mode=0o700)
    guardian_policy = tmp_path / "guardian.json"
    enrollment.replace(
        guardian_policy,
        enrollment.canonical(
            {
                "version": 1,
                "operator_uid": 1000,
                "autonomy_account": "jarvis-autonomy",
                "privileged_operations": [],
            }
        ),
    )
    policy = tmp_path / "broker.json"
    broker_marker = tmp_path / "broker-installed.json"
    guardian_marker = guardian_base / "installed.json"
    for name, value in (
        ("POLICY", policy),
        ("BROKER_MARKER", broker_marker),
        ("GUARDIAN_MARKER", guardian_marker),
    ):
        monkeypatch.setattr(enrollment, name, value)
    enrollment.replace(
        policy, enrollment.canonical({"operator_public_keys": {}, "fixed_profile": "unchanged"})
    )
    enrollment.replace(
        broker_marker,
        enrollment.canonical(
            {"bundle": "unchanged", "files": {str(policy): enrollment.digest(policy.read_bytes())}}
        ),
    )
    enrollment.replace(
        guardian_marker,
        enrollment.canonical(
            {
                "files": {
                    str(guardian_policy): enrollment.digest(guardian_policy.read_bytes()),
                    str(policy): enrollment.digest(policy.read_bytes()),
                    str(broker_marker): enrollment.digest(broker_marker.read_bytes()),
                }
            }
        ),
    )
    guardian = Guardian(guardian_base, guardian_policy, os.getuid(), False)
    guardian.write_state("STOPPED")
    original_state = (guardian_base / "state.json").read_bytes()
    records_path = tmp_path / "records"
    records_path.mkdir(mode=0o700)
    records = Records(records_path, os.getuid(), False)
    assert guardian.status()["trusted"] is True
    events = []

    def verify():
        assert guardian.status()["trusted"] is True
        assert guardian.status()["state"] == "STOPPED"
        for name, digest in json.loads(broker_marker.read_bytes())["files"].items():
            assert enrollment.digest(Path(name).read_bytes()) == digest

    for keys in ({bundle["key_id"]: bundle["public_key"]}, {}):
        originals = {p: p.read_bytes() for p in (policy, broker_marker, guardian_marker)}
        enrollment.transaction(
            originals,
            enrollment.replacement_plan(originals, keys),
            save=lambda value: enrollment.replace(
                records_path / "operator-enrollment.json", enrollment.canonical(value), 0o600
            ),
            write=enrollment.replace,
            verify=verify,
            audit=records.audit,
            quiesce=lambda: events.append("closed"),
            reopen=lambda: events.append("opened"),
        )
        verify()
        assert json.loads(policy.read_bytes())["operator_public_keys"] == keys
        assert (guardian_base / "state.json").read_bytes() == original_state
    assert events == ["closed", "opened", "closed", "opened"]
    assert len((records_path / "audit.jsonl").read_text().splitlines()) == 4


@pytest.mark.parametrize("status", ["pending", "unknown", "running"])
def test_crash_journal_blocks_replay(tmp_path, monkeypatch, status):
    pending = tmp_path / "operator-enrollment.json"
    enrollment.replace(pending, enrollment.canonical({"status": status}), 0o600)
    monkeypatch.setattr(enrollment, "protected", lambda *_: None)  # Root-free filesystem owner.
    with pytest.raises(enrollment.EnrollmentDenied, match="reconciliation_required"):
        enrollment.check_journal(pending)


def test_shared_android_python_public_vectors():
    path = (
        Path(__file__).resolve().parents[2]
        / "android/jarvis-voice-client/app/src/test/resources/autonomy_operator_vectors.json"
    )
    vectors = json.loads(path.read_bytes())
    body = json.loads(vectors["enrollment"]["unsigned"])
    body["signature"] = vectors["enrollment"]["signature"]
    assert enrollment.validate_bundle(enrollment.canonical(body)) == body
    body = json.loads(vectors["control"]["unsigned"])
    body["signature"] = vectors["control"]["signature"]
    assert (
        verify_operator(
            body,
            peer_uid=1000,
            autonomy_uid=995,
            relay_uid=1000,
            public_keys={body["key_id"]: base64.b64decode(vectors["public_key"])},
            now=2000000000,
            current_generation=body["generation"],
        )["action"]
        == "stop"
    )
