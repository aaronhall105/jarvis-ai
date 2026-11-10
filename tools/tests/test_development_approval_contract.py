"""Cross-component HTTP/operator/admission contract with disposable keys and IPC doubles."""

import base64
import hashlib
import json
import os
import socket
import struct
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from developer_gateway import autonomy_api, autonomy_host
from tools import autonomy_broker_runtime
from tools.autonomy_broker import Ledger, canonical
from tools.autonomy_broker_ipc import handle
from tools.autonomy_broker_operator import OperatorDispatcher
from tools.supervised_development_broker import (
    DOMAIN,
    DevelopmentAdmissionBroker,
    serve_admission_connection,
)
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_pipeline import GovernorAdapter, SupervisedDevelopmentPipeline


def test_signed_development_http_roundtrip_and_android_number_regression(tmp_path, monkeypatch):
    # Ephemeral fixture identity only: no installed keys, services or approvals.
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    guardian = {"state": "STOPPED", "trusted": True, "generation": str(uuid.uuid4())}
    controller = SupervisedDevelopmentController(tmp_path / "controller.db")
    task = GovernorAdapter.from_candidate(
        {"candidate_id": 1, "status": "queued", "summary": "Fixture diagnostic"},
        owner_id="fixture",
        request_ref=str(uuid.uuid4()),
        source_sha="a" * 40,
        allowed_paths=("app/example.py",),
        guardian_generation=guardian["generation"],
        expires_at=int(time.time()) + 90,
    )
    task_id = SupervisedDevelopmentPipeline(controller, object(), object()).submit(task)
    published = controller.pending_owner_task()
    body = {k: v for k, v in published.items() if k not in {"goal", "request_ref"}}
    body.update(key_id=key_id, request_id=published["request_ref"])
    denied = []
    peer = {"operator": 1000}

    class Incoming:
        def __init__(self, data, uid):
            self.data, self.uid, self.output = data, uid, b""

        def getsockopt(self, *_):
            return struct.pack("3i", 123, self.uid, self.uid)

        def settimeout(self, *_):
            pass

        def recv(self, size):
            result, self.data = self.data[:size], self.data[size:]
            return result

        def sendall(self, data):
            self.output += data

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
            assert path in {
                "/run/jarvis-autonomy-broker/operator.sock",
                "/run/jarvis-autonomy-broker/development-admission.sock",
            }
            self.admission = path.endswith("development-admission.sock")

        def sendall(self, raw):
            incoming = Incoming(raw, 0 if self.admission else peer["operator"])
            ledger = Ledger(
                tmp_path / ("admission.db" if self.admission else "operator.db"), os.getuid()
            )
            try:
                if self.admission:
                    broker = DevelopmentAdmissionBroker(
                        ledger,
                        relay_uid=0,
                        worker_uid=995,
                        public_keys={key_id: public},
                        guardian=lambda: dict(guardian),
                    )
                    serve_admission_connection(incoming, broker)
                    result = json.loads(incoming.output[4:])
                    if "error" in result:
                        denied.append(result["error"])
                else:
                    dispatcher = OperatorDispatcher(
                        ledger,
                        relay_uid=1000,
                        autonomy_uid=995,
                        public_keys={key_id: public},
                        guardian=lambda: dict(guardian),
                        stop=lambda *_: None,
                        resume=lambda *_: None,
                        development_approve=autonomy_broker_runtime.forward_development_approval,
                    )
                    handle(incoming, dispatcher.dispatch)
                self.response = incoming.output
            finally:
                ledger.close()

        def recv(self, size):
            result, self.response = self.response[:size], self.response[size:]
            return result

    transport = SimpleNamespace(
        socket=Transport, AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM
    )
    monkeypatch.setattr(autonomy_host, "socket", transport)
    monkeypatch.setattr(autonomy_broker_runtime, "socket", transport)
    app = FastAPI()
    app.include_router(
        autonomy_api.router(lambda: object(), lambda value: value == "Bearer fixture")
    )

    def sign(fields):
        return {
            **fields,
            "signature": base64.b64encode(
                private.sign(DOMAIN + canonical(fields), ec.ECDSA(hashes.SHA256()))
            ).decode(),
        }

    headers = {"Authorization": "Bearer fixture"}
    with TestClient(app) as client:
        # Android signed Double.toString(1.0), then JSONObject serialized it as 1.
        assert body["max_cost_usd"] == 1.0
        old_android = sign(body)
        old_android["max_cost_usd"] = 1
        response = client.post("/api/autonomy/control", headers=headers, json=old_android)
        assert response.status_code == 403
        assert denied == ["development_approval_signature"]
        # Android's framework JSON escapes path separators before signing;
        # parsing and Python canonicalization remove them. It is not an expiry.
        fields = {**body, "max_cost_usd": 1}
        old_path_bytes = canonical(fields).replace(b"/", b"\\/")
        old_paths = {
            **fields,
            "signature": base64.b64encode(
                private.sign(DOMAIN + old_path_bytes, ec.ECDSA(hashes.SHA256()))
            ).decode(),
        }
        assert (
            client.post("/api/autonomy/control", headers=headers, json=old_paths).status_code == 403
        )
        assert denied[-1] == "development_approval_signature"
        corrected = sign({**body, "max_cost_usd": 1})
        assert client.post("/api/autonomy/control", json=corrected).status_code == 401
        assert (
            client.post("/api/autonomy/control", headers=headers, json=corrected).status_code == 200
        )
        assert (
            client.post("/api/autonomy/control", headers=headers, json=corrected).status_code == 200
        )
        tampered = {**corrected, "task_id": str(uuid.uuid4())}
        assert (
            client.post("/api/autonomy/control", headers=headers, json=tampered).status_code == 403
        )
        replay_conflict = sign({**body, "max_cost_usd": 1, "task_id": str(uuid.uuid4())})
        assert (
            client.post("/api/autonomy/control", headers=headers, json=replay_conflict).status_code
            == 403
        )
        assert denied[-1] == "development_approval_replay_conflict"
        expired = sign({**body, "expires_at": 1})
        assert (
            client.post("/api/autonomy/control", headers=headers, json=expired).status_code == 410
        )
        peer["operator"] = 995
        assert (
            client.post("/api/autonomy/control", headers=headers, json=corrected).status_code == 403
        )
    assert guardian["state"] == "STOPPED" and guardian["trusted"] is True
    assert controller.snapshot(task_id)["state"] == "APPROVAL_REQUESTED"
    ledger = Ledger(tmp_path / "admission.db", os.getuid())
    assert ledger.db.execute("SELECT status FROM development_admissions").fetchall() == [
        ("approved",)
    ]
    ledger.close()
    controller.db.close()


def test_android_development_canonical_vectors_match_actual_broker_serializer():
    resource = (
        Path(__file__).resolve().parents[2]
        / "android/jarvis-voice-client/app/src/test/resources/development_canonical_vectors.json"
    )
    for vector in json.loads(resource.read_text()):
        assert canonical(vector["fields"]).decode("ascii") == vector["canonical"]
