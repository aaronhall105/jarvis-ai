"""Signed HTTP/relay/broker contract with temporary state, never live activation."""

import base64
import hashlib
import json
import os
import struct
import time
import uuid
from contextlib import asynccontextmanager

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from developer_gateway import autonomy_api, autonomy_host
from developer_gateway.autonomy import Governor
from tools.autonomy_broker import Ledger, canonical
from tools.autonomy_broker_operator import OperatorDispatcher


def test_signed_stop_round_trip_and_phase1_resume_denied(tmp_path, monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    state = {"trusted": True, "state": "STOPPED", "generation": str(uuid.uuid4())}
    transitions = []

    def transition(value):
        transitions.append(value)
        state.update(state=value, generation=str(uuid.uuid4()))

    def dispatch(raw):
        # Reopen the persistent ledger on the transport thread, like a service
        # restart between requests; never share a SQLite handle across threads.
        ledger = Ledger(tmp_path / "operator.sqlite3", os.getuid())
        try:
            return OperatorDispatcher(
                ledger,
                relay_uid=1000,
                autonomy_uid=995,
                public_keys={key_id: public},
                guardian=lambda: dict(state),
                stop=lambda _: transition("STOPPED"),
                resume=lambda *_: transition("ACTIVE"),
            ).dispatch(raw, 1000)
        finally:
            ledger.close()

    class Transport:
        def __init__(self, *_):
            self.response = b""

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, *_):
            pass

        def connect(self, path):
            assert path == "/run/jarvis-autonomy-broker/operator.sock"

        def sendall(self, raw):
            assert struct.unpack("!I", raw[:4])[0] == len(raw) - 4
            try:
                result = {"ok": True, "result": dispatch(raw[4:])}
            except Exception:
                result = {"ok": False}
            payload = canonical(result)
            self.response = struct.pack("!I", len(payload)) + payload

        def recv(self, size):
            part, self.response = self.response[:size], self.response[size:]
            return part

    # Patch only the relay's socket namespace, not test HTTP's event-loop sockets.
    from types import SimpleNamespace

    monkeypatch.setattr(
        autonomy_host,
        "socket",
        SimpleNamespace(
            socket=Transport,
            AF_UNIX=1,
            SOCK_STREAM=1,
        ),
    )
    monkeypatch.setattr(autonomy_api, "guardian_status", lambda: dict(state))
    active = []

    @asynccontextmanager
    async def lifespan(_):
        governor = Governor(tmp_path / "observer.sqlite3")
        active.append(governor)
        try:
            yield
            assert governor.state.threads() == []
        finally:
            active.pop().close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(
        autonomy_api.router(lambda: active[0], lambda value: value == "Bearer fixture")
    )
    headers = {"Authorization": "Bearer fixture"}

    def signed(action):
        body = {
            "version": 1,
            "action": action,
            "key_id": key_id,
            "generation": state["generation"],
            "request_id": str(uuid.uuid4()),
            "expires_at": int(time.time()) + 30,
        }
        body["signature"] = base64.b64encode(
            private.sign(
                b"jarvis-autonomy-control-v1\n" + canonical(body), ec.ECDSA(hashes.SHA256())
            )
        ).decode()
        return body

    try:
        with TestClient(app) as client:
            first = signed("stop")
            assert client.post("/api/autonomy/control", json=first).status_code == 401
            assert transitions == []
            assert (
                client.post("/api/autonomy/control", headers=headers, json=first).status_code == 200
            )
            assert client.get("/api/autonomy", headers=headers).json()["status"] == "STOPPED"
            assert transitions == ["STOPPED"]
            tampered = {**signed("resume"), "action": "stop"}
            assert (
                client.post("/api/autonomy/control", headers=headers, json=tampered).status_code
                == 403
            )
            assert transitions == ["STOPPED"]
            resume = signed("resume")
            assert (
                client.post("/api/autonomy/control", headers=headers, json=resume).status_code
                == 403
            )
            assert transitions == ["STOPPED"]  # No signed RESUME reaches the broker.
            assert client.get("/api/autonomy", headers=headers).json()["status"] == "STOPPED"
            assert (
                client.post(
                    "/api/autonomy/control", headers=headers, json=signed("stop")
                ).status_code
                == 200
            )
            assert (
                client.post("/api/autonomy/control", headers=headers, json=first).status_code == 200
            )
            assert transitions == ["STOPPED", "STOPPED"]  # Receipt replay, no duplicate effect.
            assert client.get("/api/autonomy", headers=headers).json()["status"] == "STOPPED"
            assert json.loads(canonical(state))["state"] == "STOPPED"
    finally:
        assert active == []
