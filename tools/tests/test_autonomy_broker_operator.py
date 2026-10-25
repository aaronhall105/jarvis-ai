import base64
import hashlib
import os
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import Denied, Ledger, canonical
from tools.autonomy_broker_operator import OperatorDispatcher, verify_operator


@pytest.fixture
def signed():
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    body = {
        "version": 1,
        "action": "resume",
        "key_id": key_id,
        "request_id": str(uuid.uuid4()),
        "generation": str(uuid.uuid4()),
        "expires_at": int(time.time()) + 30,
    }
    body["signature"] = base64.b64encode(
        key.sign(b"jarvis-autonomy-control-v1\n" + canonical(body), ec.ECDSA(hashes.SHA256()))
    ).decode()
    return body, {key_id: public}


def check(body, keys, uid=1000):
    return verify_operator(
        body,
        peer_uid=uid,
        autonomy_uid=995,
        relay_uid=1000,
        public_keys=keys,
        now=time.time(),
        current_generation=body["generation"],
    )


def test_enrolled_operator_signature_required(signed):
    body, keys = signed
    assert check(body, keys)["action"] == "resume"
    with pytest.raises(Denied):
        check(body, {})


@pytest.mark.parametrize("uid", [995, 0, 1001, -1])
def test_worker_or_external_peer_cannot_use_operator_route(signed, uid):
    with pytest.raises(Denied, match="operator_peer_denied"):
        check(*signed, uid=uid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", "stop"),
        ("request_id", str(uuid.uuid4())),
        ("signature", "not base64!"),
        ("expires_at", 0),
        ("version", True),
    ],
)
def test_modified_or_expired_operator_request_rejected(signed, field, value):
    body, keys = signed
    body[field] = value
    with pytest.raises(Denied):
        check(body, keys)


def test_mobile_token_is_not_operator_authority(signed):
    body, keys = signed
    body["authorization"] = "Bearer fixture-not-a-secret"
    with pytest.raises(Denied, match="operator_schema"):
        check(body, keys)


def test_generation_rotation_invalidates_signed_resume(signed):
    body, keys = signed
    with pytest.raises(Denied, match="operator_stale_generation"):
        verify_operator(
            body,
            peer_uid=1000,
            autonomy_uid=995,
            relay_uid=1000,
            public_keys=keys,
            now=time.time(),
            current_generation=str(uuid.uuid4()),
        )


@pytest.fixture
def operator(tmp_path, signed):
    body, keys = signed
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    ledger = Ledger(private / "operator.db", os.getuid())
    state = {"trusted": True, "state": "STOPPED", "generation": body["generation"]}
    calls = []

    def stop(actor):
        calls.append(("stop", actor))
        state.update(state="STOPPED", generation=str(uuid.uuid4()))

    def resume(actor, expected_generation):
        assert expected_generation == state["generation"]
        calls.append(("resume", actor))
        state.update(state="ACTIVE", generation=str(uuid.uuid4()))

    dispatcher = OperatorDispatcher(
        ledger,
        relay_uid=1000,
        autonomy_uid=995,
        public_keys=keys,
        guardian=lambda: dict(state),
        stop=stop,
        resume=resume,
    )
    yield dispatcher, state, calls
    ledger.close()


def test_signed_resume_replay_cannot_undo_later_stop(operator, signed, monkeypatch):
    dispatcher, state, calls = operator
    raw = canonical(signed[0])
    result = dispatcher.dispatch(raw, 1000)
    assert result["state"] == "ACTIVE"
    dispatcher.stop(1000)
    monkeypatch.setattr(
        "tools.autonomy_broker_operator.time.time", lambda: signed[0]["expires_at"] + 500
    )
    assert dispatcher.dispatch(raw, 1000) == result  # Historical receipt only.
    assert state["state"] == "STOPPED"
    assert calls == [("resume", 1000), ("stop", 1000)]


def test_operator_unknown_transition_cannot_replay(operator, signed):
    dispatcher, _, _ = operator
    effects = []

    def lost_acknowledgement(*_):
        effects.append(True)
        raise OSError("simulated crash")

    dispatcher.resume = lost_acknowledgement
    with pytest.raises(OSError):
        dispatcher.dispatch(canonical(signed[0]), 1000)
    with pytest.raises(Denied, match="reconciliation_required"):
        dispatcher.dispatch(canonical(signed[0]), 1000)
    assert effects == [True]


def test_operator_reserve_failure_prevents_resume(operator, signed, monkeypatch):
    dispatcher, _, calls = operator
    monkeypatch.setattr(
        dispatcher.ledger, "event", lambda *_: (_ for _ in ()).throw(OSError("audit failed"))
    )
    with pytest.raises(OSError):
        dispatcher.dispatch(canonical(signed[0]), 1000)
    assert calls == []


def test_operator_does_not_report_unconfirmed_resume(operator, signed):
    dispatcher, _, _ = operator
    dispatcher.resume = lambda *_: None
    with pytest.raises(Denied, match="operator_transition_unconfirmed"):
        dispatcher.dispatch(canonical(signed[0]), 1000)


def test_worker_cannot_obtain_completed_operator_receipt(operator, signed):
    dispatcher, _, _ = operator
    dispatcher.dispatch(canonical(signed[0]), 1000)
    with pytest.raises(Denied, match="operator_peer_denied"):
        dispatcher.dispatch(canonical(signed[0]), 995)
